"""Measured attempt costs. Missing observations stay unknown; no cloud profiling."""
from contextlib import contextmanager
from datetime import datetime, timezone
import math
import platform
import time


STAGES = ("preparation", "staging", "upload", "provider_startup", "data_loading",
          "training", "checkpointing", "evaluation", "export", "collection", "local_export")


class Performance:
    def __init__(self, spec):
        self.started = time.perf_counter()
        self.phase_started = self.started
        self.phase = "preparation"
        self.stages = {name: None for name in STAGES}
        self.samples = 0
        self.start_step = 0
        self.checkpoints = 0
        self.spec = spec
        self.hardware = {"cpu": platform.processor() or platform.machine(), "gpu": None}

    def transition(self, phase):
        now = time.perf_counter()
        self.stages[self.phase] = (self.stages[self.phase] or 0) + now - self.phase_started
        self.phase, self.phase_started = phase, now

    @contextmanager
    def measure(self, stage):
        previous = self.phase
        self.transition(stage)
        try:
            yield
        finally:
            self.transition(previous)

    def snapshot(self, step, device, torch=None):
        durations = dict(self.stages)
        durations[self.phase] = (durations[self.phase] or 0) + time.perf_counter() - self.phase_started
        memory = {"cpu_peak_bytes": None, "gpu_peak_allocated_bytes": None, "gpu_utilization_percent": None}
        try:
            if platform.system() != "Windows":
                import resource
                memory["cpu_peak_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if platform.system() == "Darwin" else 1024)
            else:
                import ctypes
                from ctypes import wintypes
                class Counters(ctypes.Structure):
                    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [(key, ctypes.c_size_t) for key in
                        ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
                         "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
                counters = Counters()
                counters.cb = ctypes.sizeof(counters)
                kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel.GetCurrentProcess.restype = wintypes.HANDLE
                query = ctypes.WinDLL("psapi").GetProcessMemoryInfo
                query.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
                if query(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
                    memory["cpu_peak_bytes"] = counters.PeakWorkingSetSize
            if torch is not None and str(device).startswith("cuda"):
                peak = torch.cuda.max_memory_allocated(device)
                memory["gpu_peak_allocated_bytes"] = peak if type(peak) is int else None
                name = torch.cuda.get_device_name(device)
                self.hardware["gpu"] = name if isinstance(name, str) else None
        except (OSError, RuntimeError, ValueError):
            pass
        training = durations.get("training") or 0
        return {"format": "multigpu-performance-v1", "source": "worker", "observed_at": datetime.now(timezone.utc).isoformat(),
                "attempt_id": self.spec.get("attempt_id"), "recipe": self.spec.get("recipe"),
                "recipe_version": self.spec.get("recipe_version"), "device": device, "hardware": self.hardware,
                "runtime": {"python": platform.python_version(), "torch": str(getattr(torch, "__version__", "unknown")) if torch is not None else None},
                "stages_seconds": {key: round(value, 6) if value is not None else None for key, value in durations.items()},
                "attempt_seconds": round(time.perf_counter() - self.started, 6), "trained_samples": self.samples,
                "completed_steps": max(0, step - self.start_step), "recovered_steps": self.start_step,
                "repeated_steps": self.spec.get("observed_repeated_steps"), "repeated_steps_confidence": "observed_lower_bound" if self.spec.get("observed_repeated_steps") is not None else "unknown",
                "samples_per_second": self.samples / training if training > 0 and self.samples else None,
                "checkpoint_count": self.checkpoints, "memory": memory, "transferred_bytes": None,
                "metric_interval_seconds": 1}


def recommendations(spec, decisions, jobs, fingerprint, hardware, connections=None):
    """Comparable completed attempts only; never change order or entitlement."""
    estimates = []
    candidates = list(decisions)
    for decision in decisions:
        resource = decision.get("resolved_resource") or {}
        if decision["provider_id"] == "local" and decision.get("eligible") and resource.get("device") == "cuda" and spec.get("device") == "auto":
            candidates.append({**decision, "resolved_resource": {"provider_id": "local", "device": "cpu", "gpu": None, "gpu_index": None}})
    for decision in candidates:
        pid = decision["provider_id"]
        resource = decision.get("resolved_resource") or {}
        device = resource.get("device")
        compatible = []
        for job in jobs:
            p = job.get("performance") or {}
            old = job.get("spec") or {}
            if (job.get("status") != "completed" or job.get("export_status") != "verified" or job.get("provider_id") != pid
                    or job.get("fingerprint") != fingerprint or p.get("recipe") != spec["recipe"]
                    or old.get("image_feature_cache", False) != spec.get("image_feature_cache", False)
                    or old.get("image_loader", "sync") != spec.get("image_loader", "sync")
                    or p.get("source") != "worker" or p.get("completed_steps", 0) <= 0):
                continue
            if "submission_upload_and_startup" in p.get("controller_stages_seconds", {}):
                continue  # SDK stage overlap is unknown; do not present it as an additive ETA.
            if device == "cpu" and p.get("device") != "cpu" or device == "cuda" and not str(p.get("device")).startswith("cuda"):
                continue
            if pid == "local":
                if device == "cuda":
                    index = resource.get("gpu_index") or 0
                    gpus = hardware.get("gpus", [])
                    if (p.get("device") != f"cuda:{index}" or index >= len(gpus)
                            or p.get("hardware", {}).get("gpu") != gpus[index].get("name")):
                        continue
                elif p.get("hardware", {}).get("cpu") != (platform.processor() or platform.machine()):
                    continue
                if p.get("runtime", {}).get("python") != platform.python_version():
                    continue
                if spec["recipe"] != "starter" and p.get("runtime", {}).get("torch") != hardware.get("torch_version"):
                    continue
            else:
                if old.get("resolved_resource") != resource or device == "cuda" and not p.get("hardware", {}).get("gpu"):
                    continue
            compatible.append(job)
        latest = max(compatible, key=lambda j: j.get("updated_at", ""), default=None)
        estimate = {"provider_id": pid, "device": device, "resource": resource, "eligible": decision.get("eligible", False), "estimated_seconds": None,
                    "source": "unknown", "evidence_job_id": None, "allowance": "See the current dated provider quota; unknown limits stay unknown."}
        connection = (connections or {}).get(pid, {})
        quota = connection.get("quota") or {}
        hours = quota.get("remaining_hours")
        allowance_seconds = hours * 3600 if type(hours) in (int, float) and math.isfinite(hours) and hours >= 0 else None
        estimate.update(allowance_observed_at=connection.get("checked_at"), remaining_allowance_seconds=allowance_seconds,
                        allowance_confidence=quota.get("confidence", "not_metered" if pid == "local" else "unknown"))
        if latest:
            p = latest["performance"]
            stages = p.get("stages_seconds", {})
            overhead = sum(value for key, value in stages.items() if key != "training" and isinstance(value, (int, float)))
            controller = p.get("controller_stages_seconds", {})
            overhead += sum(value for value in controller.values() if isinstance(value, (int, float)))
            training = stages.get("training") or 0
            total = training / p["completed_steps"] * spec["steps"] + overhead
            if math.isfinite(total) and total > 0:
                estimate.update(estimated_seconds=round(total, 3), estimated_overhead_seconds=round(overhead, 3),
                                source="compatible_completed_attempt", evidence_job_id=latest["id"], observed_at=p.get("observed_at"))
                session = min(spec["runtime_seconds"], allowance_seconds) if allowance_seconds is not None else spec["runtime_seconds"]
                estimate["estimated_useful_training_seconds"] = round(max(0, session - overhead), 3)
        estimates.append(estimate)
    known = [e for e in estimates if e["eligible"] and e["estimated_seconds"] is not None]
    best = min(known, key=lambda e: e["estimated_seconds"], default=None)
    return {"estimates": estimates, "recommended_provider_id": best["provider_id"] if best else None,
            "recommended_resource": best["resource"] if best else None,
            "automatic_reordering": False, "message": "Estimates include observed overhead. Accepting a suggestion requires a fresh preview; provider limits still apply." if best else
            "No comparable completed measurements yet. Keep your selected order; no background benchmark will run."}


def continuation_advice(job, checkpoint, plan, jobs, current_eligible):
    p = job.get("performance") or {}
    count = p.get("completed_steps", 0)
    training = p.get("stages_seconds", {}).get("training")
    remaining = None
    if p.get("source") == "worker" and type(count) is int and count > 0 and type(training) in (int, float) and training > 0:
        remaining = training / count * (checkpoint["total_steps"] - checkpoint["step"])
    migration = None
    target = plan.get("selected_provider")
    if target != job.get("provider_id"):
        candidates = [old for old in jobs if old.get("provider_id") == target and old.get("fingerprint") == job.get("fingerprint")
                      and old.get("status") == "completed" and old.get("export_status") == "verified"
                      and old.get("performance", {}).get("source") == "worker"
                      and old.get("spec", {}).get("resolved_resource") == plan.get("resolved_resource")]
        latest = max(candidates, key=lambda j: j.get("updated_at", ""), default=None)
        if latest:
            observed = latest["performance"]
            stages = observed.get("stages_seconds", {})
            controller = observed.get("controller_stages_seconds", {})
            costs = [stages.get("preparation"), stages.get("data_loading"),
                     *(controller.get(key) for key in ("preparation", "staging", "provider_startup", "submission_upload_and_startup"))]
            migration = sum(value for value in costs if type(value) in (int, float) and value >= 0)
    stay = bool(current_eligible and remaining is not None and migration is not None and migration > remaining)
    return {"current_resource_eligible": current_eligible, "estimated_current_remaining_seconds": remaining,
            "estimated_migration_overhead_seconds": migration, "stay_recommended": stay,
            "message": "Consider resuming on the current resource: observed migration overhead exceeds its estimated remaining training time. Your reviewed choice is unchanged." if stay else
            "Switching costs are estimates. Unknown provider startup, transfer or allowance prevents a reliable speedup claim; your reviewed choice is unchanged."}
