"""Portable runner. Custom scripts implement train(ctx) with report/checkpoint/stop/export hooks."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import runpy
import sys
import threading
import time
import uuid

from .artifacts import atomic_json, canonical_json, directory, load_checkpoint, write_checkpoint, export_artifacts, _check_path
from .datasets import prepare_spec, load_dataset, fingerprint_for_spec
from . import recipes


def _file_lock(handle, unlock=False):
    handle.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK if unlock else msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle, fcntl.LOCK_UN if unlock else fcntl.LOCK_EX | fcntl.LOCK_NB)


def worker_active(output_dir: Path) -> bool:
    """Probe the kernel lock; stale identity files and reused PIDs do not imply liveness."""
    path = Path(output_dir) / "worker.lock"
    if not path.exists():
        return False
    try:
        _check_path(path.absolute())
        with path.open("r+b") as handle:
            try:
                _file_lock(handle)
            except OSError:
                return True
            _file_lock(handle, unlock=True)
        return False
    except (OSError, ValueError):
        return True


class WorkerLock:
    def __init__(self, output_dir: Path, spec: dict):
        self.output_dir = directory(output_dir)
        path = self.output_dir / "worker.lock"
        _check_path(path)
        self.handle = path.open("a+b")
        self.handle.seek(0, 2)
        if self.handle.tell() == 0:
            self.handle.write(b"0")
            self.handle.flush()
        try:
            _file_lock(self.handle)
        except OSError:
            self.handle.close()
            raise RuntimeError("A worker already owns this run's output folder; stop or reconnect before resuming") from None
        self.identity = {"pid": os.getpid(), "attempt_id": spec.get("attempt_id"), "job_id": spec.get("id"),
                         "started_at": datetime.now(timezone.utc).isoformat(), "process_token": uuid.uuid4().hex, "active": True}
        try:
            atomic_json(self.output_dir / "worker-identity.json", self.identity)
        except Exception:
            self.close()
            raise

    def close(self):
        if self.handle.closed:
            return
        try:
            atomic_json(self.output_dir / "worker-identity.json", {**self.identity, "active": False})
        except (OSError, ValueError):
            pass
        self.handle.close()


class TrainingContext:
    def __init__(self, spec: dict, output_dir: Path, resumed: dict | None = None):
        self.spec = prepare_spec(spec)
        self.output_dir = directory(output_dir)
        self.device = "cpu" if self.spec["recipe"] == "starter" else self.spec["device"]
        self.step = resumed.get("step", 0) if resumed else 0
        self.loss = resumed.get("loss") if resumed else None
        self.resume_state = resumed.get("payload") if resumed else None
        self._elapsed_before = resumed.get("elapsed_seconds", 0.0) if resumed else 0.0
        self._started = time.monotonic()
        self._last_checkpoint = self._last_summary = self._last_heartbeat = self._started
        self._state_factory = None
        self._checkpoint_step = None
        self._fingerprint = ""
        self.checkpoint_path = self.export_path = None
        self.evaluation = None
        self.phase = "validating_inputs"
        self._writer = threading.RLock()
        self._stop_telemetry = threading.Event()
        self._telemetry = None

    @property
    def elapsed_seconds(self):
        return self._elapsed_before + time.monotonic() - self._started

    def emit(self, kind: str, **extra) -> dict:
        with self._writer:
            event = {"type": kind, "job_id": self.spec.get("id"), "attempt_id": self.spec.get("attempt_id"),
                     "timestamp": datetime.now(timezone.utc).isoformat(), "step": self.step,
                     "total_steps": self.spec["steps"], "loss": self.loss, "phase": self.phase,
                     "elapsed_seconds": round(self.elapsed_seconds, 6), "device": self.device, **extra}
            line = canonical_json(event).decode("utf-8")
            path = self.output_dir / "events.jsonl"
            _check_path(path)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()
            print(line, flush=True)
            return event

    def _tick(self):
        with self._writer:
            now = time.monotonic()
            if now - self._last_heartbeat >= self.spec["heartbeat_interval_seconds"]:
                self.emit("heartbeat", checkpoint_step=self._checkpoint_step)
                self._last_heartbeat = now
            if now - self._last_summary >= self.spec["summary_interval_seconds"]:
                self.emit("summary", checkpoint_step=self._checkpoint_step)
                self._last_summary = now

    def start_telemetry(self):
        def update():
            interval = min(1.0, self.spec["heartbeat_interval_seconds"], self.spec["summary_interval_seconds"])
            while not self._stop_telemetry.wait(interval):
                try:
                    self._tick()
                except (OSError, ValueError):
                    self._stop_telemetry.set()
        if self._telemetry is None:
            self._telemetry = threading.Thread(target=update, daemon=True, name="worker-heartbeat")
            self._telemetry.start()

    def close(self):
        self._stop_telemetry.set()
        if self._telemetry:
            self._telemetry.join(timeout=2)

    def report(self, loss: float, step: int | None = None, **metrics):
        import math
        with self._writer:
            next_step = self.step + 1 if step is None else step
            if type(next_step) is not int or not self.step <= next_step <= self.spec["steps"]:
                raise ValueError("Reported step must be monotonic and within total steps")
            if not isinstance(loss, (int, float)) or not math.isfinite(loss):
                raise ValueError("Training produced a non-finite loss")
            if "device" in metrics:
                self.device = str(metrics.pop("device"))
            self.step, self.loss = next_step, float(loss)
            self.emit("metric", metrics=metrics)
            self._tick()

    def should_stop(self):
        runtime = self.spec["runtime_seconds"]
        return (self.output_dir / "STOP").exists() or time.monotonic() - self._started >= runtime - min(5.0, runtime * .05)

    def checkpoint(self, state: dict) -> Path:
        if not isinstance(state, dict):
            raise TypeError("Checkpoint state must be a JSON object")
        with self._writer:
            envelope = {"job_id": self.spec.get("id"), "recipe": self.spec["recipe"], "recipe_version": self.spec["recipe_version"],
                        "fingerprint": self._fingerprint, "step": self.step, "total_steps": self.spec["steps"],
                        "loss": self.loss, "elapsed_seconds": self.elapsed_seconds, "device": self.device, "payload": state}
            self.checkpoint_path = write_checkpoint(self.output_dir, envelope)
            self._checkpoint_step = self.step
            self._last_checkpoint = time.monotonic()
            self.emit("checkpoint", checkpoint_path=str(self.checkpoint_path))
            return self.checkpoint_path

    def maybe_checkpoint(self):
        if self._state_factory and time.monotonic() - self._last_checkpoint >= self.spec["checkpoint_interval_seconds"]:
            self.checkpoint(self._state_factory())

    def export(self, files: dict[str, bytes | str | Path]) -> Path:
        self.phase = "exporting"
        self.export_path = export_artifacts(files, self.output_dir / "export", {
            "job_id": self.spec.get("id"), "name": self.spec.get("name", "Training run"),
            "recipe": self.spec["recipe"], "recipe_version": self.spec["recipe_version"],
            "fingerprint": self._fingerprint, "step": self.step, "loss": self.loss,
            "device": self.device, "evaluation": self.evaluation})
        return self.export_path


def run(spec: dict, output_dir: Path) -> dict:
    ctx, ownership = None, None
    try:
        ownership = WorkerLock(output_dir, spec)
        validated = prepare_spec(spec)
        resumed = load_checkpoint(Path(validated["resume_from"])) if validated.get("resume_from") else None
        ctx = TrainingContext(validated, output_dir, resumed)
        ctx.start_telemetry()
        if validated["recipe"] == "image" and resumed and not Path(validated.get("weights_path") or "").is_file():
            asset = Path(validated["resume_from"]).parent / "assets" / "base_weights.pth"
            if asset.is_file():
                validated["weights_path"] = str(asset)
                validated["weights_sha256"] = resumed.get("payload", {}).get("weights_sha256")
        data = load_dataset(validated)
        if validated["recipe"] == "image":
            validated["steps"] = data["metadata"]["total_steps"]
            ctx.spec["steps"] = validated["steps"]
        ctx._fingerprint = data["fingerprint"]
        if validated.get("fingerprint") and validated["fingerprint"] != ctx._fingerprint:
            raise ValueError("Inputs changed after preflight; preview this training plan again")
        if resumed and (resumed.get("fingerprint") != ctx._fingerprint or resumed.get("recipe") != validated["recipe"]):
            raise ValueError("Resume checkpoint does not match this recipe, dataset, or training configuration")
        if resumed and (type(ctx.step) is not int or not 0 <= ctx.step <= validated["steps"]):
            raise ValueError("Resume checkpoint has an invalid step")
        ctx.spec = validated
        ctx.phase = "training"
        if validated["recipe"] == "starter":
            recipes.train_starter(ctx, data)
        elif validated["recipe"] == "pytorch":
            recipes.train_pytorch(ctx, data)
        elif validated["recipe"] == "image":
            recipes.train_image(ctx, data)
        else:
            path = Path(validated["script_path"])
            sys.path.insert(0, str(path.absolute().parent))
            try:
                train = runpy.run_path(str(path)).get("train")
                if not callable(train):
                    raise ValueError("Custom Python script must define train(ctx)")
                train(ctx)
            finally:
                sys.path.pop(0)
            if ctx.step < validated["steps"] and not ctx.should_stop():
                raise ValueError("Custom train(ctx) returned before total steps without a stop request")
            if ctx._checkpoint_step != ctx.step:
                raise ValueError("Custom script must checkpoint its current reported step before returning or pausing")
        status = "completed" if ctx.step == validated["steps"] else "paused"
        if status == "completed" and ctx.export_path is None:
            raise ValueError("Training completed without exported model artifacts")
        result = {"status": status, "step": ctx.step, "total_steps": validated["steps"], "loss": ctx.loss,
                  "device": ctx.device, "elapsed_seconds": ctx.elapsed_seconds, "evaluation": ctx.evaluation,
                  "checkpoint_path": str(ctx.checkpoint_path) if ctx.checkpoint_path else None,
                  "export_path": str(ctx.export_path) if ctx.export_path else None}
        ctx.close()
        ctx.emit(status, **result)
        return result
    except Exception as exc:
        result = {"status": "error", "error": str(exc), "step": ctx.step if ctx else 0,
                  "total_steps": spec.get("steps"), "loss": ctx.loss if ctx else None,
                  "device": ctx.device if ctx else spec.get("device", "cpu"), "elapsed_seconds": ctx.elapsed_seconds if ctx else 0.0}
        if ctx:
            ctx.close()
            try:
                ctx.emit("error", **result)
            except (OSError, ValueError):
                print(json.dumps({"type": "error", **result}), flush=True)
        elif ownership is not None:
            fallback = TrainingContext({"steps": spec.get("steps", 1), "recipe": "starter", "device": "cpu",
                                        "id": spec.get("id"), "attempt_id": spec.get("attempt_id")}, output_dir)
            fallback.emit("error", **result)
        else:
            # A rejected second worker must not append events to its owner's run.
            print(json.dumps({"type": "error", **result}), flush=True)
        return result
    finally:
        if ctx:
            ctx.close()
        if ownership:
            ownership.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Train or resume a portable MultiGPU recipe")
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        spec = json.loads(args.spec.read_text(encoding="utf-8-sig"))
        if not isinstance(spec, dict):
            raise ValueError("Job spec must be a JSON object")
        return 1 if run(spec, args.output)["status"] == "error" else 0
    except (OSError, ValueError) as exc:
        print(json.dumps({"type": "error", "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
