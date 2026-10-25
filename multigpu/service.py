"""Job lifecycle, local processes and optional account-owned connectors."""
import importlib.util
import hashlib
import hmac
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from collections import deque
from datetime import datetime, timezone

from .catalog import CATALOG
from . import connectors
from .planner import RECIPES, preview, validate_spec
from .store import Store, now

TERMINAL = {"completed", "paused", "error", "awaiting_user", "interrupted"}
CONFIG_KEYS = {"username", "user", "start_studio", "dedicated_studio", "stop_studio_after_run", "studio", "teamspace", "workspace", "environment", "organization", "project", "region", "notebook_url", "server_url", "allow_custom_scripts", "entitlement_confirmed", "budget_confirmed", "native_spend_limit", "gpu", "machine", "studio_name", "teamspace_name", "owner", "name"}


def hardware_info():
    result = {"python": sys.executable, "torch_available": importlib.util.find_spec("torch") is not None,
              "torchvision_available": importlib.util.find_spec("torchvision") is not None,
              "pillow_available": importlib.util.find_spec("PIL") is not None,
              "cuda_available": False, "gpus": [], "cpu_count": os.cpu_count(), "checked_at": now()}
    try:
        probe = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=8)
        if probe.returncode == 0:
            for line in probe.stdout.strip().splitlines():
                name, memory = line.rsplit(",", 1)
                result["gpus"].append({"name": name.strip(), "memory_mb": int(memory.strip())})
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    if result["torch_available"]:
        try:
            code = "import json,torch; available=torch.cuda.is_available();print(json.dumps({'cuda_available':available,'torch_version':torch.__version__,'visible_gpus':[{'name':torch.cuda.get_device_name(i),'memory_mb':torch.cuda.get_device_properties(i).total_memory//1048576,'index':i} for i in range(torch.cuda.device_count())] if available else []}))"
            p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
            if p.returncode == 0:
                result.update(json.loads(p.stdout.strip().splitlines()[-1]))
                if result["cuda_available"]:
                    result["gpus"] = result.pop("visible_gpus", result["gpus"])
                else:
                    result.pop("visible_gpus", None)
            else:
                result.update(torch_available=False, environment_error="PyTorch could not be imported. Repair the app environment before using a PyTorch recipe.")
        except (OSError, subprocess.SubprocessError, ValueError):
            result.update(torch_available=False, environment_error="PyTorch validation could not finish. Recheck or repair the app environment.")
    return result


class Service:
    def __init__(self, root):
        self.store = Store(root)
        self.root = self.store.root
        self.jobs_root = self.root / "jobs"
        self.jobs_root.mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.environment_busy = False
        self.processes = {}
        self.threads = {}
        self.closing = threading.Event()
        self.hardware = hardware_info()
        self._event_readers = {}
        self._event_lock = threading.RLock()
        self._ingest_cursors = {}
        self._launch_clocks = {}
        self._migrate()
        for job in self.store.all("job"):
            if self._manually_resolved(job) or job.get("submission_status") == "not_started":
                continue
            if job["status"] in ("queued", "running", "pausing", "exporting") or (job["status"] == "interrupted" and (job.get("handle") or job.get("submission_intent"))):
                job.update(status="interrupted", connection_status="unknown", message="Reconnecting to the previous execution before another attempt can start.")
                self.save_job(job)
                if job["provider_id"] != "local" and (job.get("handle") or job.get("submission_intent")):
                    thread = threading.Thread(target=self.reconcile, args=(job["id"],), daemon=True)
                    self.threads[job["id"]] = thread
                    thread.start()
                elif job["provider_id"] == "local":
                    from .worker import worker_active
                    if worker_active(self.jobs_root / job["id"]):
                        job.update(status="running", connection_status="connected", message="Reconnected to the existing local worker.")
                        self.save_job(job)
                        thread = threading.Thread(target=self._monitor_local_orphan, args=(job["id"],), daemon=True)
                        self.threads[job["id"]] = thread
                        thread.start()

    def _migrate(self):
        if self.store.get("schema", "version", {}).get("version", 1) >= 2:
            return
        import sqlite3
        backup = self.root / "state.before-v2.sqlite3"
        if not backup.exists():
            import contextlib
            with contextlib.closing(sqlite3.connect(backup)) as target:
                self.store.db.backup(target)
        for job in self.store.all("job"):
            job.setdefault("execution_mode", "guided" if job["spec"].get("guided") or next((p["integration"] != "automatic" for p in CATALOG if p["id"] == job["provider_id"]), False) else "automatic")
            job["spec"].setdefault("recipe_version", 1)
            job.setdefault("schema_version", 2)
            job.setdefault("cleanup_status", "unknown")
            self.save_job(job)
        self.store.put("schema", "version", {"version": 2})

    def draft(self):
        value = self.store.get("draft", "local", {"spec": {}, "wizard_step": 0})
        value["has_saved_draft"] = bool(value.get("has_saved_draft", value.get("spec")))
        value.setdefault("spec", {}).update(free_only=True, trust_script=False)
        return value

    def save_draft(self, raw):
        value = raw.get("draft", raw)
        if not isinstance(value, dict) or not isinstance(value.get("spec", {}), dict):
            raise ValueError("Invalid training draft.")
        spec = {k: v for k, v in value.get("spec", {}).items() if k not in ("preview_digest", "id", "attempt_id")}
        spec.update(free_only=True, trust_script=False)
        extras = {key:value[key] for key in ("primary", "fallbacks", "dataset_mode", "dataset_report") if key in value}
        meaningful = bool(value.get("spec"))
        return self.store.put("draft", "local", {**extras, "schema_version": 1, "spec": spec, "has_saved_draft": meaningful,
                                                "wizard_step": max(0, min(3, int(value.get("wizard_step", 0))))})

    def settings(self):
        return {"desktop_notifications": False, **self.store.get("settings", "local", {"output_dir": str(self.root / "exports"), "summary_interval_seconds": 300})}

    def save_settings(self, raw):
        settings = self.settings()
        if "output_dir" in raw:
            settings["output_dir"] = str(Path(raw["output_dir"]).expanduser().resolve())
        if "desktop_notifications" in raw:
            if not isinstance(raw["desktop_notifications"], bool):
                raise ValueError("Desktop notifications must be true or false.")
            settings["desktop_notifications"] = raw["desktop_notifications"]
        return self.store.put("settings", "local", settings)

    def connections(self):
        return {x["provider_id"]: x for x in self.store.all("connection")}

    def provider_setup(self, pid, raw=None):
        from .onboarding import provider_setup
        with self.lock:
            if pid not in {p["id"] for p in CATALOG}:
                raise ValueError("Unknown provider.")
            guide = provider_setup(pid, self.connections().get(pid), self.hardware)
            return self._setup_progress("provider_setup", pid, guide, raw)

    def job_setup(self, jid, raw=None):
        from .onboarding import job_setup
        with self.lock:
            guide = job_setup(self.job(jid), self.jobs_root / jid)
            return self._setup_progress("job_setup", jid, guide, raw)

    def _setup_progress(self, kind, key, guide, raw):
        steps = guide["steps"]
        ids = [s["id"] for s in steps]
        saved = self.store.get(kind, key, {})
        if guide.get("revision") is not None and saved.get("revision") != guide["revision"]:
            saved = {}
        acknowledgements = set(saved.get("acknowledged_steps", [])) & set(ids)
        for step in steps:
            # Reading a guide never proves authentication, entitlement or quota.
            automatic = step["completion"] == "automatic"
            step["completed"] = bool(step.get("ready")) if automatic else step["id"] in acknowledgements
            step["can_complete"] = bool(step.get("can_complete", True)) and (not automatic or bool(step.get("ready")))
        current = saved.get("current_step")
        if current not in ids:
            current = next((s["id"] for s in steps if not s["completed"]), ids[-1])
        if raw is not None:
            if not isinstance(raw, dict) or set(raw) - {"action", "step_id", "acknowledged", "guide_revision"}:
                raise ValueError("Send a setup action, step_id and optional acknowledgement only.")
            if guide.get("revision") is not None and raw.get("guide_revision") != guide["revision"]:
                raise ValueError("The training files changed. Refresh this setup guide before continuing.")
            action, selected = raw.get("action"), raw.get("step_id")
            if action not in ("advance", "back", "goto") or selected not in ids:
                raise ValueError("Choose a valid setup step and action.")
            if action == "goto":
                current = selected
            else:
                if selected != current:
                    raise ValueError("The setup step changed. Refresh the guide before continuing.")
                index = ids.index(current)
                if action == "advance":
                    step = steps[index]
                    if not step["can_complete"]:
                        raise ValueError(step.get("blocking_reason") or "Finish this setup step before continuing.")
                    if step["completion"] == "acknowledgement":
                        if raw.get("acknowledged") is not True:
                            raise ValueError("Confirm you finished this step before continuing.")
                        acknowledgements.add(current)
                        step["completed"] = True
                    current = ids[min(index + 1, len(ids) - 1)]
                else:
                    current = ids[max(index - 1, 0)]
            self.store.put(kind, key, {"schema_version": 1, "current_step": current,
                                      "acknowledged_steps": sorted(acknowledgements),
                                      "revision": guide.get("revision"), "updated_at": now()})
        guide["progress"] = {"current_step": current, "completed_steps": [s["id"] for s in steps if s["completed"]]}
        guide["completed"] = all(s["completed"] for s in steps)
        return guide

    def bootstrap(self):
        from .research import research_documents, research_providers
        connections = self.connections()
        providers = []
        for p in CATALOG:
            connection = connections.get(p["id"], {"ready": p["id"] == "local", "detail": "Available on this computer." if p["id"] == "local" else "Not connected", "config": {}})
            if p["id"] == "local":
                connection = {**connection, "ready": True, "checked_at": self.hardware.get("checked_at"),
                              "actual_resource": {"machine": str(self.hardware.get("cpu_count") or "Available") + " CPU threads",
                                                  "status": "CUDA available" if self.hardware.get("cuda_available") else "CPU available",
                                                  "gpu_names": [gpu["name"] for gpu in self.hardware.get("gpus", [])] if self.hardware.get("cuda_available") else []}}
            providers.append({**p, "connection": connection})
        from . import __version__
        environment = getattr(self, "environment", None)
        return {"providers": providers, "research_providers": research_providers(), "research_documents": research_documents(),
                "jobs": self.jobs(), "hardware": self.hardware, "settings": self.settings(),
                "environment_tasks": environment.recent() if environment else [],
                "app": {"name": "MultiGPU", "version": __version__, "workspace_dir":str(self.root), "runtime_base_prefix": sys.base_prefix}, "recipes": [{**r,"supports_demo":r["id"] in ("starter","pytorch")} for r in RECIPES], "desktop_available": os.name == "nt"}

    def runtime_snapshot(self):
        """Mutable observations only; catalog and research arrive once at bootstrap."""
        environment = getattr(self, "environment", None)
        connections = self.connections()
        connections["local"] = {**connections.get("local", {}), "ready": True, "checked_at": self.hardware.get("checked_at"),
                                "detail": "Available on this computer.", "actual_resource": {
                                    "machine": str(self.hardware.get("cpu_count") or "Available") + " CPU threads",
                                    "status": "CUDA available" if self.hardware.get("cuda_available") else "CPU available",
                                    "gpu_names": [g["name"] for g in self.hardware.get("gpus", [])] if self.hardware.get("cuda_available") else []}}
        return {"connections": connections, "jobs": self.jobs(), "hardware": self.hardware,
                "settings": self.settings(), "environment_tasks": environment.recent() if environment else []}

    def probe(self, pid, config):
        if pid not in {p["id"] for p in CATALOG}:
            raise ValueError("Unknown provider.")
        if not isinstance(config, dict):
            raise ValueError("Connection settings must be an object.")
        unknown = set(config) - CONFIG_KEYS
        if unknown:
            raise ValueError("Use the provider's native login. The app does not accept or store passwords or API tokens. Unknown settings: " + ", ".join(sorted(unknown)))
        result = connectors.probe(pid, config)
        if result.get("error_code"):
            self._log_provider_error(None, connectors.ConnectorError(result["detail"], code=result["error_code"], diagnostic=result.get("diagnostic")), "connection check", pid)
        result.update(provider_id=pid, config=config, checked_at=now())
        self.store.put("connection", pid, result)
        return result

    def _log_provider_error(self, jid, exc, stage, provider=None):
        event = {"timestamp": now(), "stage": stage, "provider": provider,
                 "job_id": jid, "error_code": getattr(exc, "code", "provider"),
                 "message": connectors.redact_provider_output(str(exc)), "exception_type": type(exc).__name__}
        if isinstance(exc, connectors.ConnectorError) and exc.diagnostic:
            event["diagnostic"] = connectors.redact_diagnostic(exc.diagnostic)
        if getattr(exc, "submission_evidence", None):
            event["submission_evidence"] = connectors.redact_diagnostic(exc.submission_evidence)
        line = json.dumps(event, ensure_ascii=False) + "\n"
        with self.lock:
            try:
                folder = self.root / "logs"
                folder.mkdir(exist_ok=True)
                target = folder / "app.log"
                if target.exists() and target.stat().st_size > 2 * 1024 * 1024:
                    target.replace(folder / "app.previous.log")
                with target.open("a", encoding="utf-8") as log:
                    log.write(line)
                if jid:
                    with (self.jobs_root / jid / "worker.log").open("a", encoding="utf-8") as log:
                        log.write(line)
            except OSError:
                pass  # Diagnostics must not replace the original training failure.

    def diagnostic_logs(self):
        path = self.root / "logs" / "app.log"
        if not path.is_file():
            return {"path": str(path), "logs": "No provider errors have been recorded yet."}
        with path.open("rb") as log:
            log.seek(max(0, path.stat().st_size - 65536))
            content = log.read().decode("utf-8", errors="replace")
        return {"path": str(path), "logs": content}

    def jobs(self):
        return [self._describe_job(j) for j in sorted(self.store.all("job"), key=lambda j: j["created_at"], reverse=True)]

    @staticmethod
    def _manually_resolved(job):
        resolution = job.get("submission_resolution", {})
        attempt = job.get("spec", {}).get("attempt_id")
        return bool(attempt) and resolution.get("source") == "user_confirmed" and resolution.get("outcome") in ("stopped", "absent") and resolution.get("attempt_id") == attempt

    @staticmethod
    def _recovery_token(job):
        snapshot = {key: job.get(key) for key in ("id", "provider_id", "submission_status", "submission_intent", "handle", "submission_resolution")}
        snapshot["attempt_id"] = job.get("spec", {}).get("attempt_id")
        return hashlib.sha256(json.dumps(snapshot, sort_keys=True, allow_nan=False).encode()).hexdigest()

    def _describe_job(self, job):
        reasons = []
        unresolved = job.get("submission_status") == "unknown" and not self._manually_resolved(job)
        if unresolved:
            reasons.append("Provider launch needs resolution")
        if job["status"] in ("error", "interrupted"):
            reasons.append("Training needs recovery")
        for key, label in (("export_status", "Local export"), ("collection_status", "Result collection"), ("cleanup_status", "Provider cleanup")):
            if job.get(key) == "failed":
                reasons.append(label + " needs attention")
        job["attention_required"] = bool(reasons)
        job["attention_reasons"] = reasons
        job["recovery_token"] = self._recovery_token(job)
        if job.get("export_path") and not job.get("inference"):
            from .prediction import inference_capability
            job["inference"] = inference_capability(Path(job["export_path"]))
        job.setdefault("inference", {"supported": False, "reason": "Verify and export the model before prediction.", "features": [], "classes": []})
        job["guided_providers"] = self._guided_options(job)
        job["allowed_actions"] = self._allowed_actions(job)
        return job

    def job(self, jid, include_events=False, observations_revision=None):
        if not jid or any(c not in "0123456789abcdef" for c in jid):
            raise ValueError("Invalid job identifier.")
        job = self.store.get("job", jid)
        if job is None:
            raise KeyError("Training run not found.")
        if include_events:
            observations = []
            for name in ("events.jsonl", "imported-history.jsonl", "worker.log"):
                path = self.jobs_root / jid / name
                stat = path.stat() if path.is_file() else None
                observations.append((name, stat.st_size, stat.st_mtime_ns, stat.st_ino) if stat else (name,))
            revision = hashlib.sha256(json.dumps(observations).encode()).hexdigest()
            job["observations_revision"] = revision
            job["observations_unchanged"] = observations_revision == revision
            if not job["observations_unchanged"]:
                events = self.read_events(jid)
                job["events"] = events[-500:]
                observed = {(e.get("attempt_id"), e.get("step")): e for e in events if e.get("loss") is not None
                            and e.get("type") in {"metric", "checkpoint", "completed", "paused", "error"}}
                job["metrics"] = list(observed.values())[-2000:]
        return self._describe_job(job)

    def _allowed_actions(self, job):
        actions = []
        status = job["status"]
        resolved = self._manually_resolved(job)
        cleanup_blocked = job.get("provider_id") == "lightning" and job.get("cleanup_status") in ("pending", "failed") and not resolved
        stopped = status in TERMINAL and not cleanup_blocked and (job.get("submission_status") != "unknown" or resolved) and (not job.get("handle") or job.get("remote_terminal") or resolved)
        thread = self.threads.get(job["id"])
        idle = job["id"] not in self.processes and not (thread and thread.is_alive())
        if stopped and idle:
            actions.extend(["archive", "relocate-inputs"])
        if (job.get("submission_status") == "unknown" or cleanup_blocked) and not resolved and idle:
            actions.append("resolve-submission")
        if status in ("queued", "running", "pausing"):
            actions.append("pause" if job["provider_id"] != "kaggle" else "external-stop")
            if job["provider_id"] == "modal":
                actions.append("cancel")
        if status in TERMINAL:
            if job.get("execution_mode") == "guided" and stopped and idle:
                actions.append("import")
                if job.get("step", 0) < job["spec"]["steps"] and next(p for p in CATALOG if p["id"] == job["provider_id"])["capabilities"].get("notebook_bundle"):
                    actions.append("bundle")
            checkpoint_step = (job.get("checkpoint") or {}).get("step", job.get("step", 0))
            checkpoint_exists = (self.jobs_root / job["id"] / "checkpoint.json").is_file()
            resumable = checkpoint_exists or self._pristine_pause(job)
            if status in ("paused", "interrupted", "error") and checkpoint_step < job.get("total_steps", job["spec"]["steps"]) and resumable and stopped and idle:
                if self._resource_preview(job, job["spec"]["providers"])["eligible"]:
                    actions.append("resume")
                if self._resource_preview(job, ["local"])["eligible"]:
                    actions.append("resume-local")
            if stopped and idle and checkpoint_exists:
                if checkpoint_step < job["spec"]["steps"] and self._guided_options(job):
                    actions.append("continue-guided")
                if checkpoint_step == job["spec"]["steps"] and job["spec"]["recipe"] in ("starter", "pytorch", "image"):
                    internal = self.jobs_root / job["id"] / "export" / "manifest.json"
                    if not internal.is_file() or job.get("worker_status") == "error":
                        actions.append("finish-delivery")
            if stopped and idle and (self.jobs_root / job["id"] / "checkpoint.previous.json").exists():
                actions.append("recover")
        if job.get("handle") or job.get("submission_intent"):
            actions.append("reconcile")
        if status == "completed" or job.get("export_path"):
            actions.append("export")
        if job.get("export_path"):
            actions.append("open-export")
            if job.get("inference", {}).get("supported"):
                actions.append("predict")
        return actions

    def _resource_preview(self, job, providers, guided=False):
        others = [item for item in self.store.all("job") if item["id"] != job["id"]]
        return preview({**job["spec"], "providers": providers, "guided": guided}, CATALOG,
                       self.connections(), self.hardware, others)

    def _guided_options(self, job):
        return [pid for pid in job["spec"]["providers"]
                if self._resource_preview(job, [pid], guided=True).get("preparation_eligible")]

    def save_job(self, job):
        job["updated_at"] = now()
        return self.store.put("job", job["id"], job)

    def plan(self, raw):
        from .datasets import inspect_dataset
        spec = validate_spec(raw, self.settings()["output_dir"])
        if spec["recipe"] == "image" and not spec.get("weights_path"):
            spec.update(self.store.get("environment", "image_weights", {}))
        report = inspect_dataset(spec)
        report["actions"] = []
        if report["errors"]:
            report["actions"].append({"type": "wizard", "step": 0, "label": "Review training inputs"})
        if spec["recipe"] == "image" and not report.get("errors") and not report.get("dataset", {}).get("weights", {}).get("ready"):
            report["errors"].append("Prepare the image environment and verified pretrained weights before launching or preparing a notebook.")
            report["actions"].append({"type": "environment", "profile": "image", "device": spec["device"], "label": "Prepare image dependencies"})
        input_errors = len(report["errors"])
        try:
            destination = Path(spec["output_dir"])
            if destination.exists() and not destination.is_dir():
                report["errors"].append("The export path is a file. Choose a folder.")
            existing = destination
            while not existing.exists():
                existing = existing.parent
            if existing.is_file():
                existing = existing.parent
            if not os.access(existing, os.W_OK):
                report["errors"].append("The export folder is not writable. Choose another folder.")
            if destination.resolve().is_relative_to(self.jobs_root.resolve()):
                report["errors"].append("Choose an export folder outside the application's internal jobs directory.")
            required = 10 * 1024 * 1024
            weights = report.get("dataset", {}).get("weights", {})
            if spec["recipe"] == "image" and weights.get("ready"):
                required += 3 * Path(weights["path"]).stat().st_size
            available = shutil.disk_usage(existing).free
            report["storage"] = {"required_free_bytes": required, "available_free_bytes": available}
            if available < required:
                report["errors"].append("The export drive has insufficient space for a verified export and its recovery copy. Choose another folder.")
        except OSError as exc:
            report["errors"].append("Cannot access the export folder: " + str(exc))
        if len(report["errors"]) > input_errors:
            report["actions"].append({"type": "wizard", "step": 2, "label": "Choose another export folder"})
        if spec["recipe"] == "image" and report.get("dataset", {}).get("total_steps"):
            spec["steps"] = report["dataset"]["total_steps"]
        result = preview(spec, CATALOG, self.connections(), self.hardware, self.jobs())
        if result["selected_provider"] == "local" and spec["device"] == "cuda" and self.hardware.get("gpus"):
            index = spec.get("gpu_index", 0)
            gpus = self.hardware["gpus"]
            capacity = gpus[index].get("memory_mb", 0) * 1024 * 1024 if index < len(gpus) else 0
            batch = report.get("dataset", {}).get("estimated_batch_bytes", 0)
            if index >= len(gpus):
                report["errors"].append("The selected local GPU index is unavailable. Choose an attached GPU.")
            elif capacity and batch > capacity:
                report["errors"].append("The input batch alone exceeds the selected local GPU's total memory. Reduce batch size.")
            else:
                report["warnings"].append("Batch memory is an estimate; model, optimizer and runtime memory can still exceed GPU capacity.")
        if report["errors"]:
            result["eligible"] = False
            result["preparation_eligible"] = False
            result["selected_provider"] = None
            result["execution_mode"] = None
            result["allowed_actions"] = []
            for decision in result["decisions"]:
                decision.update(eligible=False, preparation_eligible=False, reason="; ".join(report["errors"]))
        result["preflight"] = report
        result["warnings"].extend(report.get("warnings", []))
        result["spec"] = spec
        from .performance import recommendations
        result["performance_recommendations"] = recommendations(spec, result["decisions"], self.jobs(), report.get("fingerprint"), self.hardware, self.connections())
        identities = {pid: self.connections().get(pid, {}).get("config", {}) for pid in spec["providers"]}
        digest = {"spec": spec, "fingerprint": report.get("fingerprint"), "selected_provider": result["selected_provider"], "execution_mode": result.get("execution_mode"), "resolved_device": result.get("resolved_device"), "resolved_resource": result.get("resolved_resource"), "accounts": identities}
        result["preview_digest"] = hashlib.sha256(json.dumps(digest, sort_keys=True, allow_nan=False).encode()).hexdigest()
        return result

    def create(self, raw, require_preview=False):
        with self.lock:
            if self.environment_busy:
                raise ValueError("Wait for environment preparation to finish before starting training.")
            plan = self.plan(raw)
            digest = raw.get("preview_digest")
            if (require_preview or digest is not None) and (not isinstance(digest, str) or not hmac.compare_digest(digest, plan["preview_digest"])):
                raise ValueError("The reviewed plan changed. Preview your current settings again before starting.")
            if plan["preflight"]["errors"]:
                raise ValueError("; ".join(plan["preflight"]["errors"]))
            spec = plan["spec"]
            spec["fingerprint"] = plan["preflight"].get("fingerprint")
            spec["resolved_resource"] = plan.get("resolved_resource")
            guided = plan.get("execution_mode") == "guided"
            if guided and not plan.get("preparation_eligible"):
                raise ValueError("Guided preparation is blocked. Resolve the plan's requirements before continuing.")
            if not guided and not plan["eligible"]:
                raise ValueError("No selected resource is eligible. Resolve the plan's blockers or explicitly prepare a guided notebook.")
            if guided and all(d["mode"] == "development" for d in plan["decisions"]):
                raise ValueError("This resource is a development helper, not a training backend.")
            jid = uuid.uuid4().hex
            spec["id"] = jid
            if guided:
                spec["attempt_id"] = uuid.uuid4().hex
            pid = plan["selected_provider"]
            if not pid:
                raise ValueError("No reviewed resource can run or prepare this training. Resolve the plan's blockers.")
            path = self.jobs_root / jid
            path.mkdir()
            self._write_json(path / "spec.json", spec)
            job = {"id": jid, "name": spec["name"], "status": "awaiting_user" if guided else "queued", "provider_id": pid,
                   "created_at": now(), "updated_at": now(), "spec": spec, "step": 0, "total_steps": spec["steps"],
                   "loss": None, "device": None, "checkpoint": None, "export_path": None,
                   "message": "Follow the in-app setup guide to run training in your provider account." if guided else "Preparing training environment.",
                   "plan": plan, "attempted_providers": [pid], "schema_version": 2,
                   "execution_mode": "guided" if guided else "automatic", "phase": "preparing", "connection_status": "connected",
                   "submission_status": "prepared", "cleanup_status": "not_required" if guided else "pending", "export_status": "pending",
                   "fingerprint": plan["preflight"].get("fingerprint")}
            job["input_snapshot"] = {"fingerprint": job["fingerprint"], "metadata": plan["preflight"].get("dataset", {})}
            job["limit_enforcement"] = plan.get("limit_enforcement", "manual_unverified" if guided else "local" if pid == "local" else "provider_quota")
            self.save_job(job)
            if not guided:
                self._start(jid)
            self.store.put("draft", "local", {"spec": {}, "wizard_step": 0, "has_saved_draft": False})
            return job

    @staticmethod
    def _write_json(path, value):
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")
        os.replace(temp, path)

    def _event_source(self, source):
        previous = self._event_readers.get(str(source), {})
        if not source.is_file():
            self._event_readers[str(source)] = {"items": [], "sequence": previous.get("sequence", 0)}
            return {"items": [], "sequence": previous.get("sequence", 0)}
        with source.open("rb") as raw:
            stat = os.fstat(raw.fileno())
            identity = (stat.st_dev, stat.st_ino)
            offset = previous.get("offset", 0)
            reset = previous.get("identity") != identity or stat.st_size < offset
            if not reset and offset:
                raw.seek(max(0, offset - 64))
                reset = raw.read(min(64, offset)) != previous.get("boundary")
            state = {"identity": identity, "offset": 0, "items": deque(maxlen=8000), "sequence": previous.get("sequence", 0)} if reset else previous
            start = state.get("offset", 0)
            # Read only complete lines; keep a bounded tail when reconnecting.
            if stat.st_size - start > 2 * 1024 * 1024:
                raw.seek(stat.st_size - 2 * 1024 * 1024)
                raw.readline(65537)
                start = raw.tell()
            raw.seek(start)
            content = raw.read(2 * 1024 * 1024)
            complete = content.rfind(b"\n") + 1
            for line in content[:complete].splitlines():
                try:
                    event = json.loads(line)
                    if isinstance(event, dict):
                        state["sequence"] += 1
                        state["items"].append((state["sequence"], event))
                except (ValueError, UnicodeError):
                    continue
            state["offset"] = start + complete
            raw.seek(max(0, state["offset"] - 64))
            state["boundary"] = raw.read(min(64, state["offset"]))
            self._event_readers[str(source)] = state
            return state

    def read_events(self, jid, include_history=True, incremental=False):
        with self._event_lock:
            paths = [self.jobs_root / jid / "events.jsonl"]
            if include_history:
                paths.append(self.jobs_root / jid / "imported-history.jsonl")
            events = []
            for source in paths:
                state = self._event_source(source)
                cursor = self._ingest_cursors.get(str(source), 0) if incremental else 0
                events.extend(event for sequence, event in state["items"] if sequence > cursor)
                if incremental:
                    self._ingest_cursors[str(source)] = state["sequence"]
            if include_history:
                events.sort(key=lambda item: item.get("timestamp", ""))
            return events

    def _start(self, jid):
        thread = threading.Thread(target=self._run, args=(jid,), daemon=True, name="train-" + jid[:8])
        self.threads[jid] = thread
        thread.start()

    def _ingest(self, job):
        # Imported observations are display history only, never worker liveness,
        # progress mutations, or terminal acknowledgments for a new attempt.
        events = self.read_events(job["id"], include_history=False, incremental=True)
        if job["id"] in self._launch_clocks:
            started = next((e for e in events if e.get("type") == "started" and e.get("attempt_id") == job["spec"].get("attempt_id")), None)
            if started:
                wall_start = self._launch_clocks.pop(job["id"])
                try:
                    duration = max(0, (datetime.fromisoformat(started["timestamp"]) - wall_start).total_seconds())
                    job.setdefault("performance", {}).setdefault("controller_stages_seconds", {})["provider_startup"] = round(duration, 6)
                except (ValueError, TypeError, KeyError):
                    pass
        for event in events:
            if event.get("attempt_id") is not None and event.get("attempt_id") != job["spec"].get("attempt_id"):
                continue
            if isinstance(event.get("performance"), dict) and event.get("attempt_id") == job["spec"].get("attempt_id"):
                controller = (job.get("performance") or {}).get("controller_stages_seconds", {})
                job["performance"] = {**event["performance"], "controller_stages_seconds": controller}
            job["last_worker_event_at"] = event.get("timestamp", job.get("last_worker_event_at"))
            if event.get("type") == "metric":
                job["last_progress_at"] = event.get("timestamp")
            if event.get("type") == "summary" and event.get("attempt_id") == job["spec"].get("attempt_id"):
                job["last_summary"] = event
            if event.get("type") == "evaluation":
                job["evaluation"] = event.get("metrics", event.get("evaluation", {}))
            if event.get("evaluation"):
                job["evaluation"] = event["evaluation"]
            if event.get("phase") and event.get("attempt_id") == job["spec"].get("attempt_id"):
                job["phase"] = event["phase"]
            for key in ("step", "total_steps", "loss", "device", "elapsed_seconds"):
                if event.get(key) is not None:
                    job[key] = event[key]
            if event.get("type") == "checkpoint":
                job["checkpoint"] = {"step": event.get("step"), "path": event.get("path", event.get("checkpoint_path")), "at": event.get("timestamp", now())}
            if event.get("type") in ("paused", "completed", "error") and event.get("attempt_id") == job["spec"].get("attempt_id"):
                job["worker_status"] = event["type"]
                if event.get("message") or event.get("error"):
                    job["message"] = event.get("message", event.get("error"))
        checkpoint = self.jobs_root / job["id"] / "checkpoint.json"
        if checkpoint.exists():
            from .artifacts import load_checkpoint, IntegrityError
            try:
                saved = load_checkpoint(checkpoint)
                job["checkpoint"] = {"path": str(checkpoint), "step": saved["step"], "at": job.get("checkpoint", {}).get("at") if job.get("checkpoint") else now(), "location":"local", "verified":True}
            except (IntegrityError, OSError):
                job["checkpoint_error"] = "The latest local checkpoint needs recovery. Previous verified state may still be available."
        return job

    def _run(self, jid):
        path = self.jobs_root / jid
        job = self.job(jid)
        preparation_started = time.monotonic()
        try:
            if (path / "STOP").exists():
                job.update(status="paused", phase="paused", message="Paused before a new execution was submitted.")
                self.save_job(job)
                return
            if job["provider_id"] == "local":
                while not self.closing.is_set():
                    with self.lock:
                        if (path / "STOP").exists():
                            job.update(status="paused", phase="paused", message="Paused while waiting for this computer.")
                            self.save_job(job)
                            return
                        busy = any(other["id"] != jid and other.get("provider_id") == "local" and other["status"] in ("running", "pausing") for other in self.jobs())
                        if not busy:
                            job.update(status="running", phase="preparing")
                            self.save_job(job)
                            break
                        job.update(status="queued", phase="waiting", message="Waiting for this computer's current training run to release its compute.")
                        self.save_job(job)
                    self.closing.wait(0.5)
                if self.closing.is_set():
                    return
            job["spec"]["attempt_id"] = uuid.uuid4().hex
            preparation_started = time.monotonic()
            if job.get("performance", {}).get("source") == "worker":
                job["performance_attempts"] = [*job.get("performance_attempts", []), job["performance"]][-20:]
            job["performance"] = {"format": "multigpu-performance-v1", "source": "controller",
                                  "attempt_id": job["spec"]["attempt_id"], "controller_stages_seconds": {}}
            job["spec"]["execution_provider"] = job["provider_id"]
            from .datasets import fingerprint_for_spec
            if job["spec"].get("fingerprint") and fingerprint_for_spec(job["spec"]) != job["spec"]["fingerprint"]:
                raise ValueError("Inputs changed after preflight; preview this training plan again")
            delivery = job.get("run_kind") == "delivery"
            if not delivery:
                current_plan = self._resource_preview(job, [job["provider_id"]])
                resource = next((d.get("resolved_resource") for d in current_plan["decisions"] if d["provider_id"] == job["provider_id"]), None)
                frozen = job["spec"].get("resolved_resource")
                if frozen is not None and frozen != resource:
                    raise ValueError("The selected compute resource changed after review. Preview a new continuation before launching.")
                if frozen is None:
                    job["spec"]["resolved_resource"] = resource
            observation = dict(self.connections().get(job["provider_id"], {}).get("quota") or {})
            observation.update(confidence=observation.get("confidence", "unknown"), snapshot=True,
                               source=observation.get("source", "local" if job["provider_id"] == "local" else "provider_connection"))
            observation.setdefault("observed_at", self.connections().get(job["provider_id"], {}).get("checked_at"))
            job["allowance_observation"] = observation
            job["spec"]["allowance_observation"] = observation
            execution_spec = dict(job["spec"])
            if execution_spec.get("resume_from") and job.get("last_progress_at"):
                from .artifacts import load_checkpoint
                resumed_state = load_checkpoint(Path(execution_spec["resume_from"]))
                execution_spec["observed_repeated_steps"] = max(0, job.get("step", 0) - resumed_state["step"])
            history = [other for other in self.jobs() if other["id"] != jid and other.get("status") == "completed"
                       and other.get("provider_id") == job["provider_id"] and other.get("fingerprint") == job.get("fingerprint")]
            reserves = [sum((other.get("performance", {}).get("stages_seconds", {}).get(stage) or 0) for stage in ("evaluation", "export")) for other in history]
            if reserves:
                execution_spec["delivery_reserve_seconds"] = max(reserves)
            if delivery:
                recovery = job["delivery"]
                execution_spec.update(device="cpu", delivery_only=True, resume_from=recovery["checkpoint"]["path"],
                                      resume_sha256=recovery["checkpoint"]["sha256"], resolved_resource=recovery["resolved_resource"])
            self._write_json(path / "spec.json", execution_spec)
            job.update(status="running", phase="preparing", message="Preparing the training environment.")
            self.save_job(job)
            self._controller_cost(job, "preparation", preparation_started)
            if job["provider_id"] == "local":
                env = os.environ.copy()
                package_root = str(Path(__file__).resolve().parent.parent)
                env["PYTHONPATH"] = package_root + os.pathsep + env.get("PYTHONPATH", "")
                with (path / "worker.log").open("a", encoding="utf-8") as log:
                    self._launch_clocks[jid] = datetime.now(timezone.utc)
                    process = subprocess.Popen([sys.executable, "-m", "multigpu.worker", "--spec", str(path / "spec.json"), "--output", str(path)], stdout=log, stderr=subprocess.STDOUT, env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    self.processes[jid] = process
                    job.update(phase="evaluating" if delivery else "training", local_pid=process.pid)
                    while process.poll() is None:
                        if self.closing.is_set():
                            (path / "STOP").touch()
                        current = self.job(jid)
                        job["status"] = current["status"]
                        self.save_job(self._ingest(job))
                        time.sleep(0.5)
                    self.processes.pop(jid, None)
                job = self._ingest(job)
                job["status"] = job.get("worker_status") or ("completed" if process.returncode == 0 else "error")
                if job["status"] == "error":
                    if not any(e.get("error") for e in self.read_events(jid)):
                        job["message"] = "Training failed. Open the run logs for details."
                elif job["status"] == "paused":
                    job["message"] = "Progress saved. Resume preserves the original training target."
                elif job["status"] == "completed":
                    job["message"] = "Training finished. Verify and export the model to your local folder."
                self.save_job(job)
                if job["status"] == "completed":
                    self._auto_export(jid)
                elif job["status"] == "paused":
                    self._continue_to_next(job)
            else:
                config = self.connections().get(job["provider_id"], {}).get("config", {})
                staging_started = time.monotonic()
                intent = connectors.prepare(job["provider_id"], config, path / "spec.json", path)
                self._controller_cost(job, "staging", staging_started)
                if (path / "STOP").exists():
                    job.update(status="paused", phase="paused", submission_status="prepared", message="Paused before provider submission.")
                    self.save_job(job)
                    return
                job.update(submission_intent=intent, submission_status="submitting", phase="submitting", connection_status="connected")
                self.save_job(job)
                try:
                    submission_started = time.monotonic()
                    handle = connectors.submit(job["provider_id"], config, path / "spec.json", path, intent=intent)
                    # SDK submission may include upload and provisioning; do not invent a split.
                    self._controller_cost(job, "submission_upload_and_startup", submission_started)
                except Exception as exc:
                    self._log_provider_error(jid, exc, "submission", job["provider_id"])
                    known_not_started = getattr(exc, "submission_outcome", "unknown") == "not_started"
                    if getattr(exc, "intent", None):
                        job["submission_intent"] = exc.intent
                        if exc.intent.get("cleanup_status"):
                            job["cleanup_status"] = exc.intent["cleanup_status"]
                    if known_not_started and not (getattr(exc, "intent", None) or {}).get("cleanup_status"):
                        job["cleanup_status"] = "not_required"
                    job["submission_evidence"] = getattr(exc, "submission_evidence", None)
                    job.update(status="error" if known_not_started else "interrupted", submission_status="not_started" if known_not_started else "unknown", connection_status="connected" if known_not_started else "unknown",
                               error_code=getattr(exc, "code", "provider"),
                               message=("Training did not start. Resolve this issue before retrying: " if known_not_started else "Submission was not confirmed. Reconcile this attempt before retrying: ") + str(exc))
                    self.save_job(job)
                    return
                job.update(handle=handle, submission_status="confirmed", phase="training")
                job["submission_evidence"] = handle.get("submission_evidence")
                self.save_job(job)
                self._monitor_remote(jid)
        except Exception as exc:
            job = self.job(jid)
            self._log_provider_error(jid, exc, "preparation", job["provider_id"])
            job.update(status="error", error_code=getattr(exc, "code", "provider"), message=str(exc))
            if getattr(exc, "submission_outcome", None) == "unknown":
                job.update(status="interrupted", submission_status="unknown", connection_status="unknown")
                if getattr(exc, "intent", None):
                    job["submission_intent"] = exc.intent
            elif job.get("phase") in ("preparing", "waiting") and not job.get("handle"):
                job.update(submission_status="not_started", cleanup_status="not_required")
            job["submission_evidence"] = getattr(exc, "submission_evidence", None)
            self.save_job(job)

    @staticmethod
    def _controller_cost(job, stage, started):
        performance = job.setdefault("performance", {})
        performance.setdefault("source", "controller")
        performance.setdefault("format", "multigpu-performance-v1")
        performance.setdefault("controller_stages_seconds", {})[stage] = round(time.monotonic() - started, 6)
        performance["controller_observed_at"] = now()

    @staticmethod
    def _pristine_pause(job):
        return job.get("step", 0) == 0 and (job.get("submission_status", "prepared") in ("prepared", "not_started") or Service._manually_resolved(job)) and (not job.get("handle") or Service._manually_resolved(job))

    def _assert_stopped(self, job):
        from .worker import worker_active
        jid = job["id"]
        thread = self.threads.get(jid)
        if jid in self.processes or (thread and thread.is_alive()) or worker_active(self.jobs_root / jid):
            raise ValueError("Wait for the previous worker to stop before changing recovery data or starting another attempt.")
        if not self._manually_resolved(job) and (job.get("submission_status") == "unknown" or (job.get("handle") and not job.get("remote_terminal"))):
            raise ValueError("Reconcile the previous remote execution before starting another attempt or replacing recovery data.")
        if job.get("provider_id") == "lightning" and job.get("cleanup_status") in ("pending", "failed") and not self._manually_resolved(job):
            raise ValueError("Stop and resolve the dedicated Studio before retrying; provider compute charges may continue.")

    def _monitor_local_orphan(self, jid):
        from .worker import worker_active
        path = self.jobs_root / jid
        while worker_active(path):
            if self.closing.wait(.5):
                return
            job = self._ingest(self.job(jid))
            self.save_job(job)
        job = self._ingest(self.job(jid))
        job.update(status=job.get("worker_status", "interrupted"), connection_status="connected")
        if job["status"] == "interrupted":
            job["message"] = "The previous worker stopped without a final acknowledgment. Verify its checkpoint before resuming."
        self.save_job(job)
        if job["status"] == "completed":
            self._auto_export(jid)

    def _finish_remote(self, jid, terminal_state):
        from .artifacts import IntegrityError, verify_export
        job = self.job(jid)
        config = self.connections().get(job["provider_id"], {}).get("config", {})
        path = self.jobs_root / jid
        job.update(phase="collecting", remote_terminal=True, connection_status="connected")
        self.save_job(job)
        collection_error = None
        collection_started = time.monotonic()
        try:
            connectors.collect(job["provider_id"], config, job["handle"], path)
        except Exception as exc:
            self._log_provider_error(jid, exc, "result collection", job["provider_id"])
            collection_error = str(exc)
        handle = job["handle"]  # collect may acknowledge cleanup even when it raises afterwards.
        self._controller_cost(job, "collection", collection_started)
        job = self._ingest(self.job(jid))
        job["handle"] = handle
        cleanup = handle.get("cleanup_status", "verified" if handle.get("studio_stopped") else "retained")
        job["cleanup_status"] = cleanup
        try:
            if collection_error and cleanup not in ("failed", "pending"):
                raise IntegrityError(collection_error)
            checkpoint, exported = path / "checkpoint.json", path / "export"
            if checkpoint.exists():
                self._validate_checkpoint(job, checkpoint, delivery=(exported / "manifest.json").exists())
            if (exported / "manifest.json").exists():
                metadata = verify_export(exported).get("metadata", {})
                if metadata.get("job_id") != jid or metadata.get("recipe") != job["spec"]["recipe"] or metadata.get("fingerprint", job.get("fingerprint")) != job.get("fingerprint"):
                    raise IntegrityError("Collected model identity does not match this run")
                if metadata.get("step") != job["spec"]["steps"]:
                    raise IntegrityError("Collected model export has not reached the requested target")
            elif job.get("worker_status") == "completed":
                raise IntegrityError("Completed execution is missing its verified model export")
            elif not checkpoint.exists():
                raise IntegrityError(collection_error or "Provider returned no verified model or checkpoint")
            job["collection_status"] = "verified"
            job.pop("collection_error", None)
        except Exception as exc:
            self._log_provider_error(jid, exc, "result verification", job["provider_id"])
            job.update(collection_status="failed", collection_error=str(exc), status="interrupted", message="The remote execution ended, but results need recovery: " + str(exc))
            if collection_error and cleanup in ("failed", "pending"):
                job["cleanup_error"] = collection_error
            self.save_job(job)
            return job
        successful = terminal_state in ("completed", "complete", "succeeded", "success")
        job["status"] = job.get("worker_status", "completed" if successful else "paused" if (path / "checkpoint.json").exists() else "error")
        job["message"] = "Remote results were collected and verified." if successful else "Provider execution ended. Recoverable progress has been collected."
        if collection_error:
            job["cleanup_error"] = collection_error
        elif cleanup == "verified":
            job.pop("cleanup_error", None)
        self.save_job(job)
        if job["status"] == "completed":
            self._auto_export(jid)
        elif job["status"] == "paused" and cleanup not in ("failed", "pending"):
            self._continue_to_next(job)
        if cleanup in ("failed", "pending"):
            job = self.job(jid)
            job["message"] = "Training results were verified locally. Provider cleanup still needs attention: " + (collection_error or cleanup)
            self.save_job(job)
        return self.job(jid)

    def _monitor_remote(self, jid):
        backoff = 2
        while not self.closing.is_set():
            job = self.job(jid)
            config = self.connections().get(job["provider_id"], {}).get("config", {})
            try:
                state = connectors.poll(job["provider_id"], config, job["handle"], self.jobs_root / jid)
                job = self._ingest(self.job(jid))
                job.update(connection_status="connected", last_provider_check=now(), message=state.get("detail", "Monitoring the provider execution."))
                self.save_job(job)
                backoff = 2
                status = state.get("state", "unknown").lower()
                if status in ("completed", "complete", "succeeded", "success", "error", "failed", "cancelled", "canceled", "paused"):
                    self._finish_remote(jid, status)
                    return
                if job["status"] == "pausing" and not job.get("stop_requested"):
                    stopped = connectors.stop(job["provider_id"], config, job["handle"], self.jobs_root / jid)
                    job.update(stop_requested=True, message=stopped.get("detail", "Waiting for a durable checkpoint and stop acknowledgment."))
                    self.save_job(job)
                self.closing.wait(10)
            except Exception as exc:
                job = self.job(jid)
                self._log_provider_error(jid, exc, "monitoring", job["provider_id"])
                job.update(connection_status="retrying", error_code=getattr(exc, "code", "provider"), message="Provider connection interrupted; this execution may still be running. Retrying: " + str(exc))
                self.save_job(job)
                self.closing.wait(backoff)
                backoff = min(60, backoff * 2)

    def reconcile(self, jid):
        job = self.job(jid)
        if self._manually_resolved(job):
            return job
        thread = self.threads.get(jid)
        if thread and thread.is_alive() and thread is not threading.current_thread():
            return job
        if job["provider_id"] == "local":
            from .worker import worker_active
            busy = worker_active(self.jobs_root / jid)
            job.update(connection_status="connected" if busy else "unknown", message="The previous local worker is still active. Resume is blocked." if busy else "No local worker holds this run. A verified checkpoint can be resumed.")
            return self.save_job(job)
        config = self.connections().get(job["provider_id"], {}).get("config", {})
        try:
            state = connectors.reconcile(job["provider_id"], config, job.get("handle") or job.get("submission_intent"), self.jobs_root / jid)
        except Exception as exc:
            self._log_provider_error(jid, exc, "reconciliation", job["provider_id"])
            state = {"state": "unknown", "detail": str(exc), "error_code": getattr(exc, "code", "provider")}
        status = state.get("state", "unknown").lower()
        if state.get("handle"):
            job["handle"] = state["handle"]
        if status == "unknown" or not job.get("handle"):
            job.update(connection_status="unknown", submission_status="unknown",
                       error_code=state.get("error_code", job.get("error_code", "submission_unknown")),
                       message=state.get("detail", "Provider state is unresolved. Check the provider console before retrying."))
            return self.save_job(job)
        job.update(submission_status="confirmed", connection_status="connected", last_provider_check=now())
        job.pop("error_code", None)
        self.save_job(job)
        if status in ("completed", "complete", "succeeded", "success", "error", "failed", "cancelled", "canceled", "paused"):
            return self._finish_remote(jid, status)
        job.update(status="running", message="Reconnected to the existing provider execution.")
        self.save_job(job)
        if not self.threads.get(jid) or not self.threads[jid].is_alive() or self.threads[jid] is threading.current_thread():
            thread = threading.Thread(target=self._monitor_remote, args=(jid,), daemon=True)
            self.threads[jid] = thread
            thread.start()
        return self.job(jid)

    def _auto_export(self, jid):
        try:
            self.export(jid)
        except Exception as exc:
            job = self.job(jid)
            self._log_provider_error(jid, exc, "local export", job["provider_id"])
            job.update(status="completed", export_status="failed", export_error=str(exc), message="Training finished, but local export needs attention: " + str(exc))
            self.save_job(job)

    def resolve_submission(self, jid, raw):
        """Resolve one reviewed attempt without silently issuing a new launch."""
        with self.lock:
            job = self.job(jid)
            token = raw.get("recovery_token")
            if not isinstance(token, str) or not hmac.compare_digest(token, job["recovery_token"]):
                raise ValueError("The saved attempt changed. Refresh this run before resolving it.")
            if "resolve-submission" not in job["allowed_actions"]:
                raise ValueError("Only an unresolved, inactive submission can be resolved here.")
            outcome = raw.get("outcome")
            if outcome not in ("attach", "stopped", "absent"):
                raise ValueError("Attach an existing execution, or confirm that you stopped it or verified it absent.")
            if job["provider_id"] == "lightning" and job.get("cleanup_status") in ("pending", "failed") and outcome == "absent":
                raise ValueError("A missing training process does not prove the Studio stopped. Confirm that you stopped the dedicated Studio in its console.")
            intent = job.get("submission_intent") or job.get("handle") or {}
            if outcome != "attach" and raw.get("confirmed") is not True:
                raise ValueError("Confirm that you checked the selected provider account and stopped the attempt or verified it absent. Remote work can otherwise continue.")
            config = self.connections().get(job["provider_id"], {}).get("config", {})
        state = None
        if outcome == "attach":
            try:
                state = connectors.attach(job["provider_id"], config, raw.get("execution_id"), intent, self.jobs_root / jid)
            except Exception as exc:
                self._log_provider_error(jid, exc, "attach existing execution", job["provider_id"])
                raise
            if not state.get("handle") or state.get("state", "unknown") == "unknown":
                raise ValueError("The provider could not verify this execution's identity. The existing reservation is retained.")
        with self.lock:
            job = self.job(jid)
            if not hmac.compare_digest(token, job["recovery_token"]) or "resolve-submission" not in job["allowed_actions"]:
                raise ValueError("The saved attempt changed during verification. Refresh this run.")
            legacy_identity = not job["spec"].get("attempt_id")
            if legacy_identity:
                # This is a local recovery nonce, never a claimed provider ID.
                job["spec"]["attempt_id"] = "recovery-" + uuid.uuid4().hex
            resolution = {"outcome": outcome, "source": "provider_verified" if state else "user_confirmed",
                          "attempt_id": job["spec"]["attempt_id"], "checked_at": now(),
                          "identity_source": "local_recovery" if legacy_identity else "saved_attempt",
                          "provider_id": job["provider_id"], "note": connectors.redact_provider_output(str(raw.get("note", ""))[:1000])}
            job["submission_resolution"] = resolution
            if state:
                job.update(handle=state["handle"], submission_status="confirmed", connection_status="connected", last_provider_check=now())
                job.pop("error_code", None)
            else:
                if job["provider_id"] == "lightning" and job.get("cleanup_status") in ("pending", "failed"):
                    job["cleanup_status"] = "user_confirmed_stopped"
                job.update(status="interrupted", phase="resolved", message="You confirmed in the provider console that this attempt is " + ("stopped" if outcome == "stopped" else "absent") + ". This was not independently verified. Review settings before a separate retry.")
            self.save_job(job)
        if state:
            return self.reconcile(jid)
        return self.job(jid)

    def relocate_inputs(self, jid, raw):
        from .datasets import load_dataset
        with self.lock:
            job = self.job(jid)
            self._assert_stopped(job)
            if "relocate-inputs" not in job["allowed_actions"]:
                raise ValueError("Stop and resolve the run before relinking its inputs.")
            updates = {key: raw[key] for key in ("dataset_path", "script_path") if key in raw and raw[key]}
            if not updates or any(not isinstance(value, str) for value in updates.values()):
                raise ValueError("Choose an identical relocated dataset or training script.")
            candidate = validate_spec({**job["spec"], **updates}, self.settings()["output_dir"])
            data = load_dataset(candidate)
            expected = job.get("fingerprint") or job.get("input_snapshot", {}).get("fingerprint")
            if not expected or data["fingerprint"] != expected:
                raise ValueError("The selected inputs differ from the original run. Original recovery data was preserved.")
            job["spec"].update({key: candidate[key] for key in updates})
            job["input_snapshot"] = {"fingerprint": expected, "metadata": data["metadata"]}
            job["inputs_relocated_at"] = now()
            self._write_json(self.jobs_root / jid / "spec.json", job["spec"])
            self.save_job(job)
            return self.job(jid)

    def _continue_to_next(self, job):
        """Only move a committed checkpoint; never shorten the requested target."""
        with self.lock:
            reserved = self._reserve_next(job)
        if reserved:
            self._run(job["id"])

    def _reserve_next(self, job):
        path = self.jobs_root / job["id"]
        if self.closing.is_set() or (path / "STOP").exists() or not (path / "checkpoint.json").exists():
            return
        attempted = job.get("attempted_providers", [job["provider_id"]])
        remaining = [pid for pid in job["spec"]["providers"] if pid not in attempted]
        if not remaining:
            return
        original = job["spec"]["providers"]
        candidate = {**job["spec"], "providers": remaining}
        plan = self.plan(candidate)
        if not plan["eligible"]:
            assisted = next((d["provider_id"] for d in plan["decisions"] if d.get("preparation_eligible")), None)
            if assisted:
                state, checkpoint = self._checkpoint_snapshot(job)
                job.update(pending_handoff={"provider_id": assisted, "checkpoint": checkpoint, "requires_review": True},
                           phase="awaiting_handoff", message="Progress is saved. Review the assisted continuation to finish this same target in your selected provider notebook.")
                self.save_job(job)
            return
        self._validate_checkpoint(job, path / "checkpoint.json")
        job["spec"]["resume_from"] = str(path / "checkpoint.json")
        job["spec"]["providers"] = original
        job.setdefault("attempts", []).append({"provider_id":job["provider_id"], "attempt_id":job["spec"].get("attempt_id"), "handle":job.get("handle"), "status":job["status"], "checkpoint":job.get("checkpoint"), "cleanup_status":job.get("cleanup_status"), "collection_status":job.get("collection_status")})
        for key in ("handle", "submission_intent", "remote_terminal", "stop_requested"):
            job.pop(key, None)
        job.update(submission_status="prepared", connection_status="connected")
        job["provider_id"] = plan["selected_provider"]
        job["spec"]["resolved_resource"] = plan.get("resolved_resource")
        job["spec"]["resume_sha256"] = hashlib.sha256((path / "checkpoint.json").read_bytes()).hexdigest()
        job["attempted_providers"] = [*attempted, job["provider_id"]]
        job.update(status="queued", message="Continuing the saved training target on another selected resource.")
        job.pop("worker_status", None)
        self._write_json(path / "spec.json", job["spec"])
        self.save_job(job)
        return True

    def pause(self, jid):
        with self.lock:
            job = self.job(jid)
            if job["status"] not in ("queued", "running", "pausing"):
                raise ValueError("Only an active run can be paused.")
            if job["provider_id"] == "kaggle":
                raise ValueError("Use Stop in Kaggle. Immediate remote stopping is not available through this connector.")
            (self.jobs_root / jid / "STOP").touch()
            job.update(status="pausing", message="Saving a checkpoint before stopping. Remote jobs may require the provider's stop control.")
            return self.save_job(job)

    def _checkpoint_snapshot(self, job):
        from .artifacts import _check_path
        checkpoint = self.jobs_root / job["id"] / "checkpoint.json"
        _check_path(checkpoint.absolute())
        with checkpoint.open("rb") as handle:
            contents = handle.read(64 * 1024 * 1024 + 1)
        state = self._validate_checkpoint(job, checkpoint, contents=contents)
        return state, {"path": str(checkpoint), "sha256": hashlib.sha256(contents).hexdigest(),
                       "step": state["step"], "total_steps": state["total_steps"], "saved_at": state.get("saved_at")}

    def _recovery_preview(self, job, action, provider_id=None):
        if self.environment_busy:
            raise ValueError("Wait for environment preparation before reviewing a continuation.")
        if job["status"] not in TERMINAL:
            raise ValueError("Stop training before reviewing a continuation.")
        self._assert_stopped(job)
        state, checkpoint = self._checkpoint_snapshot(job)
        candidate = {**job["spec"]}
        if state["recipe"] == "image":
            candidate.update(weights_path=str(self.jobs_root / job["id"] / "assets" / "base_weights.pth"),
                             weights_sha256=state["payload"]["weights_sha256"])
        if action == "continue-guided":
            if state["step"] >= state["total_steps"]:
                raise ValueError("This checkpoint reached its target. Finish model delivery instead of preparing more training.")
            choices = self._guided_options(job)
            provider_id = provider_id or (job.get("pending_handoff") or {}).get("provider_id")
            provider_id = provider_id or (job["provider_id"] if job["provider_id"] in choices else next(iter(choices), None))
            if provider_id not in choices:
                raise ValueError("Choose an eligible assisted provider selected for this run.")
            candidate.update(providers=[provider_id], guided=True)
        else:
            if state["recipe"] not in ("starter", "pytorch", "image") or state["step"] != state["total_steps"]:
                raise ValueError("Delivery recovery requires a full-target checkpoint from a built-in recipe.")
            from .artifacts import IntegrityError, verify_export
            for exported in (self.jobs_root / job["id"] / "export", Path(job["export_path"]) if job.get("export_path") else None):
                if exported is None or not (exported / "manifest.json").is_file():
                    continue
                try:
                    metadata = verify_export(exported).get("metadata", {})
                except (IntegrityError, OSError):
                    continue
                if (metadata.get("job_id") == job["id"] and metadata.get("recipe") == state["recipe"]
                        and metadata.get("fingerprint") == state["fingerprint"] and metadata.get("step") == state["total_steps"]):
                    raise ValueError("Verified model files already exist. Save or download those results instead of running delivery recovery.")
            provider_id = "local"
            candidate.update(providers=["local"], guided=False, device="cpu")
        plan = self.plan(candidate)
        permitted = plan.get("preparation_eligible") if action == "continue-guided" else plan.get("eligible")
        if not permitted or plan.get("selected_provider") != provider_id or plan["preflight"]["errors"]:
            detail = "; ".join(plan["preflight"]["errors"]) or "; ".join(d["reason"] for d in plan["decisions"])
            raise ValueError("Continuation is blocked: " + detail)
        if plan["preflight"].get("fingerprint") != state["fingerprint"] or plan["spec"]["steps"] != state["total_steps"]:
            raise ValueError("The reviewed inputs or training target differ from this checkpoint.")
        target = {"steps": state["total_steps"], "recipe": state["recipe"], "recipe_version": state.get("recipe_version", 1)}
        if state["recipe"] == "image":
            target["epochs"] = job["spec"]["epochs"]
        snapshot = {"action": action, "job_id": job["id"], "recovery_token": job["recovery_token"],
                    "checkpoint_sha256": checkpoint["sha256"], "target": target, "plan_digest": plan["preview_digest"]}
        digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, allow_nan=False).encode()).hexdigest()
        from .performance import continuation_advice
        advice = None
        if action == "continue-guided":
            current = self._resource_preview(job, [job["provider_id"]])
            advice = continuation_advice(job, checkpoint, plan, self.jobs(), bool(current.get("eligible")))
        return {"preview_digest": digest, "provider_id": provider_id, "checkpoint": checkpoint, "target": target,
                "continuation_performance": advice,
                "plan": plan, "requires_stopped_confirmation": job.get("execution_mode") == "guided",
                "optimizer_updates": 0 if action == "finish-delivery" else None}

    @staticmethod
    def _check_recovery_digest(raw, reviewed):
        digest = raw.get("preview_digest")
        if not isinstance(digest, str) or not hmac.compare_digest(digest, reviewed["preview_digest"]):
            raise ValueError("This continuation preview is stale. Review the current checkpoint and plan again.")
        if reviewed["requires_stopped_confirmation"] and raw.get("confirmed_stopped") is not True:
            raise ValueError("Confirm that the previous provider notebook has stopped before preparing a new session.")

    @staticmethod
    def _retain_attempt(job):
        job.setdefault("attempt_history", []).append({key: job.get(key) for key in
            ("provider_id", "execution_mode", "submission_status", "submission_intent", "handle", "submission_resolution", "cleanup_status", "checkpoint")}
            | {"attempt_id": job["spec"].get("attempt_id"), "at": now()})
        for key in ("handle", "submission_intent", "submission_resolution", "remote_terminal", "worker_status", "stop_requested", "error_code", "pending_handoff", "delivery"):
            job.pop(key, None)

    def continue_guided(self, jid, raw):
        with self.lock:
            job = self.job(jid)
            reviewed = self._recovery_preview(job, "continue-guided", raw.get("provider_id"))
            if raw.get("preview") is True:
                return reviewed
            self._check_recovery_digest(raw, reviewed)
            self._retain_attempt(job)
            checkpoint = reviewed["checkpoint"]
            job["spec"].update(guided=True, attempt_id=uuid.uuid4().hex, resume_from=checkpoint["path"],
                               resume_sha256=checkpoint["sha256"], resolved_resource=reviewed["plan"].get("resolved_resource"))
            if job["spec"]["recipe"] == "image":
                job["spec"].update({key: reviewed["plan"]["spec"][key] for key in ("weights_path", "weights_sha256") if key in reviewed["plan"]["spec"]})
            job.update(provider_id=reviewed["provider_id"], execution_mode="guided", run_kind="training", status="awaiting_user",
                       phase="awaiting_handoff", submission_status="prepared", cleanup_status="not_required", connection_status="manual",
                       step=checkpoint["step"], checkpoint={**checkpoint, "verified": True}, plan=reviewed["plan"],
                       limit_enforcement="manual_unverified", message="Recovery verified. Download the continuation notebook and run the remaining target in your provider account.")
            job["reviewed_checkpoint_sha256"] = checkpoint["sha256"]
            job.pop("notebook_preparation", None)
            job.pop("last_summary", None)
            (self.jobs_root / jid / "STOP").unlink(missing_ok=True)
            self._write_json(self.jobs_root / jid / "spec.json", job["spec"])
            self.save_job(job)
            return self.job(jid)

    def finish_delivery(self, jid, raw):
        with self.lock:
            job = self.job(jid)
            reviewed = self._recovery_preview(job, "finish-delivery")
            if raw.get("preview") is True:
                return reviewed
            self._check_recovery_digest(raw, reviewed)
            self._retain_attempt(job)
            job["delivery"] = {"checkpoint": reviewed["checkpoint"], "requested_at": now(), "device": "cpu",
                               "resolved_resource": reviewed["plan"].get("resolved_resource"), "optimizer_updates": 0}
            job.update(provider_id="local", execution_mode="automatic", run_kind="delivery", status="queued", phase="preparing_delivery",
                       submission_status="prepared", cleanup_status="not_required", connection_status="connected",
                       message="Finishing evaluation and model export locally on CPU; training weights will not be updated.")
            (self.jobs_root / jid / "STOP").unlink(missing_ok=True)
            self.save_job(job)
            self._start(jid)
            return job

    def resume(self, jid, providers=None):
        with self.lock:
            if self.environment_busy:
                raise ValueError("Wait for environment preparation to finish before resuming training.")
            job = self.job(jid)
            if job["status"] not in ("paused", "interrupted", "error"):
                raise ValueError("This run is not paused or interrupted.")
            self._assert_stopped(job)
            checkpoint = self.jobs_root / jid / "checkpoint.json"
            if not checkpoint.exists() and not self._pristine_pause(job):
                raise ValueError("No local checkpoint is available. Retrieve the provider's checkpoint before resuming.")
            if checkpoint.exists():
                state = self._validate_checkpoint(job, checkpoint)
                if state["step"] >= state["total_steps"]:
                    raise ValueError("This checkpoint already reached the training target. Verify and export its results.")
            candidate_spec = {**job["spec"]}
            candidate_spec["guided"] = False
            if providers is not None:
                candidate_spec["providers"] = providers
            others = [item for item in self.jobs() if item["id"] != jid]
            normalized = validate_spec(candidate_spec, self.settings()["output_dir"])
            plan = preview(normalized, CATALOG, self.connections(), self.hardware, others)
            plan["spec"] = normalized
            if not plan["eligible"]:
                raise ValueError("No selected resource is currently eligible. Check provider connections and allowances.")
            stop = self.jobs_root / jid / "STOP"
            stop.unlink(missing_ok=True)
            job["spec"] = {**job["spec"], "providers": plan["spec"]["providers"], "guided": False}
            if checkpoint.exists():
                job["spec"]["resume_from"] = str(checkpoint)
                job["spec"]["resume_sha256"] = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
            else:
                job["spec"].pop("resume_from", None)
                job["spec"].pop("resume_sha256", None)
            job.update(status="queued", provider_id=plan["selected_provider"], message="Resuming from the last committed checkpoint.")
            if job.get("submission_intent") or job.get("submission_resolution"):
                job.setdefault("attempt_history", []).append({key: job.get(key) for key in ("submission_intent", "handle", "submission_status", "submission_resolution")})
            for key in ("handle", "submission_intent", "remote_terminal", "stop_requested", "submission_resolution", "error_code", "delivery", "pending_handoff"):
                job.pop(key, None)
            job.update(submission_status="prepared", connection_status="connected", execution_mode="automatic")
            job["run_kind"] = "training"
            job["spec"]["resolved_resource"] = plan.get("resolved_resource")
            job["attempted_providers"] = [job["provider_id"]]
            job.pop("worker_status", None)
            self._write_json(self.jobs_root / jid / "spec.json", job["spec"])
            self.save_job(job)
            self._start(jid)
            return job

    def export(self, jid, output_dir=None):
        from .artifacts import export_verified, verify_export
        from .prediction import inference_capability
        with self.lock:
            job = self.job(jid)
            path = self.jobs_root / jid
            selected = str(Path(output_dir).expanduser().resolve()) if output_dir else job["spec"]["output_dir"]
            destination = Path(selected) / jid
            if destination.resolve().is_relative_to(self.jobs_root.resolve()):
                raise ValueError("Choose an export folder outside the application's internal jobs directory.")
            started = time.monotonic()
            result = export_verified(path, destination)
            job["export_path"] = str(result)
            job["spec"]["output_dir"] = selected
            job["export_status"] = "verified"
            manifest = verify_export(result)
            job["artifacts"] = manifest["files"]
            job["inference"] = inference_capability(result, manifest)
            job["export_verified_at"] = now()
            if job.get("performance"):
                job["performance"].setdefault("controller_stages_seconds", {})["local_export"] = round(time.monotonic() - started, 6)
            if job.get("observation_source") == "notebook_import" and job.get("last_summary"):
                job["last_summary"] = {**job["last_summary"], "required_action": "Model files are verified and saved locally."}
            job["phase"] = "completed"
            job.pop("export_error", None)
            job["message"] = "Model files verified and saved locally. Remote cleanup remains under your control."
            return self.save_job(job)

    def _validate_checkpoint(self, job, checkpoint, delivery=False, contents=None):
        from .artifacts import checkpoint_assets, load_checkpoint, decode_checkpoint, IntegrityError, validate_checkpoint_payload
        from .datasets import load_dataset
        from .imports import frozen_dataset
        state = decode_checkpoint(contents) if contents is not None else load_checkpoint(checkpoint)
        data = frozen_dataset(job) if delivery else None
        if data is None:
            data = load_dataset(job["spec"])
        expected = job.get("fingerprint") or data["fingerprint"]
        if state.get("job_id") != job["id"] or state.get("recipe") != job["spec"]["recipe"] or state.get("total_steps") != job["spec"]["steps"] or state.get("fingerprint") != expected:
            raise IntegrityError("Checkpoint identity or training inputs do not match this run.")
        if data["fingerprint"] != expected:
            raise IntegrityError("Training inputs changed since this run was planned.")
        validate_checkpoint_payload(state, job["spec"], data)
        checkpoint_assets(state, Path(checkpoint).parent)
        return state

    def recover(self, jid, raw):
        from .artifacts import atomic_bytes, load_checkpoint
        with self.lock:
            job = self.job(jid)
            if job["status"] not in TERMINAL or (job.get("submission_status") == "unknown" and not self._manually_resolved(job)):
                raise ValueError("Resolve and stop the previous execution before restoring a checkpoint.")
            self._assert_stopped(job)
            if raw.get("checkpoint", "previous") != "previous":
                raise ValueError("Only the previous verified checkpoint can be restored.")
            path = self.jobs_root / jid
            previous = path / "checkpoint.previous.json"
            state = self._validate_checkpoint(job, previous)
            if raw.get("preview"):
                return {"step": state["step"], "current_step": job.get("step", 0), "progress_loss": max(0, job.get("step", 0) - state["step"])}
            current = path / "checkpoint.json"
            if current.exists():
                atomic_bytes(path / ("checkpoint.replaced-" + uuid.uuid4().hex + ".json"), current.read_bytes())
            atomic_bytes(current, previous.read_bytes())
            job["spec"].update(resume_from=str(current), resume_sha256=hashlib.sha256(current.read_bytes()).hexdigest())
            job.pop("reviewed_checkpoint_sha256", None)
            job.update(status="paused", step=state["step"], checkpoint={"path": str(current), "step": state["step"]}, message="Previous verified checkpoint restored. Review the recovered progress before resuming.")
            return self.save_job(job)

    def cancel(self, jid):
        job = self.job(jid)
        if job["status"] not in ("running", "pausing") or not job.get("handle"):
            raise ValueError("There is no confirmed remote execution to cancel.")
        config = self.connections().get(job["provider_id"], {}).get("config", {})
        result = connectors.cancel(job["provider_id"], config, job["handle"], self.jobs_root / jid)
        job.update(status="pausing", stop_requested=True, message=result.get("detail", "Cancellation requested. Waiting for provider confirmation; uncommitted progress may be lost."))
        return self.save_job(job)

    def clean_cache(self):
        if any(j["status"] in ("queued", "running", "pausing") for j in self.jobs()):
            raise ValueError("Pause training before cleaning downloaded caches.")
        # Keep models and checkpoints; remove only application-owned download copies.
        removed = 0
        from .worker import worker_active
        from .artifacts import _check_path
        for folder in self.jobs_root.iterdir():
            if not folder.is_dir() or folder.is_symlink():
                continue
            if worker_active(folder):
                raise ValueError("Wait for every local worker to stop before cleaning caches.")
            for name in ("model.zip", "notebook.zip", "notebook-bundle.zip", "training.ipynb", "training-payload.zip"):
                target = folder / name
                if target.is_file() and not target.is_symlink() and target.resolve().is_relative_to(self.jobs_root.resolve()):
                    removed += target.stat().st_size
                    target.unlink()
            for name in ("features.pt", "manifest.json"):
                target = folder / "feature-cache" / name
                _check_path(target)
                if target.is_file():
                    removed += target.stat().st_size
                    target.unlink()
        return {"removed_bytes": removed, "message":"Disposable downloads and optional feature caches removed. Models and recovery state were retained."}

    def archive(self, jid, archived=True):
        with self.lock:
            job = self.job(jid)
            if job["status"] not in TERMINAL or (job.get("submission_status") == "unknown" and not self._manually_resolved(job)):
                raise ValueError("Stop and resolve this execution before archiving it.")
            self._assert_stopped(job)
            job["archived"] = bool(archived)
            return self.save_job(job)

    def predict(self, jid, raw):
        from .prediction import predict
        job = self.job(jid)
        if not job.get("export_path"):
            raise ValueError("Verify and export the model before prediction.")
        return predict(Path(job["export_path"]), raw)

    def import_output(self, jid, raw):
        from .imports import import_results
        with self.lock:
            job = self.job(jid)
            if job["status"] not in TERMINAL:
                raise ValueError("Stop this run before importing external results.")
            self._assert_stopped(job)
            source = raw.get("path")
            if not isinstance(source, str) or not source.strip():
                raise ValueError("Enter the local path of your downloaded results folder or ZIP.")
            if not self._manually_resolved(job) and (job.get("submission_status") == "unknown" or (job.get("handle") and not job.get("remote_terminal"))):
                raise ValueError("Resolve the remote execution before replacing local recovery data.")
            result = import_results(Path(source).expanduser(), self.jobs_root / jid, job, preview_only=bool(raw.get("preview")), allow_rollback=raw.get("allow_rollback") is True, expected_digest=raw.get("preview_digest"))
            if raw.get("preview"):
                return result
            job.update(result, message="Notebook results verified and imported locally.")
            job.pop("reviewed_checkpoint_sha256", None)
            job.update(last_import_at=now(), last_import_verified=True)
            imported_summary = job.pop("imported_summary", None)
            job.pop("last_summary", None)
            if imported_summary:
                job["last_summary"] = imported_summary
            if result.get("checkpoint"):
                job["spec"]["resume_from"] = result["checkpoint"]["path"]
                job["spec"]["resume_sha256"] = result["checkpoint"]["sha256"]
                if job["spec"]["recipe"] == "image":
                    from .artifacts import load_checkpoint
                    state = load_checkpoint(Path(result["checkpoint"]["path"]))
                    job["spec"].update(weights_path=str(self.jobs_root / jid / "assets" / "base_weights.pth"),
                                       weights_sha256=state["payload"]["weights_sha256"])
                self._write_json(self.jobs_root / jid / "spec.json", job["spec"])
            job["remote_terminal"] = True
            self.save_job(job)
            if result["status"] == "completed":
                self._auto_export(jid)
            return self.job(jid)

    def bundle(self, jid, provider=None):
        return self._training_asset(jid, provider, "bundle")

    def notebook(self, jid):
        return self._training_asset(jid, None, "notebook")

    def notebook_payload(self, jid):
        return self._training_asset(jid, None, "payload")

    def _training_asset(self, jid, provider, kind):
        with self.lock:
            job = self.job(jid)
            self._assert_stopped(job)
            pid = provider or job["provider_id"]
            if pid == "codespaces":
                raise ValueError("Codespaces is a repository development helper, not a training backend.")
            if pid not in job["spec"]["providers"]:
                raise ValueError("Choose a provider selected for this run.")
            admission = preview({**job["spec"], "providers": [pid], "guided": True}, CATALOG,
                                self.connections(), self.hardware, [other for other in self.jobs() if other["id"] != jid])
            if not admission.get("preparation_eligible"):
                reason = admission["decisions"][0]["reason"] if admission.get("decisions") else "This provider cannot prepare the selected recipe."
                raise ValueError(reason)
            path = self.jobs_root / jid
            checkpoint = path / "checkpoint.json"
            if checkpoint.exists():
                state = self._validate_checkpoint(job, checkpoint)
                if state["step"] >= state["total_steps"]:
                    raise ValueError("This checkpoint already reached the target. Export the finished model.")
                job["spec"]["resume_from"] = str(checkpoint)
                checksum = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
                if job.get("reviewed_checkpoint_sha256") and checksum != job["reviewed_checkpoint_sha256"]:
                    raise ValueError("The checkpoint changed after continuation review. Review this continuation again before downloading.")
                job["spec"]["resume_sha256"] = checksum
                if state["recipe"] == "image":
                    job["spec"].update(weights_path=str(path / "assets" / "base_weights.pth"), weights_sha256=state["payload"]["weights_sha256"])
                self._write_json(path / "spec.json", job["spec"])
                self.save_job(job)
            config = self.connections().get(pid, {}).get("config", {})
            build = {"bundle": connectors.build_bundle, "notebook": connectors.build_notebook,
                     "payload": connectors.build_notebook_payload}[kind]
            started = time.monotonic()
            result = build(pid, config, path / "spec.json", path)
            self._controller_cost(job, "staging", started)
            self.save_job(job)
            if kind == "notebook":
                job["notebook_preparation"] = {"prepared_at": now(),
                    "checkpoint_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest() if checkpoint.exists() else None}
                self.save_job(job)
            return result

    def artifact_zip(self, jid):
        job = self.job(jid)
        if not job.get("export_path"):
            raise ValueError("Export and verify the model before downloading it.")
        source = Path(job["export_path"])
        from .artifacts import verify_export
        manifest = verify_export(source)
        target = self.jobs_root / jid / "model.zip"
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(source / "manifest.json", "manifest.json")
            for item in manifest["files"]:
                file = source / item["path"]
                archive.write(file, item["path"])
        return target

    def logs(self, jid):
        self.job(jid)
        path = self.jobs_root / jid / "worker.log"
        if not path.exists():
            return ""
        with path.open("rb") as handle:
            handle.seek(max(0, path.stat().st_size - 50000))
            return handle.read().decode("utf-8", errors="replace")

    def shutdown(self):
        self.closing.set()
        environment = getattr(self, "environment", None)
        if environment:
            environment.shutdown()
        for jid in list(self.processes):
            (self.jobs_root / jid / "STOP").touch()
        for process in list(self.processes.values()):
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
        for thread in list(self.threads.values()):
            thread.join(timeout=2)
