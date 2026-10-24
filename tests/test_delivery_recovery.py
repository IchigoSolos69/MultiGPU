"""Real CPU recovery and optional notebook history; no provider launches."""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from multigpu import imports
from multigpu.artifacts import load_checkpoint, write_checkpoint, verify_export
from multigpu.service import Service
from multigpu.worker import TrainingContext, run


class DeliveryRecoveryTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / ".test-runs"
        scratch.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.root = Path(self.temp.name)
        with patch("multigpu.service.hardware_info", return_value={"torch_available": False, "cuda_available": False, "gpus": []}):
            self.service = Service(self.root / "state")
        self.addCleanup(self.close)

    def close(self):
        self.service.shutdown()
        self.service.store.close()
        self.temp.cleanup()

    def guided(self, **extra):
        return self.service.create({"providers": ["colab"], "steps": 8, **extra})

    def partial(self, job=None, destination=None):
        job = job or self.guided()
        destination = destination or self.service.jobs_root / job["id"]
        original = TrainingContext.report
        def stop(ctx, *args, **kwargs):
            original(ctx, *args, **kwargs)
            if ctx.step == 2:
                (ctx.output_dir / "STOP").touch()
        with patch.object(TrainingContext, "report", stop), contextlib.redirect_stdout(io.StringIO()):
            result = run(job["spec"], destination)
        self.assertEqual((result["status"], result["step"]), ("paused", 2), result)
        if destination == self.service.jobs_root / job["id"]:
            job.update(status="paused", step=2, checkpoint={"step": 2, "verified": True}, remote_terminal=True)
            self.service.save_job(job)
        return job, destination

    def failed_delivery(self):
        job = self.guided()
        folder = self.service.jobs_root / job["id"]
        with patch.object(TrainingContext, "export", side_effect=OSError("Export disk unavailable")), contextlib.redirect_stdout(io.StringIO()):
            result = run(job["spec"], folder)
        self.assertEqual(result["status"], "error")
        job = self.service._ingest(job)
        job.update(status="error", remote_terminal=True)
        self.service.save_job(job)
        return job, folder

    def test_fresh_workspace_and_cleared_draft_are_not_restored(self):
        self.assertFalse(self.service.draft()["has_saved_draft"])
        self.assertFalse(self.service.draft()["has_saved_draft"])
        saved = self.service.save_draft({"spec": {"name": "My saved input"}, "wizard_step": 1})
        self.assertTrue(saved["has_saved_draft"])
        self.assertTrue(self.service.draft()["has_saved_draft"])
        self.guided()
        self.assertFalse(self.service.draft()["has_saved_draft"])

    def test_image_preflight_preparation_preserves_requested_device(self):
        for device in ("cpu", "cuda"):
            with self.subTest(device=device), patch("multigpu.datasets.inspect_dataset", return_value={
                    "errors": [], "warnings": [], "fingerprint": "fixture", "dataset": {"weights": {"ready": False}}}):
                reviewed = self.service.plan({"providers": ["local"], "recipe": "image", "device": device})
            action = next(action for action in reviewed["preflight"]["actions"] if action["type"] == "environment")
            self.assertEqual((action["profile"], action["device"]), ("image", device))

    def test_guided_continuation_is_reviewed_pinned_and_never_launches_compute(self):
        job, folder = self.partial()
        previous = job["spec"]["attempt_id"]
        checkpoint_bytes = (folder / "checkpoint.json").read_bytes()
        self.assertNotIn("resume", self.service.job(job["id"])["allowed_actions"])
        self.assertIn("continue-guided", self.service.job(job["id"])["allowed_actions"])
        with patch.object(self.service, "_start") as start, patch("multigpu.connectors.submit") as submit:
            reviewed = self.service.continue_guided(job["id"], {"preview": True})
            self.assertEqual((reviewed["provider_id"], reviewed["checkpoint"]["step"], reviewed["target"]["steps"]), ("colab", 2, 8))
            with self.assertRaisesRegex(ValueError, "Confirm"):
                self.service.continue_guided(job["id"], {"preview_digest": reviewed["preview_digest"]})
            committed = self.service.continue_guided(job["id"], {"preview_digest": reviewed["preview_digest"], "confirmed_stopped": True})
            self.assertEqual((committed["id"], committed["status"], committed["spec"]["steps"]), (job["id"], "awaiting_user", 8))
            self.assertNotEqual(committed["spec"]["attempt_id"], previous)
            self.assertEqual(committed["spec"]["resume_sha256"], hashlib.sha256(checkpoint_bytes).hexdigest())
            self.assertEqual((folder / "checkpoint.json").read_bytes(), checkpoint_bytes)
            self.assertEqual(committed["attempt_history"][-1]["attempt_id"], previous)
            start.assert_not_called()
            submit.assert_not_called()
            with self.assertRaisesRegex(ValueError, "stale"):
                self.service.continue_guided(job["id"], {"preview_digest": reviewed["preview_digest"], "confirmed_stopped": True})

    def test_changed_checkpoint_or_provider_configuration_invalidates_review(self):
        job, folder = self.partial()
        reviewed = self.service.continue_guided(job["id"], {"preview": True})
        state = load_checkpoint(folder / "checkpoint.json")
        state["loss"] += .01
        write_checkpoint(folder, state)
        with self.assertRaisesRegex(ValueError, "stale"):
            self.service.continue_guided(job["id"], {"preview_digest": reviewed["preview_digest"], "confirmed_stopped": True})
        reviewed = self.service.continue_guided(job["id"], {"preview": True})
        self.service.store.put("connection", "colab", {"provider_id": "colab", "config": {"notebook_url": "https://colab.research.google.com/example"}})
        with self.assertRaisesRegex(ValueError, "stale"):
            self.service.continue_guided(job["id"], {"preview_digest": reviewed["preview_digest"], "confirmed_stopped": True})

    def test_unresolved_or_active_execution_blocks_both_recovery_paths(self):
        job, folder = self.partial()
        job.update(provider_id="modal", execution_mode="automatic", submission_status="unknown", submission_intent={"attempt_id": "old"})
        self.service.save_job(job)
        with self.assertRaisesRegex(ValueError, "Reconcile"):
            self.service.continue_guided(job["id"], {"preview": True})
        self.assertNotIn("continue-guided", self.service.job(job["id"])["allowed_actions"])
        job.update(submission_status="prepared", status="running")
        self.service.save_job(job)
        for action in (self.service.continue_guided, self.service.finish_delivery):
            with self.assertRaisesRegex(ValueError, "Stop training"):
                action(job["id"], {"preview": True})

    def test_automatic_pause_exposes_selected_assisted_fallback_without_launch(self):
        job, folder = self.partial()
        job.update(provider_id="local", execution_mode="automatic")
        job["spec"]["providers"] = ["local", "colab"]
        job["attempted_providers"] = ["local"]
        self.service.save_job(job)
        (folder / "STOP").unlink()
        with patch.object(self.service, "_run") as launch:
            self.service._continue_to_next(job)
        launch.assert_not_called()
        saved = self.service.job(job["id"])
        self.assertEqual(saved["pending_handoff"]["provider_id"], "colab")
        self.assertEqual(saved["provider_id"], "local")
        reviewed = self.service.continue_guided(job["id"], {"preview": True})
        self.assertFalse(reviewed["requires_stopped_confirmation"])
        committed = self.service.continue_guided(job["id"], {"preview_digest": reviewed["preview_digest"]})
        self.assertEqual(committed["provider_id"], "colab")

    def test_failed_delivery_finishes_real_model_without_optimizer_updates(self):
        job, folder = self.failed_delivery()
        before = copy.deepcopy(load_checkpoint(folder / "checkpoint.json")["payload"])
        original_device = job["spec"]["device"]
        reviewed = self.service.finish_delivery(job["id"], {"preview": True})
        self.assertEqual(reviewed["plan"]["resolved_resource"]["device"], "cpu")
        self.assertEqual(reviewed["optimizer_updates"], 0)
        with patch.object(self.service, "_start") as start:
            committed = self.service.finish_delivery(job["id"], {"preview_digest": reviewed["preview_digest"], "confirmed_stopped": True})
            start.assert_called_once()
            with self.assertRaises(ValueError):
                self.service.finish_delivery(job["id"], {"preview_digest": reviewed["preview_digest"], "confirmed_stopped": True})
        self.assertEqual(committed["spec"]["device"], original_device)
        self.service._run(job["id"])
        completed = self.service.job(job["id"])
        self.assertEqual((completed["status"], completed["export_status"]), ("completed", "verified"), completed)
        self.assertEqual(load_checkpoint(folder / "checkpoint.json")["payload"], before)
        self.assertEqual(completed["spec"]["device"], original_device)
        verify_export(Path(completed["export_path"]))

    def test_delivery_pin_rejects_checkpoint_changed_after_commit(self):
        job, folder = self.failed_delivery()
        reviewed = self.service.finish_delivery(job["id"], {"preview": True})
        with patch.object(self.service, "_start"):
            self.service.finish_delivery(job["id"], {"preview_digest": reviewed["preview_digest"], "confirmed_stopped": True})
        state = load_checkpoint(folder / "checkpoint.json")
        state["payload"]["weights"][0] += .01
        write_checkpoint(folder, state)
        self.service._run(job["id"])
        self.assertEqual(self.service.job(job["id"])["status"], "error")
        self.assertFalse((folder / "export" / "manifest.json").exists())
        self.assertEqual(load_checkpoint(folder / "checkpoint.json")["payload"], state["payload"])

    def test_verified_model_delivery_is_not_recomputed(self):
        job = self.guided()
        folder = self.service.jobs_root / job["id"]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run(job["spec"], folder)["status"], "completed")
        job = self.service._ingest(job)
        job.update(status="completed", remote_terminal=True)
        self.service.save_job(job)
        with patch.object(self.service, "_start") as start:
            with self.assertRaisesRegex(ValueError, "Verified model files already exist"):
                self.service.finish_delivery(job["id"], {"preview": True})
        start.assert_not_called()

    def test_completed_import_reports_elapsed_and_successful_local_delivery(self):
        job = self.guided()
        remote = self.root / "remote"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run(job["spec"], remote)["status"], "completed")
        reported = {"type": "summary", "job_id": job["id"], "attempt_id": job["spec"]["attempt_id"],
                    "step": 8, "total_steps": 8, "timestamp": datetime.now(timezone.utc).isoformat(),
                    "elapsed_seconds": 42.5, "required_action": "Verify and save model files."}
        with (remote / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(reported) + "\n")
        result = self.service.import_output(job["id"], {"path": str(remote)})
        self.assertEqual(result["elapsed_seconds"], 42.5)
        self.assertEqual(result["export_status"], "verified")
        self.assertEqual(result["last_summary"]["required_action"], "Model files are verified and saved locally.")
        self.assertEqual(result["last_summary"]["summary_kind"], "import")

    def history_source(self):
        job = self.guided()
        job, remote = self.partial(job, self.root / "remote")
        stamp = datetime.now(timezone.utc).isoformat()
        extra = [{"type": "summary", "job_id": job["id"], "attempt_id": job["spec"]["attempt_id"], "step": 2, "total_steps": 8,
                  "timestamp": stamp, "loss": .2, "remaining_session_seconds": 300, "active_gpu_count": 0},
                 {"type": "completed", "job_id": job["id"], "step": 8, "total_steps": 8, "timestamp": stamp},
                 {"type": "metric", "job_id": "another-job", "step": 1, "total_steps": 8, "timestamp": stamp, "loss": .1}]
        with (remote / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write("\n".join(json.dumps(event) for event in extra) + "\n{malformed\n")
        return job, remote

    def test_imported_history_is_validated_deduped_and_never_replayed_as_live(self):
        job, remote = self.history_source()
        reviewed = self.service.import_output(job["id"], {"path": str(remote), "preview": True})
        self.assertTrue(reviewed["warnings"])
        imported = self.service.import_output(job["id"], {"path": str(remote), "preview_digest": reviewed["preview_digest"]})
        self.assertEqual((imported["status"], imported["observation_source"]), ("paused", "notebook_import"))
        detailed = self.service.job(job["id"], True)
        self.assertEqual(len(detailed["metrics"]), 2)
        self.assertTrue(all(event["job_id"] == job["id"] for event in detailed["events"]))
        self.assertNotIn("completed", [event["type"] for event in detailed["events"]])
        self.assertEqual(detailed["last_summary"]["summary_kind"], "import")
        self.assertTrue(detailed["checkpoint"]["verified_at"])
        first = (self.service.jobs_root / job["id"] / "imported-history.jsonl").read_bytes()
        reviewed = self.service.import_output(job["id"], {"path": str(remote), "preview": True})
        self.service.import_output(job["id"], {"path": str(remote), "preview_digest": reviewed["preview_digest"]})
        self.assertEqual((self.service.jobs_root / job["id"] / "imported-history.jsonl").read_bytes(), first)
        ingested = self.service._ingest(self.service.job(job["id"]))
        self.assertNotIn("last_worker_event_at", ingested)
        self.assertNotIn("worker_status", ingested)

    def test_oversize_or_unwritable_history_does_not_discard_checkpoint(self):
        job, remote = self.history_source()
        with (remote / "events.jsonl").open("rb") as stream:
            valid = stream.read()
        (remote / "events.jsonl").write_bytes(b"x" * (imports._HISTORY_BYTES + 20) + b"\n" + valid)
        reviewed = self.service.import_output(job["id"], {"path": str(remote), "preview": True})
        self.assertTrue(any("2 MiB" in warning for warning in reviewed["warnings"]))
        original = imports.atomic_bytes
        def fail_history(path, data):
            if Path(path).name == "imported-history.jsonl":
                raise OSError("No history space")
            return original(path, data)
        with patch.object(imports, "atomic_bytes", side_effect=fail_history):
            imported = self.service.import_output(job["id"], {"path": str(remote), "preview_digest": reviewed["preview_digest"]})
        self.assertEqual(imported["status"], "paused")
        self.assertIn("could not be saved", imported["imported_history_warning"])
        self.assertEqual(load_checkpoint(self.service.jobs_root / job["id"] / "checkpoint.json")["step"], 2)


if __name__ == "__main__":
    unittest.main()
