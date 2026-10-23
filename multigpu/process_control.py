"""Own an installer process tree without touching unrelated user processes.

Windows callers must create Popen with CREATE_SUSPENDED (0x4). Ownership is
established before its initial thread resumes. POSIX callers use start_new_session.
"""
import ctypes
import os
import signal
import threading


class OwnedProcessTree:
    def __init__(self, process):
        self.process = process
        self._job = None
        self._closed = False
        self._lock = threading.RLock()
        try:
            if os.name == "nt":
                self._own_windows()
            elif os.getpgid(process.pid) != process.pid:
                raise RuntimeError("Installer must start in its own process session.")
        except Exception:
            self.close()
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            raise

    def _own_windows(self):
        from ctypes import wintypes as w

        class BasicLimits(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                        ("flags", w.DWORD), ("min_working", ctypes.c_size_t),
                        ("max_working", ctypes.c_size_t), ("active_processes", w.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in
                        ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("basic", BasicLimits), ("io", IoCounters),
                        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                        ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]

        class ThreadEntry(ctypes.Structure):
            _fields_ = [("size", w.DWORD), ("usage", w.DWORD), ("thread_id", w.DWORD),
                        ("owner_pid", w.DWORD), ("base_priority", w.LONG),
                        ("delta_priority", w.LONG), ("flags", w.DWORD)]

        api = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([w.LPVOID, w.LPCWSTR], w.HANDLE),
            "SetInformationJobObject": ([w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD], w.BOOL),
            "AssignProcessToJobObject": ([w.HANDLE, w.HANDLE], w.BOOL),
            "TerminateJobObject": ([w.HANDLE, w.UINT], w.BOOL),
            "CloseHandle": ([w.HANDLE], w.BOOL),
            "CreateToolhelp32Snapshot": ([w.DWORD, w.DWORD], w.HANDLE),
            "Thread32First": ([w.HANDLE, ctypes.POINTER(ThreadEntry)], w.BOOL),
            "Thread32Next": ([w.HANDLE, ctypes.POINTER(ThreadEntry)], w.BOOL),
            "OpenThread": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            "ResumeThread": ([w.HANDLE], w.DWORD),
        }
        for name, (args, result) in signatures.items():
            getattr(api, name).argtypes = args
            getattr(api, name).restype = result
        self._api = api
        self._job = api.CreateJobObjectW(None, None)
        if not self._job:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE; no breakaway.
        if not api.SetInformationJobObject(self._job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not api.AssignProcessToJobObject(self._job, int(self.process._handle)):
            raise RuntimeError("Windows could not safely own the installer process tree.")
        snapshot = api.CreateToolhelp32Snapshot(0x4, 0)  # TH32CS_SNAPTHREAD
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry = ThreadEntry(size=ctypes.sizeof(ThreadEntry))
            found = api.Thread32First(snapshot, ctypes.byref(entry))
            thread_ids = []
            while found:
                if entry.owner_pid == self.process.pid:
                    thread_ids.append(entry.thread_id)
                found = api.Thread32Next(snapshot, ctypes.byref(entry))
            if len(thread_ids) != 1:
                raise RuntimeError("Installer did not start with one suspended initial thread.")
            thread = api.OpenThread(0x2, False, thread_ids[0])  # THREAD_SUSPEND_RESUME
            if not thread:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                if api.ResumeThread(thread) != 1:
                    raise RuntimeError("Installer must be created suspended before process-tree ownership.")
            finally:
                api.CloseHandle(thread)
        finally:
            api.CloseHandle(snapshot)

    def terminate(self):
        with self._lock:
            if self._closed:
                return
            if os.name == "nt":
                if self._job and not self._api.TerminateJobObject(self._job, 1):
                    raise ctypes.WinError(ctypes.get_last_error())
            else:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def close(self):
        with self._lock:
            if self._closed:
                return
            if os.name == "nt":
                if self._job:
                    self._api.CloseHandle(self._job)
                    self._job = None
            else:
                try:
                    if os.getpgid(self.process.pid) == self.process.pid:
                        self.terminate()
                except ProcessLookupError:
                    pass
            self._closed = True

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
