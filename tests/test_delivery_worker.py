import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from multigpu.artifacts import verify_export
from multigpu.worker import TrainingContext, run


class DeliveryWorkerTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / ".test-runs"
        scratch.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.root = Path(self.temp.name)
        self.spec = {"id": "delivery-test", "recipe": "starter", "recipe_version": 2,
                     "steps": 8, "device": "cpu", "runtime_seconds": 30}

    def tearDown(self):
        self.temp.cleanup()

    def train(self, spec, folder):
        with contextlib.redirect_stdout(io.StringIO()):
            return run(spec, folder)

    def test_short_runs_emit_terminal_summary_before_terminal_event(self):
        for label, extra, expected in (("completed", {}, "completed"),
                                       ("paused", {"runtime_seconds": 0}, "paused")):
            folder = self.root / label
            result = self.train({**self.spec, **extra}, folder)
            self.assertEqual(result["status"], expected)
            events = [json.loads(line) for line in (folder / "events.jsonl").read_text().splitlines()]
            self.assertEqual(events[-1]["type"], expected)
            self.assertEqual(events[-2]["summary_kind"], "terminal")
            self.assertEqual(events[-2]["worker_status"], expected)
            self.assertTrue(events[-2]["required_action"])

    def test_finish_export_uses_full_checkpoint_without_training_updates(self):
        folder = self.root / "failed-export"
        with patch.object(TrainingContext, "export", side_effect=OSError("disk unavailable")):
            self.assertEqual(self.train(self.spec, folder)["status"], "error")
        checkpoint = folder / "checkpoint.json"
        spec = {**self.spec, "delivery_only": True, "resume_from": str(checkpoint),
                "resume_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest()}
        with patch.object(TrainingContext, "report", side_effect=AssertionError("optimizer ran again")):
            result = self.train(spec, folder)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["step"], self.spec["steps"])
        self.assertEqual(verify_export(folder / "export")["metadata"]["step"], self.spec["steps"])

    def test_delivery_rejects_partial_or_changed_checkpoint(self):
        folder = self.root / "partial"
        self.train({**self.spec, "runtime_seconds": 0}, folder)
        checkpoint = folder / "checkpoint.json"
        result = self.train({**self.spec, "delivery_only": True, "resume_from": str(checkpoint)}, folder)
        self.assertEqual(result["status"], "error")
        self.assertIn("complete training target", result["error"])
        result = self.train({**self.spec, "resume_from": str(checkpoint), "resume_sha256": "0" * 64}, folder)
        self.assertEqual(result["status"], "error")
        self.assertIn("reviewed checkpoint changed", result["error"])

    def test_recovery_copy_failure_pauses_with_local_checkpoint(self):
        from multigpu.notebook_runtime import RecoveryPersistenceError
        folder = self.root / "recovery-failure"
        with patch("multigpu.worker.mirror_checkpoint", side_effect=RecoveryPersistenceError("recovery storage full")):
            result = self.train({**self.spec, "recovery_dir": str(self.root / "chosen-drive")}, folder)
        self.assertEqual(result["status"], "paused")
        self.assertEqual(result["step"], 0)
        self.assertTrue(Path(result["checkpoint_path"]).is_file())
        self.assertFalse((folder / "export").exists())
        events = [json.loads(line) for line in (folder / "events.jsonl").read_text().splitlines()]
        self.assertTrue(any(event["type"] == "recovery_warning" for event in events))
        self.assertIn("Recovery storage", events[-2]["required_action"])


if __name__ == "__main__":
    unittest.main()
