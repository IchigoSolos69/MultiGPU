from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from unittest.mock import patch, MagicMock

from multigpu.environment import EnvironmentSetup


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = SimpleNamespace(lock=threading.RLock(), jobs=lambda: [], hardware={}, root=Path(self.temp.name), environment_busy=False)
        self.setup = EnvironmentSetup(self.service)

    def tearDown(self):
        self.temp.cleanup()

    def test_system_interpreter_is_not_modified(self):
        with patch("multigpu.environment.sys.prefix", "system"), patch("multigpu.environment.sys.base_prefix", "system"):
            with self.assertRaisesRegex(ValueError, "will not modify"):
                self.setup.start("cpu")

    def test_active_or_unknown_training_blocks_install(self):
        with patch("multigpu.environment.sys.prefix", "managed"), patch("multigpu.environment.sys.base_prefix", "base"):
            for job in ({"status":"running"}, {"status":"interrupted", "submission_status":"unknown"}):
                self.service.jobs = lambda: [job]
                with self.assertRaisesRegex(ValueError, "resolve"):
                    self.setup.start("image")

    def test_only_one_setup_runs_and_training_reservation_is_visible(self):
        with patch("multigpu.environment.sys.prefix", "managed"), patch("multigpu.environment.sys.base_prefix", "base"), patch("multigpu.environment.threading.Thread"):
            task = self.setup.start("cpu")
            self.assertTrue(self.service.environment_busy)
            self.assertEqual(task["status"], "queued")
            with self.assertRaisesRegex(ValueError, "already"):
                self.setup.start("modal")

    def test_pinned_cpu_install_reports_progress_and_releases_reservation(self):
        self.setup.tasks["test"] = {"id":"test", "status":"queued"}
        self.service.environment_busy = True
        process = MagicMock()
        process.__enter__.return_value = process
        process.stdout = iter(["Installing CPU training\n", "Done\n"])
        process.wait.return_value = 0
        with patch("multigpu.environment.subprocess.Popen", return_value=process) as launch, patch("multigpu.service.hardware_info", return_value={"torch_available":True}), patch.dict("os.environ", {"MULTIGPU_UV":""}):
            self.setup._run("test", "cpu")
        self.assertEqual(self.setup.get("test")["status"], "completed")
        self.assertIn("torch==2.14.1+cpu", launch.call_args.args[0])
        self.assertFalse(self.service.environment_busy)
        self.assertEqual(len(self.setup.get("test")["logs"]), 2)

    def test_failed_install_preserves_actionable_error_and_releases(self):
        self.setup.tasks["test"] = {"id":"test", "status":"queued"}
        self.service.environment_busy = True
        with patch("multigpu.environment.subprocess.Popen", side_effect=OSError("network unavailable")):
            self.setup._run("test", "modal")
        self.assertEqual(self.setup.get("test")["status"], "error")
        self.assertIn("network", self.setup.get("test")["error"])
        self.assertFalse(self.service.environment_busy)


if __name__ == "__main__":
    unittest.main()
