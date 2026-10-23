from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import time
import subprocess
import sys
import unittest
from unittest.mock import patch, MagicMock

from multigpu.environment import EnvironmentSetup
from multigpu.store import Store


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        temporary_root = Path(__file__).resolve().parents[1] / ".test-runs"
        temporary_root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=temporary_root)
        self.service = SimpleNamespace(lock=threading.RLock(), jobs=lambda: [], hardware={}, root=Path(self.temp.name), environment_busy=False,
                                       store=Store(self.temp.name), closing=threading.Event(), _manually_resolved=lambda j: False)
        self.setup = EnvironmentSetup(self.service)

    def tearDown(self):
        self.service.store.close()
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
        process.poll.return_value = 0
        with patch("multigpu.environment.subprocess.Popen", return_value=process) as launch, patch("multigpu.process_control.OwnedProcessTree"), patch("multigpu.service.hardware_info", return_value={"torch_available":True}), patch.dict("os.environ", {"MULTIGPU_UV":""}):
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

    def test_cancel_silent_owned_installer_releases_reservation(self):
        real_popen = subprocess.Popen
        def silent_installer(command, **kwargs):
            return real_popen([sys.executable, "-u", "-c", "import time; time.sleep(60)"], **kwargs)
        with patch("multigpu.environment.subprocess.Popen", side_effect=silent_installer), patch.dict("os.environ", {"MULTIGPU_UV": ""}):
            task = self.setup.start("cpu")
            deadline = time.monotonic() + 10
            while task["id"] not in self.setup.processes and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertIn(task["id"], self.setup.processes)
            self.setup.cancel(task["id"])
            self.setup.threads[task["id"]].join(timeout=10)
        self.assertEqual(self.setup.get(task["id"])["status"], "cancelled")
        self.assertTrue(self.setup.get(task["id"])["requires_repair"])
        self.assertFalse(self.service.environment_busy)
        self.assertFalse(self.setup.threads[task["id"]].is_alive())

    def test_log_storage_failure_terminates_live_installer_before_waiting(self):
        real_popen = subprocess.Popen
        children = []
        def installer(command, **kwargs):
            child = real_popen([sys.executable, "-u", "-c", "import time; print('Installing', flush=True); time.sleep(60)"], **kwargs)
            children.append(child)
            return child
        update = self.setup._update
        def failing_log(task_id, **changes):
            if "logs" in changes:
                raise OSError("Disk full while retaining setup log")
            return update(task_id, **changes)
        with patch("multigpu.environment.subprocess.Popen", side_effect=installer), patch.object(self.setup, "_update", side_effect=failing_log), patch.dict("os.environ", {"MULTIGPU_UV": ""}):
            task = self.setup.start("cpu")
            self.setup.threads[task["id"]].join(timeout=10)
        self.assertFalse(self.setup.threads[task["id"]].is_alive())
        self.assertIsNotNone(children[0].poll())
        self.assertEqual(self.setup.get(task["id"])["status"], "error")
        self.assertFalse(self.service.environment_busy)


if __name__ == "__main__":
    unittest.main()
