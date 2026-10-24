"""Explicit, observable dependency installation in the application's own environment."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import threading
import queue
import time
import uuid

from .store import now

PROFILES = {
    "cpu": (["torch==2.14.1+cpu"], "https://download.pytorch.org/whl/cpu"),
    "image": (["torch==2.14.1", "torchvision==0.29.1", "Pillow==12.3.0"], "https://download.pytorch.org/whl/cpu"),
    "cuda": (["torch==2.14.1+cu130", "torchvision==0.29.1+cu130", "Pillow==12.3.0"], "https://download.pytorch.org/whl/cu130"),
    "kaggle": (["kaggle>=2.2.3,<3"], None),
    "modal": (["modal>=1,<2"], None),
    "lightning": (["lightning-sdk"], None),
}


def resolve_preparation(profile, device, hardware):
    if profile not in PROFILES:
        raise ValueError("Choose cpu, image, cuda, kaggle, modal or lightning setup.")
    if device not in (None, "auto", "cpu", "cuda"):
        raise ValueError("Preparation device must be auto, cpu or cuda.")
    effective = profile
    resolved_device = None
    if profile == "image":
        requested = device or "auto"
        cuda = requested == "cuda" or requested == "auto" and bool(hardware.get("gpus") or hardware.get("cuda_available"))
        effective, resolved_device = ("cuda", "cuda") if cuda else ("image", "cpu")
    elif profile in {"cpu", "cuda"}:
        if device in {"cpu", "cuda"} and device != profile:
            raise ValueError("The preparation profile conflicts with the selected device. Choose matching CPU or CUDA preparation.")
        resolved_device = profile
    return {"profile": profile, "effective_profile": effective, "device": resolved_device,
            "requested_device": device or "auto"}


class EnvironmentSetup:
    def __init__(self, service):
        self.service = service
        self.lock = threading.Lock()
        self.tasks = {task["id"]: task for task in service.store.all("environment_task")}
        self.processes = {}
        self.cancellations = {}
        self.threads = {}
        self.service.environment = self
        for task in self.tasks.values():
            if task["status"] in ("queued", "running"):
                task.update(status="interrupted", cancellable=False, finished_at=now(), requires_repair=True,
                            error="Setup was interrupted by a controller restart. Run preparation again to repair and verify the environment.")
                service.store.put("environment_task", task["id"], task)

    def recent(self):
        with self.lock:
            return [dict(task) for task in sorted(self.tasks.values(), key=lambda t: t["created_at"], reverse=True)[:10]]

    def get(self, task_id):
        with self.lock:
            if task_id not in self.tasks:
                raise KeyError("Setup task not found.")
            return dict(self.tasks[task_id])

    def start(self, profile, device=None):
        preparation = resolve_preparation(profile, device, self.service.hardware)
        if sys.prefix == sys.base_prefix:
            raise ValueError("Start MultiGPU from its managed environment (start.cmd or .venv). Setup will not modify system or Codex Python.")
        with self.service.lock:
            if any(j["status"] in ("queued", "running", "pausing") or (j.get("submission_status") == "unknown" and not self.service._manually_resolved(j)) for j in self.service.jobs()):
                raise ValueError("Pause and resolve active training before changing its environment.")
            with self.lock:
                if any(t["status"] in ("queued", "running") for t in self.tasks.values()):
                    raise ValueError("An environment setup is already running.")
                task_id = uuid.uuid4().hex
                self.tasks[task_id] = {"id": task_id, **preparation, "status": "queued", "logs": [], "created_at": now(), "cancellable": True}
                self.cancellations[task_id] = threading.Event()
                self.service.store.put("environment_task", task_id, self.tasks[task_id])
            self.service.environment_busy = True
        thread = threading.Thread(target=self._run, args=(task_id, profile), daemon=True)
        self.threads[task_id] = thread
        thread.start()
        return self.get(task_id)

    def _update(self, task_id, **changes):
        with self.lock:
            self.tasks[task_id].update(changes)
            self.service.store.put("environment_task", task_id, self.tasks[task_id])

    def cancel(self, task_id):
        with self.lock:
            if task_id not in self.tasks:
                raise KeyError("Setup task not found.")
            task = self.tasks[task_id]
            if task["status"] not in ("queued", "running"):
                return dict(task)
            self.cancellations[task_id].set()
        self._update(task_id, cancellation_requested=True, message="Stopping the app-owned installer. Preparation may need repair.")
        return self.get(task_id)

    def _run(self, task_id, profile):
        preparation = self.tasks[task_id]
        effective_profile = preparation.get("effective_profile") or resolve_preparation(profile, preparation.get("requested_device"), self.service.hardware)["effective_profile"]
        packages, index = PROFILES[effective_profile]
        uv = os.environ.get("MULTIGPU_UV")
        command = [uv, "pip", "install", "--python", sys.executable, *packages] if uv and Path(uv).is_file() else [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", *packages]
        if index:
            command += ["--index-url", index]
        logs = []
        cancelled = self.cancellations.setdefault(task_id, threading.Event())
        tree = None
        try:
            self._update(task_id, status="running", command=command)
            if cancelled.is_set():
                raise InterruptedError("Setup cancelled before installation started.")
            env = {**os.environ, "PYTHONUNBUFFERED": "1"}
            from .process_control import OwnedProcessTree
            with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
                                  creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0) | 0x4) if os.name == "nt" else 0,
                                  start_new_session=os.name != "nt") as process:
                tree = OwnedProcessTree(process)
                try:
                    self.processes[task_id] = tree
                    output = queue.Queue(maxsize=300)
                    def read_output():
                        try:
                            for line in process.stdout:
                                try:
                                    output.put_nowait(line.rstrip())
                                except queue.Full:
                                    pass
                        except (OSError, ValueError):
                            pass
                    reader = threading.Thread(target=read_output, daemon=True)
                    reader.start()
                    started = time.monotonic()
                    while process.poll() is None or not output.empty():
                        if cancelled.is_set() or self.service.closing.is_set():
                            tree.terminate()
                            raise InterruptedError("Setup cancelled. Run preparation again to repair and verify the environment.")
                        if time.monotonic() - started > 3600:
                            tree.terminate()
                            raise TimeoutError("Installation exceeded one hour. Review the log and retry preparation.")
                        try:
                            from .connectors import redact_provider_output
                            logs.append(redact_provider_output(output.get(timeout=.2)))
                            logs[:] = logs[-150:]
                            self._update(task_id, logs=logs[:])
                        except queue.Empty:
                            pass
                    reader.join(timeout=1)
                    while not output.empty():
                        from .connectors import redact_provider_output
                        logs.append(redact_provider_output(output.get_nowait()))
                    self._update(task_id, logs=logs[-150:])
                    if process.wait(timeout=10):
                        raise RuntimeError("Dependency installation failed. Review the setup log and retry.")
                finally:
                    # Popen.__exit__ waits; release the owned tree before it can
                    # wait on a hung installer after logging/storage failures.
                    tree.close()
            if cancelled.is_set() or self.service.closing.is_set():
                raise InterruptedError("Setup cancelled. Run preparation again to verify the environment.")
            if profile in ("image", "cuda"):
                # Preparation runs locally before any account-owned compute is allocated.
                from .recipes import prepare_image_weights
                self._update(task_id, logs=logs[-150:] + ["Preparing and verifying official ResNet-18 weights…"])
                info = prepare_image_weights(self.service.root / "cache" / "models", allow_download=True,
                                             cancelled=lambda: cancelled.is_set() or self.service.closing.is_set())
                self.service.store.put("environment", "image_weights", info)
            importlib.invalidate_caches()
            from .service import hardware_info
            self.service.hardware = hardware_info()
            if cancelled.is_set() or self.service.closing.is_set():
                raise InterruptedError("Setup cancelled. Run preparation again to verify the environment.")
            self._update(task_id, status="completed", finished_at=now(), hardware=self.service.hardware, cancellable=False, requires_repair=False)
        except Exception as exc:
            from .connectors import redact_provider_output
            self._update(task_id, status="cancelled" if isinstance(exc, InterruptedError) else "error", error=redact_provider_output(str(exc)), finished_at=now(), cancellable=False, requires_repair=True)
        finally:
            try:
                if tree:
                    tree.close()
            finally:
                self.processes.pop(task_id, None)
                with self.service.lock:
                    self.service.environment_busy = False

    def shutdown(self):
        for event in list(self.cancellations.values()):
            event.set()
        for tree in list(self.processes.values()):
            tree.terminate()
        for thread in list(self.threads.values()):
            thread.join(timeout=5)
