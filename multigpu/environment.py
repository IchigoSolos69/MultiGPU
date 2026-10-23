"""Explicit, observable dependency installation in the application's own environment."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import threading
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


class EnvironmentSetup:
    def __init__(self, service):
        self.service = service
        self.lock = threading.Lock()
        self.tasks = {}

    def get(self, task_id):
        with self.lock:
            if task_id not in self.tasks:
                raise KeyError("Setup task not found.")
            return dict(self.tasks[task_id])

    def start(self, profile):
        if profile not in PROFILES:
            raise ValueError("Choose cpu, image, cuda, kaggle, modal or lightning setup.")
        if sys.prefix == sys.base_prefix:
            raise ValueError("Start MultiGPU from its managed environment (start.cmd or .venv). Setup will not modify system or Codex Python.")
        with self.service.lock:
            if any(j["status"] in ("queued", "running", "pausing") or j.get("submission_status") == "unknown" for j in self.service.jobs()):
                raise ValueError("Pause and resolve active training before changing its environment.")
            with self.lock:
                if any(t["status"] in ("queued", "running") for t in self.tasks.values()):
                    raise ValueError("An environment setup is already running.")
                task_id = uuid.uuid4().hex
                self.tasks[task_id] = {"id": task_id, "profile": profile, "status": "queued", "logs": [], "created_at": now()}
            self.service.environment_busy = True
        threading.Thread(target=self._run, args=(task_id, profile), daemon=True).start()
        return self.get(task_id)

    def _update(self, task_id, **changes):
        with self.lock:
            self.tasks[task_id].update(changes)

    def _run(self, task_id, profile):
        packages, index = PROFILES[profile]
        if profile == "image" and self.service.hardware.get("cuda_available"):
            packages, index = PROFILES["cuda"]
        uv = os.environ.get("MULTIGPU_UV")
        command = [uv, "pip", "install", "--python", sys.executable, *packages] if uv and Path(uv).is_file() else [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", *packages]
        if index:
            command += ["--index-url", index]
        logs = []
        self._update(task_id, status="running", command=command)
        try:
            env = {**os.environ, "PYTHONUNBUFFERED": "1"}
            with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as process:
                for line in process.stdout:
                    logs.append(line.rstrip())
                    self._update(task_id, logs=logs[-150:])
                if process.wait():
                    raise RuntimeError("Dependency installation failed. Review the setup log and retry.")
            if profile in ("image", "cuda"):
                # Preparation runs locally before any account-owned compute is allocated.
                from .recipes import prepare_image_weights
                self._update(task_id, logs=logs[-150:] + ["Preparing and verifying official ResNet-18 weights…"])
                info = prepare_image_weights(self.service.root / "cache" / "models", allow_download=True)
                self.service.store.put("environment", "image_weights", info)
            importlib.invalidate_caches()
            from .service import hardware_info
            self.service.hardware = hardware_info()
            self._update(task_id, status="completed", finished_at=now(), hardware=self.service.hardware)
        except Exception as exc:
            self._update(task_id, status="error", error=str(exc), finished_at=now())
        finally:
            with self.service.lock:
                self.service.environment_busy = False
