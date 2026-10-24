"""Portable notebook recovery and result delivery; no cloud account access."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid
import zipfile

from .artifacts import (IntegrityError, _check_path, atomic_bytes, atomic_json,
                        canonical_json, checkpoint_assets, directory,
                        load_checkpoint, validate_checkpoint_payload, verify_export)
from .datasets import load_dataset, prepare_spec


class RecoveryPersistenceError(RuntimeError):
    """A local checkpoint exists, but its requested durable copy failed."""


def job_key(spec):
    identity = str(spec.get("id") or hashlib.sha256(canonical_json(spec)).hexdigest())
    label = re.sub(r"[^a-zA-Z0-9_-]", "-", identity)[:32] or "legacy"
    return label + "-" + hashlib.sha256(identity.encode()).hexdigest()[:12]


def _checkpoint(path, spec, fingerprint):
    state = load_checkpoint(path)
    if (state.get("job_id") != spec.get("id") or state.get("fingerprint") != fingerprint
            or state.get("recipe") != spec["recipe"]):
        raise IntegrityError("Checkpoint belongs to a different job or training input")
    validate_checkpoint_payload(state, spec)
    _assets(state, Path(path).parent, spec)
    return state


def _assets(state, folder, spec):
    try:
        return checkpoint_assets(state, folder)
    except IntegrityError:
        # Staged checkpoint and prepared image weights can be separate payload inputs.
        from .datasets import RESNET18_SHA256
        asset = Path(spec.get("weights_path") or "")
        if (state.get("recipe") == "image" and state.get("payload", {}).get("weights_sha256") == RESNET18_SHA256
                and asset.is_file()):
            _check_path(asset)
            if hashlib.sha256(asset.read_bytes()).hexdigest() == RESNET18_SHA256:
                return {"assets/base_weights.pth": asset}
        raise


def _recovery_root(spec):
    value = spec.get("recovery_dir")
    if not value:
        return None
    root = Path(value).expanduser().absolute()
    _check_path(root)
    if not root.is_dir():
        raise RecoveryPersistenceError("The configured recovery folder is unavailable. Mount it before resuming.")
    return root / job_key(spec)


def mirror_checkpoint(output_dir, spec):
    """Commit an immutable verified copy only when the user selected recovery storage."""
    if not spec.get("recovery_dir"):
        return None
    try:
        output = Path(output_dir).absolute()
        source = output / "checkpoint.json"
        saved = load_checkpoint(source)
        state = _checkpoint(source, spec, spec.get("fingerprint") or saved.get("fingerprint"))
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        parent = directory(_recovery_root(spec) / "checkpoints")
        if parent.is_relative_to(output) or output.is_relative_to(parent):
            raise IntegrityError("Recovery storage must be outside the session output folder")
        target = parent / (f"{state['step']:012d}-" + digest)
        if target.exists():
            _committed_checkpoint(target, spec, state["fingerprint"])
            return target
        pending = directory(parent / (".pending-" + uuid.uuid4().hex))
        atomic_bytes(pending / "checkpoint.json", source.read_bytes())
        for name, asset in checkpoint_assets(state, output).items():
            atomic_bytes(pending / name, asset.read_bytes())
        history = output / "events.jsonl"
        if history.is_file():
            _check_path(history)
            atomic_bytes(pending / "events.jsonl", history.read_bytes())
        _checkpoint(pending / "checkpoint.json", spec, state["fingerprint"])
        files = {p.relative_to(pending).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in pending.rglob("*") if p.is_file()}
        atomic_json(pending / "commit.json", {"format": "multigpu-recovery-v1", "files": files})
        _committed_checkpoint(pending, spec, state["fingerprint"])
        os.replace(pending, target)
        _committed_checkpoint(target, spec, state["fingerprint"])
        return target
    except Exception as exc:
        raise RecoveryPersistenceError("The checkpoint was saved locally, but its configured recovery copy failed. "
                                       "Training must pause; restore the mounted folder before resuming.") from exc


def _committed_checkpoint(folder, spec, fingerprint):
    _check_path(folder)
    commit = json.loads((folder / "commit.json").read_text(encoding="utf-8"))
    if (not isinstance(commit, dict) or commit.get("format") != "multigpu-recovery-v1"
            or not isinstance(commit.get("files"), dict) or "checkpoint.json" not in commit["files"]
            or not 1 <= len(commit["files"]) <= 20000
            or any(not isinstance(name, str) or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)
                   for name, digest in commit["files"].items())):
        raise IntegrityError("Recovery copy has no valid commit marker")
    for name, digest in commit["files"].items():
        path = folder / name
        _check_path(path)
        if not path.resolve().is_relative_to(folder.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise IntegrityError("Recovery copy failed its checksum")
    return _checkpoint(folder / "checkpoint.json", spec, fingerprint)


@contextmanager
def _session_lock(folder):
    # This lock covers selection as well as the worker, so two notebook cells cannot reset one run.
    from .worker import _file_lock
    path = folder / "notebook.lock"
    _check_path(path)
    with path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        try:
            _file_lock(handle)
        except OSError:
            raise RuntimeError("This notebook job is already running; wait for its worker to stop") from None
        try:
            yield
        finally:
            _file_lock(handle, unlock=True)


def launch_notebook(payload_dir, session_root, recovery_dir=None):
    """Resume this job's newest verified checkpoint, or reuse its completed export."""
    base, root = Path(payload_dir).absolute(), Path(session_root).absolute()
    spec = json.loads((base / "spec.json").read_text(encoding="utf-8"))
    key = job_key(spec)
    for name in ("dataset_path", "script_path", "resume_from", "weights_path"):
        if spec.get(name):
            value = Path(spec[name])
            spec[name] = str(value if value.is_absolute() else base / value)
    spec = prepare_spec(spec)
    data = load_dataset(spec)
    fingerprint = data["fingerprint"]
    if spec.get("fingerprint") and spec["fingerprint"] != fingerprint:
        raise IntegrityError("Training inputs differ from the prepared notebook")
    spec["fingerprint"] = fingerprint
    if spec["recipe"] == "image":
        spec["steps"] = data["metadata"]["total_steps"]
    # Recovery is a runtime-only user choice. A payload can never opt the user into Drive access.
    spec.pop("recovery_dir", None)
    if recovery_dir is not None:
        spec["recovery_dir"] = str(Path(recovery_dir).expanduser().absolute())
    folder = directory(root / "multigpu-runs" / key)
    output = directory(folder / "output")
    with _session_lock(folder):
        from .worker import worker_active
        if worker_active(output):
            raise RuntimeError("A worker already owns this job's output; do not rerun it")
        provenance = {"format": "multigpu-notebook-session-v1", "job_id": spec.get("id"),
                      "recipe": spec["recipe"], "recipe_version": spec["recipe_version"],
                      "fingerprint": fingerprint, "total_steps": spec["steps"]}
        marker = output / "notebook-session.json"
        if marker.exists() and json.loads(marker.read_text()) != provenance:
            raise IntegrityError("This job's session belongs to another training target; no files were overwritten")
        atomic_json(marker, provenance)
        if spec.get("resume_from"):
            source = Path(spec["resume_from"])
            if spec.get("resume_sha256") and hashlib.sha256(source.read_bytes()).hexdigest() != spec["resume_sha256"]:
                raise IntegrityError("The prepared recovery checkpoint changed; download the notebook again")
            _checkpoint(source, spec, fingerprint)
        export = output / "export"
        if export.exists():
            manifest = verify_export(export)
            metadata = manifest.get("metadata", {})
            if any(metadata.get(k) != provenance[k] for k in ("job_id", "recipe", "recipe_version", "fingerprint")):
                raise IntegrityError("Existing export belongs to another training job; no files were overwritten")
            if metadata.get("step") == spec["steps"]:
                print("Reusing this job's verified completed model:", export, flush=True)
                return output
        candidates, rejected = [], []
        paths = [output / "checkpoint.json", output / "checkpoint.previous.json"]
        if spec.get("resume_from"):
            paths.append(Path(spec["resume_from"]))
        recovery = _recovery_root(spec)
        if recovery and (recovery / "checkpoints").is_dir():
            for copy in (recovery / "checkpoints").iterdir():
                if copy.is_dir() and not copy.name.startswith("."):
                    try:
                        state = _committed_checkpoint(copy, spec, fingerprint)
                        candidates.append((state["step"], state.get("elapsed_seconds", 0), copy / "checkpoint.json"))
                    except (ValueError, OSError, KeyError):
                        continue  # An unfinished or damaged copy never replaces a committed recovery point.
        for path in paths:
            if not path.exists():
                continue
            try:
                identity = load_checkpoint(path)
            except (ValueError, OSError):
                rejected.append(path)
                continue
            if identity.get("job_id") != spec.get("id") or identity.get("fingerprint") != fingerprint:
                raise IntegrityError("Existing checkpoint belongs to another job or target; no files were overwritten")
            try:
                state = _checkpoint(path, spec, fingerprint)
                candidates.append((state["step"], state.get("elapsed_seconds", 0), path))
            except (ValueError, OSError):
                rejected.append(path)
        if not candidates and rejected:
            raise IntegrityError("No compatible verified checkpoint remains; existing files were preserved")
        if candidates:
            _, _, selected = max(candidates, key=lambda item: (item[0], item[1]))
            # Preserve a corrupt current file before allowing write_checkpoint to rotate valid history.
            current = output / "checkpoint.json"
            if current in rejected:
                os.replace(current, output / ("checkpoint.rejected-" + uuid.uuid4().hex + ".json"))
            if selected != current:
                state = _checkpoint(selected, spec, fingerprint)
                for name, asset in _assets(state, selected.parent, spec).items():
                    atomic_bytes(output / name, asset.read_bytes())
                atomic_bytes(current, selected.read_bytes())
                history = selected.parent / "events.jsonl"
                if history.is_file() and not (output / "events.jsonl").exists():
                    atomic_bytes(output / "events.jsonl", history.read_bytes())
            spec["resume_from"] = str(current)
            spec["resume_sha256"] = hashlib.sha256(current.read_bytes()).hexdigest()
        else:
            spec.pop("resume_from", None)
            spec.pop("resume_sha256", None)
        (output / "STOP").unlink(missing_ok=True)
        runtime = folder / "runtime-spec.json"
        atomic_json(runtime, spec)
        env = {**os.environ, "MULTIGPU_OUTPUT_DIR": str(output), "MULTIGPU_SPEC_PATH": str(runtime),
               "PYTHONPATH": str(base) + os.pathsep + os.environ.get("PYTHONPATH", "")}
        result = subprocess.run([sys.executable, str(base / "run.py")], cwd=root, env=env, check=False)
        if result.returncode:
            raise RuntimeError("Training worker failed. The job's checkpoint and technical logs were preserved.")
        return output


def package_results(output_dir):
    """Deliver only the current verified model/recovery point, never backup export trees or locks."""
    output = Path(output_dir).absolute()
    _check_path(output)
    with _session_lock(output.parent):
        from .worker import worker_active
        if worker_active(output):
            raise RuntimeError("Pause this notebook worker before packaging its results")
        provenance = json.loads((output / "notebook-session.json").read_text())
        spec = {"id": provenance["job_id"], "recipe": provenance["recipe"],
                "recipe_version": provenance["recipe_version"], "steps": provenance["total_steps"]}
        files = {"notebook-session.json": output / "notebook-session.json"}
        for name in ("checkpoint.json", "checkpoint.previous.json"):
            path = output / name
            if path.is_file():
                state = _checkpoint(path, spec, provenance["fingerprint"])
                files[name] = path
                files.update(checkpoint_assets(state, output))
        if (output / "export").is_dir():
            manifest = verify_export(output / "export")
            metadata = manifest.get("metadata", {})
            if any(metadata.get(k) != provenance[k] for k in ("job_id", "recipe", "recipe_version", "fingerprint")):
                raise IntegrityError("Export belongs to another notebook job; no results were packaged")
            files["export/manifest.json"] = output / "export" / "manifest.json"
            for item in manifest["files"]:
                files["export/" + item["path"]] = output / "export" / item["path"]
        if not any(name in files for name in ("checkpoint.json", "export/manifest.json")):
            raise IntegrityError("No verified checkpoint or model exists to download")
        for name in ("events.jsonl", "connector-state.json"):
            if (output / name).is_file():
                _check_path(output / name)
                files[name] = output / name
        target = output.parent / "multigpu-results.zip"
        temporary = target.with_suffix(".zip.tmp")
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, path in files.items():
                _check_path(path)
                archive.write(path, name)
        os.replace(temporary, target)
        return target


def publish_progress(output_dir, cursor):
    """Publish immutable event chunks and a small current snapshot before a Modal volume commit."""
    output = Path(output_dir)
    events = output / "events.jsonl"
    if not events.is_file():
        return
    with events.open("rb") as stream:
        stream.seek(cursor.get("offset", 0))
        content = stream.read()
    complete = content.rfind(b"\n") + 1
    if not complete:
        return
    lines = content[:complete].splitlines()
    for start in range(0, len(lines), 500):
        chunk = lines[start:start + 500]
        number = cursor.get("history_count", 0)
        atomic_bytes(output / "history" / f"{number:08d}.jsonl", b"\n".join(chunk) + b"\n")
        cursor["history_count"] = number + 1
    cursor["offset"] = cursor.get("offset", 0) + complete
    latest = json.loads(lines[-1])
    atomic_json(output / "progress.json", {"format": "multigpu-progress-v1",
                                           "history_count": cursor["history_count"], "latest": latest})
