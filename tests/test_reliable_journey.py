"""Real local delivery and safe mocked-provider recovery across journey boundaries."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch

from multigpu import connectors
from multigpu.onboarding import provider_setup
from multigpu.prediction import inference_capability, predict
from multigpu.service import Service
from multigpu.worker import run
from multigpu.environment import EnvironmentSetup


class ReliableJourneyTests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[1] / ".test-runs"
        parent.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=parent)
        self.root = Path(self.temp.name)
        self.hardware = patch("multigpu.service.hardware_info", return_value={"torch_available": False, "cuda_available": False, "gpus": []})
        self.hardware.start()
        self.service = Service(self.root / "state")

    def tearDown(self):
        self.service.shutdown()
        self.service.store.close()
        self.hardware.stop()
        self.temp.cleanup()

    def guided(self, **extra):
        return self.service.create({"providers": ["colab"], "steps": 20, **extra})

    def uncertain(self):
        job = self.guided()
        job["spec"]["attempt_id"] = "saved-attempt"
        job.update(provider_id="kaggle", execution_mode="automatic", status="interrupted", submission_status="unknown",
                   submission_intent={"provider": "kaggle", "attempt_id": "saved-attempt", "kernel_id": "owner/saved-attempt"})
        self.service.save_job(job)
        return self.service.job(job["id"])

    def test_creation_uses_exact_reviewed_mode_and_provider(self):
        raw = {"providers": ["colab", "local"], "steps": 20}
        plan = self.service.plan(raw)
        self.assertEqual((plan["selected_provider"], plan["execution_mode"]), ("local", "automatic"))
        with patch.object(self.service, "_start") as start:
            job = self.service.create({**raw, "preview_digest": plan["preview_digest"]}, require_preview=True)
        self.assertEqual((job["provider_id"], job["execution_mode"]), ("local", "automatic"))
        start.assert_called_once()
        raw["guided"] = True
        plan = self.service.plan(raw)
        with patch.object(self.service, "_start") as start:
            job = self.service.create({**raw, "preview_digest": plan["preview_digest"]}, require_preview=True)
        self.assertEqual((job["provider_id"], job["execution_mode"]), ("colab", "guided"))
        start.assert_not_called()

    def test_preflight_blocks_every_preparation_action(self):
        data = self.root / "bad.csv"
        data.write_text("x,label\n1,2\n")
        plan = self.service.plan({"providers": ["colab"], "dataset_path": str(data)})
        self.assertFalse(plan["preparation_eligible"])
        self.assertEqual(plan["allowed_actions"], [])
        self.assertTrue(plan["preflight"]["actions"])
        self.assertFalse(any(d["preparation_eligible"] for d in plan["decisions"]))

    def test_exhausted_allowance_does_not_erase_account_verification(self):
        guide = provider_setup("kaggle", {"ready": False, "account_verified": True, "detail": "Quota exhausted"}, {})
        verify = next(step for step in guide["steps"] if step["id"] == "verify")
        self.assertTrue(verify["ready"])
        self.assertTrue(verify["can_complete"])

    def test_unknown_attempt_requires_current_token_and_explicit_confirmation(self):
        job = self.uncertain()
        self.assertNotIn("archive", job["allowed_actions"])
        self.assertIn("resolve-submission", job["allowed_actions"])
        self.assertTrue(job["attention_required"])
        with self.assertRaisesRegex(ValueError, "Confirm"):
            self.service.resolve_submission(job["id"], {"recovery_token": job["recovery_token"], "outcome": "absent"})
        with self.assertRaisesRegex(ValueError, "changed"):
            self.service.resolve_submission(job["id"], {"recovery_token": "stale", "outcome": "absent", "confirmed": True})
        with patch.object(connectors, "submit") as submit:
            resolved = self.service.resolve_submission(job["id"], {"recovery_token": job["recovery_token"], "outcome": "absent", "confirmed": True})
        submit.assert_not_called()
        self.assertEqual(resolved["submission_status"], "unknown")
        self.assertEqual(resolved["submission_resolution"]["source"], "user_confirmed")
        self.assertIn("archive", resolved["allowed_actions"])
        self.assertNotIn("resolve-submission", resolved["allowed_actions"])
        self.service.shutdown()
        self.service.store.close()
        with patch.object(connectors, "reconcile") as reconnect:
            self.service = Service(self.root / "state")
            reconnect.assert_not_called()
        self.assertTrue(self.service._manually_resolved(self.service.job(job["id"])))

    def test_attach_failure_preserves_reservation_and_evidence(self):
        job = self.uncertain()
        with patch.object(connectors, "attach", side_effect=connectors.ConnectorError("Cannot verify identity", code="attach_unverified")):
            with self.assertRaises(connectors.ConnectorError):
                self.service.resolve_submission(job["id"], {"recovery_token": job["recovery_token"], "outcome": "attach", "execution_id": "wrong"})
        self.assertNotIn("archive", self.service.job(job["id"])["allowed_actions"])
        self.assertIn("attach existing execution", self.service.diagnostic_logs()["logs"])

    def test_legacy_unknown_can_be_explicitly_resolved_without_inventing_provider_id(self):
        job = self.uncertain()
        job["spec"].pop("attempt_id")
        self.service.save_job(job)
        current = self.service.job(job["id"])
        result = self.service.resolve_submission(job["id"], {"recovery_token": current["recovery_token"], "outcome": "stopped", "confirmed": True})
        self.assertEqual(result["submission_resolution"]["identity_source"], "local_recovery")
        self.assertEqual(result["submission_intent"], job["submission_intent"])
        self.assertTrue(self.service._manually_resolved(result))

    def test_notebook_assets_cannot_bypass_paid_gpu_or_development_admission(self):
        job = self.guided()
        job["spec"].update(providers=["colab", "deepnote", "fastero"], device="cuda", recipe="pytorch")
        self.service.save_job(job)
        with patch.object(connectors, "build_bundle") as build:
            for provider in ("deepnote", "fastero"):
                with self.assertRaises(ValueError):
                    self.service.bundle(job["id"], provider)
            build.assert_not_called()

    def test_proven_submission_failure_can_retry_without_resolution(self):
        job = self.guided()
        job.update(provider_id="kaggle", execution_mode="automatic", status="queued")
        job["spec"].update(providers=["kaggle"], guided=False)
        self.service.store.put("connection", "kaggle", {"provider_id": "kaggle", "ready": True,
                              "config": {"username": "test"}, "quota": {"remaining_hours": 1}})
        job["spec"]["resolved_resource"] = self.service._resource_preview(job, ["kaggle"])["resolved_resource"]
        self.service.save_job(job)
        intent = {"provider": "kaggle", "attempt_id": "f" * 32}
        error = connectors.ConnectorError("Quota changed before launch", code="quota", intent=intent,
                                         submission_outcome="not_started", submission_evidence={"stage": "preflight"})
        with patch.object(connectors, "prepare", return_value=intent), patch.object(connectors, "submit", side_effect=error):
            self.service._run(job["id"])
        result = self.service.job(job["id"])
        self.assertEqual(result["submission_status"], "not_started")
        self.assertEqual(result["submission_evidence"]["stage"], "preflight")
        self.assertIn("resume", result["allowed_actions"])
        self.assertNotIn("resolve-submission", result["allowed_actions"])
        with patch.object(self.service, "_start"):
            resumed = self.service.resume(job["id"], ["local"])
        self.assertEqual(resumed["provider_id"], "local")

    def test_lightning_no_training_does_not_release_unstopped_studio(self):
        job = self.guided()
        job["spec"]["attempt_id"] = "studio-attempt"
        job.update(provider_id="lightning", execution_mode="automatic", status="error", submission_status="not_started", cleanup_status="failed")
        self.service.save_job(job)
        result = self.service.job(job["id"])
        self.assertNotIn("resume", result["allowed_actions"])
        self.assertNotIn("archive", result["allowed_actions"])
        with self.assertRaisesRegex(ValueError, "Studio"):
            self.service.resolve_submission(job["id"], {"recovery_token": result["recovery_token"], "outcome": "absent", "confirmed": True})
        resolved = self.service.resolve_submission(job["id"], {"recovery_token": result["recovery_token"], "outcome": "stopped", "confirmed": True})
        self.assertEqual(resolved["cleanup_status"], "user_confirmed_stopped")
        self.assertIn("archive", resolved["allowed_actions"])

    def test_complete_export_import_and_prediction_do_not_need_original_csv(self):
        csv = self.root / "original.csv"
        csv.write_text("x,y,label\n" + "\n".join(f"{i},{i % 3},{i % 2}" for i in range(20)))
        job = self.guided(dataset_path=str(csv))
        remote = self.root / "remote"
        with contextlib.redirect_stdout(io.StringIO()):
            run(job["spec"], remote)
        moved = self.root / "moved.csv"
        csv.rename(moved)
        preview = self.service.import_output(job["id"], {"path": str(remote), "preview": True})
        result = self.service.import_output(job["id"], {"path": str(remote), "preview_digest": preview["preview_digest"]})
        self.assertEqual(result["export_status"], "verified")
        self.assertEqual(result["inference"]["features"], ["x", "y"])
        self.assertIn(self.service.predict(job["id"], {"features": {"x": 1, "y": 2}})["prediction"], [0, 1])
        relinked = self.service.relocate_inputs(job["id"], {"dataset_path": str(moved)})
        self.assertEqual(relinked["fingerprint"], job["fingerprint"])
        bad = self.root / "changed.csv"
        bad.write_text(moved.read_text().replace("0,0,0", "99,0,0", 1))
        with self.assertRaisesRegex(ValueError, "differ"):
            self.service.relocate_inputs(job["id"], {"dataset_path": str(bad)})
        self.assertEqual(self.service.job(job["id"])["spec"]["dataset_path"], str(moved))

    def test_scalar_custom_model_has_no_prediction_action(self):
        from multigpu.artifacts import export_artifacts
        job = self.guided()
        folder = self.root / "custom-export"
        export_artifacts({"model.json": "7"}, folder, {})
        job.update(status="completed", export_path=str(folder))
        self.service.save_job(job)
        self.assertFalse(inference_capability(folder)["supported"])
        self.assertNotIn("predict", self.service.job(job["id"])["allowed_actions"])
        with self.assertRaisesRegex(ValueError, "own inference instructions"):
            predict(folder, {"features": []})

    def test_import_commits_only_the_checkpoint_bytes_it_validated(self):
        from multigpu.artifacts import decode_checkpoint, load_checkpoint
        job = self.guided()
        remote = self.root / "synced-results"
        with contextlib.redirect_stdout(io.StringIO()):
            run({**job["spec"], "runtime_seconds": 0}, remote)
        source = remote / "checkpoint.json"
        original = source.read_bytes()
        def replace_after_validation(contents):
            state = decode_checkpoint(contents)
            source.write_bytes(b"An external sync replaced this file")
            return state
        with patch("multigpu.imports.decode_checkpoint", side_effect=replace_after_validation):
            imported = self.service.import_output(job["id"], {"path": str(remote)})
        local = self.service.jobs_root / job["id"] / "checkpoint.json"
        self.assertEqual(local.read_bytes(), original)
        self.assertEqual(load_checkpoint(local)["step"], imported["step"])

    def test_setup_tasks_survive_reload_and_controller_restart(self):
        setup = EnvironmentSetup(self.service)
        with patch("multigpu.environment.sys.prefix", "managed"), patch("multigpu.environment.sys.base_prefix", "base"), patch("multigpu.environment.threading.Thread"):
            task = setup.start("cpu")
        self.assertEqual(self.service.bootstrap()["environment_tasks"][0]["id"], task["id"])
        setup.threads.clear()
        self.service.shutdown()
        self.service.store.close()
        self.service = Service(self.root / "state")
        setup = EnvironmentSetup(self.service)
        restored = setup.get(task["id"])
        self.assertEqual(restored["status"], "interrupted")
        self.assertTrue(restored["requires_repair"])

    def test_collection_and_export_errors_retain_redacted_diagnostics(self):
        job = self.uncertain()
        job.update(handle={"provider": "kaggle"}, submission_status="confirmed")
        self.service.save_job(job)
        error = connectors.ConnectorError("Forbidden", code="permission", diagnostic={"stderr": "Permission denied\nKAGGLE_KEY=private-token"})
        with patch.object(connectors, "collect", side_effect=error):
            self.service._finish_remote(job["id"], "completed")
        with patch.object(self.service, "export", side_effect=OSError("Disk full")):
            self.service._auto_export(job["id"])
        log = self.service.diagnostic_logs()["logs"]
        self.assertIn("result collection", log)
        self.assertIn("local export", log)
        self.assertIn("Permission denied", log)
        self.assertNotIn("private-token", log)

    def test_new_mutation_routes_remain_token_guarded(self):
        from multigpu.server import make_server
        server = make_server(self.service, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = "http://127.0.0.1:" + str(server.server_port)
        try:
            with urllib.request.urlopen(base + "/api/bootstrap") as response:
                token = response.headers["X-MultiGPU-Token"]
                self.assertIn("environment_tasks", json.load(response))
            with patch("multigpu.desktop.provider_signin", return_value={"started": True}) as signin:
                for route in ("/api/desktop/provider-signin", "/api/environment/tasks/unknown/cancel", "/api/jobs/abc/resolve-submission", "/api/jobs/abc/relocate-inputs", "/api/jobs/abc/continue-guided", "/api/jobs/abc/finish-delivery"):
                    request = urllib.request.Request(base + route, data=b'{}', headers={"Content-Type": "application/json"})
                    with self.assertRaises(urllib.error.HTTPError) as result:
                        urllib.request.urlopen(request)
                    self.assertEqual(result.exception.code, 403)
                signin.assert_not_called()
                request = urllib.request.Request(base + "/api/desktop/provider-signin", data=b'{"provider_id":"kaggle"}',
                                                 headers={"Content-Type": "application/json", "X-MultiGPU-Token": token})
                with urllib.request.urlopen(request) as response:
                    self.assertEqual(response.status, 202)
                signin.assert_called_once_with("kaggle")
            with urllib.request.urlopen(base + "/api/environment/tasks") as response:
                self.assertEqual(json.load(response), {"tasks": []})
            job = self.guided()
            with patch.object(self.service, "export", side_effect=OSError("Disk full at selected export folder")):
                request = urllib.request.Request(base + "/api/jobs/" + job["id"] + "/export", data=b'{}',
                                                 headers={"Content-Type": "application/json", "X-MultiGPU-Token": token})
                with self.assertRaises(urllib.error.HTTPError):
                    urllib.request.urlopen(request)
            self.assertIn("local export", self.service.logs(job["id"]))
            self.assertIn("Disk full", self.service.logs(job["id"]))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
