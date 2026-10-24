"""Verify notebook results before importing them into a local run."""
import json
import hashlib
import hmac
import math
import os
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
import shutil
import stat
import uuid
import zipfile

from .artifacts import (IntegrityError, _check_path, atomic_bytes, canonical_json, checkpoint_assets, directory,
                        export_artifacts, load_checkpoint, decode_checkpoint, validate_checkpoint_payload, verify_export)

_HISTORY_BYTES = 2 * 1024 * 1024
_HISTORY_EVENTS = 2500


def _timestamp(value):
    if not isinstance(value, str) or len(value) > 80:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc).isoformat() if parsed.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def _import_history(base, job, step, checkpoint_sha256):
    """Optional observations never drive lifecycle state or invalidate a model."""
    warnings, events, reported_at = [], [], None
    candidates = list(base.rglob("events.jsonl"))
    if not candidates:
        return events, warnings, reported_at
    if len(candidates) != 1:
        return events, ["Training history was skipped because the results contain multiple event logs."], None
    try:
        path = candidates[0]
        _check_path(path)
        with path.open("rb") as handle:
            size = path.stat().st_size
            if size > _HISTORY_BYTES:
                handle.seek(size - _HISTORY_BYTES)
                handle.readline(65537)
                warnings.append("Training history exceeded 2 MiB; only its recent bounded tail was imported.")
            content = handle.read(_HISTORY_BYTES)
    except (OSError, ValueError):
        return events, ["Training history could not be read; the verified model and checkpoint were retained."], None
    rejected, seen = 0, set()
    target = job["spec"]["steps"]
    def finite(value):
        return type(value) in (int, float) and math.isfinite(value)
    for raw in content.splitlines():
        if len(raw) > 65536:
            rejected += 1
            continue
        try:
            item = json.loads(raw)
            if not isinstance(item, dict):
                raise ValueError("Invalid event")
            kind = item.get("type")
            if kind not in {"metric", "summary", "heartbeat", "checkpoint"}:
                continue
            timestamp, current = _timestamp(item.get("timestamp")), item.get("step")
            if (item.get("job_id") != job["id"] or type(current) is not int or not 0 <= current <= step
                    or item.get("total_steps") != target or timestamp is None
                    or item.get("loss") is not None and not finite(item["loss"])):
                raise ValueError("History identity or progress is invalid")
            event = {"type": kind, "job_id": job["id"], "step": current, "total_steps": target,
                     "timestamp": timestamp, "loss": item.get("loss")}
            attempt = item.get("attempt_id")
            if attempt is None or isinstance(attempt, str) and len(attempt) <= 128:
                event["attempt_id"] = attempt
            else:
                raise ValueError("Invalid attempt")
            if isinstance(item.get("device"), str) and len(item["device"]) <= 64:
                event["device"] = item["device"]
            if finite(item.get("elapsed_seconds")) and item["elapsed_seconds"] >= 0:
                event["elapsed_seconds"] = item["elapsed_seconds"]
            if kind == "summary":
                for key in ("checkpoint_step", "checkpoint_age_seconds", "remaining_session_seconds", "active_gpu_count"):
                    value = item.get(key)
                    if value is None or finite(value) and value >= 0:
                        event[key] = value
                for key in ("allowance_confidence", "required_action"):
                    value = item.get(key)
                    if isinstance(value, str):
                        from .connectors import redact_provider_output
                        event[key] = redact_provider_output(value[:500])
            if kind == "metric" and isinstance(item.get("metrics"), dict):
                event["metrics"] = {str(key)[:80]: value for key, value in list(item["metrics"].items())[:30] if finite(value)}
            identity = hashlib.sha256(canonical_json(event)).hexdigest()
            if identity in seen:
                continue
            seen.add(identity)
            event["history_id"] = identity
            event["provenance"] = {"source": "notebook_import", "provider_id": job["provider_id"], "checkpoint_sha256": checkpoint_sha256}
            events.append(event)
            if kind == "checkpoint" and current == step:
                reported_at = max(reported_at or timestamp, timestamp)
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
            rejected += 1
    if rejected:
        warnings.append(f"Skipped {rejected} malformed or incompatible history records; model verification was unaffected.")
    if len(events) > _HISTORY_EVENTS:
        events = events[-_HISTORY_EVENTS:]
        warnings.append("Training history was limited to the most recent 2500 valid observations.")
    return events, warnings, reported_at


def _publish_history(job_dir, events, warnings):
    if not events:
        return
    path = job_dir / "imported-history.jsonl"
    try:
        existing = []
        if path.is_file() and path.stat().st_size <= 4 * _HISTORY_BYTES:
            _check_path(path)
            for line in path.read_bytes().splitlines():
                try:
                    item = json.loads(line)
                    if isinstance(item, dict) and item.get("history_id"):
                        existing.append(item)
                except (ValueError, RecursionError):
                    pass
        combined = {event["history_id"]: event for event in [*existing, *events]}
        ordered = sorted(combined.values(), key=lambda item: item.get("timestamp", ""))[-_HISTORY_EVENTS:]
        lines, size = [], 0
        for event in reversed(ordered):
            line = canonical_json(event) + b"\n"
            if size + len(line) > _HISTORY_BYTES:
                warnings.append("Saved training history was limited to its most recent 2 MiB.")
                break
            lines.append(line)
            size += len(line)
        atomic_bytes(path, b"".join(reversed(lines)))
    except (OSError, ValueError, TypeError, RecursionError):
        warnings.append("The model was imported, but optional training history could not be saved locally.")


def frozen_dataset(job):
    """Use recorded input identity for delivery; resuming still reads actual inputs."""
    saved = job.get("input_snapshot") or {}
    report = job.get("plan", {}).get("preflight", {})
    fingerprint = job.get("fingerprint")
    metadata = saved.get("metadata") if saved.get("fingerprint") == fingerprint else None
    if not metadata and report.get("fingerprint") == fingerprint:
        metadata = report.get("dataset")
    if not fingerprint or not metadata:
        return None
    return {"fingerprint": fingerprint, "metadata": metadata, "content_hash": metadata.get("sha256"), "frozen_metadata": True}


def _safe_parts(name):
    name = name.replace("\\", "/")
    parts = PurePosixPath(name)
    if parts.is_absolute() or ".." in parts.parts or ":" in name or any(ord(c) < 32 for c in name):
        raise IntegrityError("Archive contains an unsafe file path.")
    return parts


def _unpack(source, staging):
    with zipfile.ZipFile(source) as archive:
        infos = archive.infolist()
        if len(infos) > 20000:
            raise IntegrityError("Archive contains too many files.")
        if sum(i.file_size for i in infos) + 1024 * 1024 > shutil.disk_usage(staging).free:
            raise OSError("Insufficient local disk space to import this archive.")
        for info in infos:
            relative = _safe_parts(info.filename)
            if stat.S_ISLNK(info.external_attr >> 16):
                raise IntegrityError("Archive symbolic links are not allowed.")
            destination = staging.joinpath(*relative.parts)
            if info.is_dir():
                directory(destination)
                continue
            directory(destination.parent)
            with archive.open(info) as src, destination.open("xb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)


def import_results(source, job_dir, job, *, preview_only=False, allow_rollback=False, expected_digest=None):
    source, job_dir = Path(source).absolute(), Path(job_dir).absolute()
    _check_path(source)
    staging = directory(job_dir / ("import-" + uuid.uuid4().hex))
    try:
        if source.is_file() and source.suffix.lower() == ".zip":
            _unpack(source, staging)
            base = staging
        elif source.is_dir():
            base = source
        else:
            raise ValueError("Select a downloaded results folder or ZIP archive.")
        for item in base.rglob("*"):
            _check_path(item)
        manifests = []
        for candidate in base.rglob("manifest.json"):
            if candidate.is_symlink():
                raise IntegrityError("Symbolic links are not allowed in result folders.")
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                if data.get("format") == "multigpu-export-v1":
                    manifests.append(candidate)
            except (ValueError, OSError):
                continue
        if len(manifests) > 1:
            raise IntegrityError("Choose a single run's result folder, not multiple model exports.")
        checkpoint_candidates = list(base.rglob("checkpoint.json"))
        if len(checkpoint_candidates) > 1:
            # A model export can include the same checkpoint as the enclosing run.
            checkpoint_candidates.sort(key=lambda p: len(p.parts))
            if len({hashlib.sha256(p.read_bytes()).hexdigest() for p in checkpoint_candidates}) != 1:
                raise IntegrityError("Results contain different checkpoints. Select one session's output folder.")
        checkpoint = checkpoint_candidates[0] if checkpoint_candidates else None
        checkpoint_bytes = None
        if checkpoint:
            with checkpoint.open("rb") as handle:
                checkpoint_bytes = handle.read(64 * 1024 * 1024 + 1)
        state = decode_checkpoint(checkpoint_bytes) if checkpoint_bytes is not None else None
        if not manifests and state is None:
            raise IntegrityError("No verified MultiGPU model export or checkpoint was found.")
        spec = job["spec"]
        if state and (state.get("job_id") != job["id"] or state.get("recipe") != spec["recipe"] or state.get("total_steps") != spec["steps"]):
            raise IntegrityError("The checkpoint does not match this run's recipe and target.")
        if state and not 0 <= state.get("step", -1) <= spec["steps"]:
            raise IntegrityError("The checkpoint reports an invalid training step.")
        manifest = verify_export(manifests[0].parent) if manifests else None
        from .datasets import load_dataset
        # Completed outputs carry their input identity; source data is only needed
        # for checkpoint-only recovery or older runs without recorded metadata.
        data = frozen_dataset(job) if manifest else None
        if data is None:
            data = load_dataset(spec)
        expected_fingerprint = job.get("fingerprint") or data["fingerprint"]
        if data["fingerprint"] != expected_fingerprint:
            raise IntegrityError("Training inputs changed since this run was planned. Current recovery data was preserved.")
        if state and state.get("fingerprint") != expected_fingerprint:
            raise IntegrityError("Checkpoint training inputs or configuration changed. Current recovery data was preserved.")
        if state:
            validate_checkpoint_payload(state, spec, data)
        assets = checkpoint_assets(state, checkpoint.parent) if state else {}
        if manifest:
            metadata = manifest.get("metadata", {})
            if metadata.get("job_id") != job["id"] or metadata.get("recipe") != spec["recipe"]:
                raise IntegrityError("This model export belongs to a different training run.")
            if metadata.get("fingerprint", expected_fingerprint) != expected_fingerprint or metadata.get("recipe_version", spec.get("recipe_version", 1)) != spec.get("recipe_version", 1):
                raise IntegrityError("This model export uses different training inputs or a different recipe version.")
            if type(metadata.get("step")) is not int or not 0 <= metadata["step"] <= spec["steps"] or (state and metadata["step"] != state["step"]):
                raise IntegrityError("Model export and checkpoint progress do not match this run.")
        step = state.get("step", 0) if state else manifest["metadata"].get("step", 0)
        current = job_dir / "checkpoint.json"
        current_bytes = None
        if current.exists():
            with current.open("rb") as handle:
                current_bytes = handle.read(64 * 1024 * 1024 + 1)
        current_hash = hashlib.sha256(current_bytes).hexdigest() if current_bytes is not None else None
        current_step = job.get("step", 0)
        if current.exists():
            try:
                current_step = max(current_step, decode_checkpoint(current_bytes)["step"])
            except (IntegrityError, OSError):
                pass
        digest_data = {"job": job["id"], "current": current_hash, "checkpoint": hashlib.sha256(checkpoint_bytes).hexdigest() if checkpoint_bytes is not None else None, "manifest": manifest, "step": step}
        digest = hashlib.sha256(json.dumps(digest_data, sort_keys=True, allow_nan=False).encode()).hexdigest()
        checkpoint_hash = hashlib.sha256(checkpoint_bytes).hexdigest() if checkpoint_bytes is not None else None
        history, history_warnings, reported_at = _import_history(base, job, step, checkpoint_hash)
        saved_at = _timestamp(state.get("saved_at")) if state else None
        reported_at = saved_at or reported_at
        if preview_only:
            return {"preview_digest": digest, "step": step, "current_step": current_step, "rollback_required": step < current_step, "status": "completed" if manifest else "paused", "verified": True,
                    "warnings": history_warnings, "history_events": len(history), "checkpoint_reported_at": reported_at}
        if expected_digest is not None and (not isinstance(expected_digest, str) or not hmac.compare_digest(expected_digest, digest)):
            raise IntegrityError("Import preview is stale. Verify these results again before importing.")
        if step < current_step and not allow_rollback:
            raise IntegrityError("This import would roll progress backwards. Preview it and explicitly allow rollback first.")
        if manifest and step < spec["steps"]:
            raise IntegrityError("Export progress does not reach the requested training target.")
        if manifest:
            files = {item["path"]: manifests[0].parent.joinpath(*_safe_parts(item["path"]).parts) for item in manifest["files"]}
            export_artifacts(files, job_dir / "export", metadata,
                             expected_hashes={item["path"]: item["sha256"] for item in manifest["files"]})
        if checkpoint:
            for name, asset in assets.items():
                contents = asset.read_bytes()
                if hashlib.sha256(contents).hexdigest() != state["payload"]["weights_sha256"]:
                    raise IntegrityError("Checkpoint base weights changed during import")
                target = job_dir.joinpath(*_safe_parts(name).parts)
                if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != state["payload"]["weights_sha256"]:
                    atomic_bytes(target, contents)
            if current.exists():
                try:
                    previous_state = decode_checkpoint(current_bytes)
                    validate_checkpoint_payload(previous_state, spec, data)
                    if previous_state.get("job_id") != job["id"] or previous_state.get("fingerprint") != expected_fingerprint:
                        raise IntegrityError("Existing checkpoint belongs to a different run")
                    atomic_bytes(job_dir / "checkpoint.previous.json", current_bytes)
                except IntegrityError:
                    atomic_bytes(job_dir / ("checkpoint.replaced-" + uuid.uuid4().hex + ".json"), current_bytes)
            atomic_bytes(job_dir / "checkpoint.json", checkpoint_bytes)
        verified_at = datetime.now(timezone.utc).isoformat()
        _publish_history(job_dir, history, history_warnings)
        summaries = [event for event in history if event["type"] == "summary"]
        last_summary = {**summaries[-1], "summary_kind": "import"} if summaries else None
        elapsed = last_summary.get("elapsed_seconds") if last_summary else None
        if elapsed is None and state:
            elapsed = state.get("elapsed_seconds")
        return {"status": "completed" if manifest else "paused", "step": step,
                "elapsed_seconds": elapsed,
                "loss": state.get("loss") if state else manifest["metadata"].get("loss"),
                "device": state.get("device") if state else manifest["metadata"].get("device"),
                "evaluation": manifest.get("metadata", {}).get("evaluation", {}) if manifest else job.get("evaluation", {}),
                "checkpoint": {"path": str(job_dir / "checkpoint.json"), "step": step, "sha256": checkpoint_hash,
                               "at": reported_at or verified_at, "verified_at": verified_at, "verified": True,
                               "timestamp_source": "worker_reported" if reported_at else "local_verification", "location": "local"} if checkpoint else None,
                "imported_at": verified_at, "observation_source": "notebook_import",
                "history_provenance": {"source": "notebook_import", "imported_at": verified_at, "checkpoint_sha256": checkpoint_hash, "event_count": len(history)},
                "imported_history_warning": " ".join(history_warnings), "imported_summary": last_summary}
    finally:
        if staging.exists():
            if staging.resolve().parent != job_dir.resolve():
                raise IntegrityError("Refusing cleanup outside this run's import directory.")
            shutil.rmtree(staging)
