import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from multigpu.process_control import OwnedProcessTree


def alive(pid):
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    api.OpenProcess.restype = ctypes.c_void_p
    api.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    api.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = api.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        result = ctypes.c_uint32()
        return bool(api.GetExitCodeProcess(handle, ctypes.byref(result))) and result.value == 259
    finally:
        api.CloseHandle(handle)


class ProcessControlTests(unittest.TestCase):
    def launch(self, code):
        return subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE,
                                text=True, creationflags=(0x4 | 0x08000000) if os.name == "nt" else 0,
                                start_new_session=os.name != "nt")

    def test_owned_tree_terminates_child_and_is_idempotent(self):
        process = self.launch("import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(90)']); print(p.pid,flush=True); time.sleep(90)")
        tree = OwnedProcessTree(process)
        try:
            child = int(process.stdout.readline())
            self.assertTrue(alive(child))
            tree.terminate()
            process.wait(timeout=5)
            deadline = time.monotonic() + 5
            while alive(child) and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertFalse(alive(child))
        finally:
            tree.close()
            tree.close()
            process.stdout.close()

    @unittest.skipUnless(os.name == "nt", "Windows Job Object crash guarantee")
    def test_controller_crash_closes_job_and_kills_children(self):
        root = Path(__file__).resolve().parents[1] / ".test-runs"
        root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            info = Path(temporary) / "pids.json"
            target = "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(90)']); print(p.pid,flush=True); time.sleep(90)"
            supervisor = ("import subprocess,sys,json,os; from pathlib import Path; from multigpu.process_control import OwnedProcessTree; "
                          f"p=subprocess.Popen([sys.executable,'-c',{target!r}],stdout=subprocess.PIPE,text=True,creationflags=0x08000004); "
                          f"tree=OwnedProcessTree(p); child=int(p.stdout.readline()); Path({str(info)!r}).write_text(json.dumps([p.pid,child])); os._exit(0)")
            result = subprocess.run([sys.executable, "-c", supervisor], timeout=15, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            pids = json.loads(info.read_text())
            deadline = time.monotonic() + 5
            while any(alive(pid) for pid in pids) and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertFalse(any(alive(pid) for pid in pids))

    @unittest.skipUnless(os.name == "nt", "Windows suspended startup contract")
    def test_running_process_is_rejected_and_stopped(self):
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(90)"], creationflags=0x08000000)
        with self.assertRaises(RuntimeError):
            OwnedProcessTree(process)
        process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
