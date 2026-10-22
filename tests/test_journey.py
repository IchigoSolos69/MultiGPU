import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from multigpu.artifacts import IntegrityError, load_checkpoint, write_checkpoint, verify_export
from multigpu.service import Service
from multigpu.worker import run


class JourneyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        with patch("multigpu.service.hardware_info", return_value={"torch_available": False, "cuda_available": False, "gpus": []}):
            self.service = Service(self.root / "state")

    def tearDown(self):
        self.service.shutdown()
        self.service.store.close()
        self.temp.cleanup()

    def guided(self, **spec):
        return self.service.create({"providers": ["colab"], "steps": 20, **spec})

    def train(self, job, folder, **changes):
        with contextlib.redirect_stdout(io.StringIO()):
            return run({**job["spec"], **changes}, folder)

    def test_preview_is_bound_to_current_spending_and_inputs(self):
        spec = {"steps": 20}
        plan = self.service.plan(spec)
        with patch.object(self.service, "_start"):
            with self.assertRaisesRegex(ValueError, "plan changed"):
                self.service.create({**spec, "steps": 21, "preview_digest": plan["preview_digest"]}, require_preview=True)
            with self.assertRaisesRegex(ValueError, "Preview"):
                self.service.create(spec, require_preview=True)
            job = self.service.create({**spec, "preview_digest": plan["preview_digest"]}, require_preview=True)
        self.assertEqual(job["spec"]["steps"], 20)

    def test_draft_never_restores_execution_confirmations(self):
        self.service.save_draft({"draft": {"wizard_step": 2, "spec": {"name": "My images", "free_only": False, "trust_script": True, "providers": ["modal"]}}})
        draft = self.service.draft()
        self.assertEqual(draft["wizard_step"], 2)
        self.assertEqual(draft["spec"]["name"], "My images")
        self.assertTrue(draft["spec"]["free_only"])
        self.assertFalse(draft["spec"]["trust_script"])

    def test_invalid_data_is_rejected_before_compute(self):
        data = self.root / "bad.csv"
        data.write_text("value,label\n1,5\n2,0\n")
        with patch.object(self.service, "_start") as start:
            with self.assertRaises(ValueError):
                self.service.create({"dataset_path": str(data)})
            start.assert_not_called()

    def test_guided_mode_survives_partial_import_and_rebundling(self):
        job = self.guided()
        remote = self.root / "remote"
        self.train(job, remote, runtime_seconds=0)
        proposed = self.service.import_output(job["id"], {"path": str(remote), "preview": True})
        self.assertEqual(self.service.job(job["id"])["status"], "awaiting_user")
        imported = self.service.import_output(job["id"], {"path": str(remote), "preview_digest": proposed["preview_digest"]})
        self.assertEqual(imported["execution_mode"], "guided")
        self.assertEqual(imported["status"], "paused")
        self.assertIn("bundle", imported["allowed_actions"])
        self.assertTrue(self.service.bundle(job["id"]).is_file())

    def test_stale_import_preview_and_rollback_are_rejected(self):
        job = self.guided()
        remote = self.root / "remote"
        self.train(job, remote, runtime_seconds=0)
        proposed = self.service.import_output(job["id"], {"path": str(remote), "preview": True})
        current = self.service.jobs_root / job["id"]
        state = load_checkpoint(remote / "checkpoint.json")
        state["step"] = 2
        write_checkpoint(current, state)
        with self.assertRaisesRegex(IntegrityError, "stale"):
            self.service.import_output(job["id"], {"path": str(remote), "preview_digest": proposed["preview_digest"]})
        with self.assertRaisesRegex(IntegrityError, "backwards"):
            self.service.import_output(job["id"], {"path": str(remote)})
        self.assertEqual(load_checkpoint(current / "checkpoint.json")["step"], 2)

    def test_export_retry_can_choose_a_new_folder_and_archive_is_reversible(self):
        job = self.guided()
        self.train(job, self.service.jobs_root / job["id"])
        job.update(status="completed")
        self.service.save_job(job)
        done = self.service.export(job["id"], str(self.root / "another-drive"))
        verify_export(Path(done["export_path"]))
        result = self.service.predict(job["id"], {"features": [0, 0, 0, 0]})
        self.assertIn(result["prediction"], [0, 1])
        self.assertTrue(self.service.archive(job["id"], True)["archived"])
        self.assertFalse(self.service.archive(job["id"], False)["archived"])

    def test_previous_checkpoint_can_recover_a_corrupt_latest(self):
        job = self.guided()
        path = self.service.jobs_root / job["id"]
        self.train(job, path, runtime_seconds=0)
        state = load_checkpoint(path / "checkpoint.json")
        write_checkpoint(path, state)
        (path / "checkpoint.json").write_text("corrupt")
        restored = self.service.recover(job["id"], {"checkpoint": "previous"})
        self.assertEqual(restored["status"], "paused")
        self.assertEqual(load_checkpoint(path / "checkpoint.json")["step"], 0)

    def test_unknown_submission_blocks_duplicate_resume(self):
        job = self.guided()
        job.update(provider_id="modal", status="interrupted", execution_mode="automatic", submission_status="unknown", submission_intent={"provider": "modal"})
        self.service.save_job(job)
        with self.assertRaisesRegex(ValueError, "Reconcile"):
            self.service.resume(job["id"])

    def test_export_zip_omits_unlisted_files_and_rejects_tampering(self):
        import zipfile
        job = self.guided()
        path = self.service.jobs_root / job["id"]
        self.train(job, path)
        job.update(status="completed")
        self.service.save_job(job)
        done = self.service.export(job["id"])
        export = Path(done["export_path"])
        (export / "unlisted-secret.txt").write_text("not a model artifact")
        with zipfile.ZipFile(self.service.artifact_zip(job["id"])) as archive:
            self.assertNotIn("unlisted-secret.txt", archive.namelist())
        (export / "model.json").write_text("tampered")
        with self.assertRaises(IntegrityError):
            self.service.artifact_zip(job["id"])


if __name__ == "__main__":
    unittest.main()
