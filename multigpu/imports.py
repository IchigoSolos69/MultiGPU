"""Verify notebook results before importing them into a local run."""
import json
import hashlib
import hmac
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import uuid
import zipfile

from .artifacts import (IntegrityError, _check_path, atomic_bytes, checkpoint_assets, directory,
                        export_artifacts, load_checkpoint, decode_checkpoint, validate_checkpoint_payload, verify_export)


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
        if preview_only:
            return {"preview_digest": digest, "step": step, "current_step": current_step, "rollback_required": step < current_step, "status": "completed" if manifest else "paused", "verified": True}
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
        return {"status": "completed" if manifest else "paused", "step": step,
                "loss": state.get("loss") if state else manifest["metadata"].get("loss"),
                "device": state.get("device") if state else manifest["metadata"].get("device"),
                "evaluation": manifest.get("metadata", {}).get("evaluation", {}) if manifest else job.get("evaluation", {}),
                "checkpoint": {"path": str(job_dir / "checkpoint.json"), "step": step} if checkpoint else None}
    finally:
        if staging.exists():
            if staging.resolve().parent != job_dir.resolve():
                raise IntegrityError("Refusing cleanup outside this run's import directory.")
            shutil.rmtree(staging)
