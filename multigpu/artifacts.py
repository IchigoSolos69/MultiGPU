"""Atomic checkpoints and portable exports with explicit integrity checks."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import uuid


class IntegrityError(ValueError):
    pass


def _check_path(path: Path) -> None:
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise IntegrityError(f"Symbolic links and junctions are not allowed: {part}")


def directory(path: Path) -> Path:
    path = Path(path).absolute()
    _check_path(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _space(path: Path, required: int) -> None:
    if shutil.disk_usage(path).free < required + 1024 * 1024:
        raise OSError("Insufficient free disk space for a safe artifact write")


def atomic_bytes(path: Path, data: bytes) -> None:
    path = Path(path).absolute()
    directory(path.parent)
    _check_path(path)
    _space(path.parent, len(data))
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_json(path: Path, value: object) -> None:
    atomic_bytes(path, canonical_json(value))


def load_checkpoint(path: Path) -> dict:
    path = Path(path).absolute()
    _check_path(path)
    if not path.is_file():
        raise IntegrityError(f"Checkpoint does not exist: {path}")
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        state = envelope["state"]
        expected = envelope["manifest"]["state_sha256"]
        if envelope["format"] != "multigpu-checkpoint-v1" or not isinstance(state, dict):
            raise IntegrityError("Unsupported checkpoint format")
        if hashlib.sha256(canonical_json(state)).hexdigest() != expected:
            raise IntegrityError("Checkpoint SHA-256 mismatch")
        return state
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise IntegrityError("Invalid checkpoint manifest") from exc


def write_checkpoint(output_dir: Path, state: dict) -> Path:
    output_dir = directory(output_dir)
    path = output_dir / "checkpoint.json"
    data = canonical_json(state)
    envelope = {
        "format": "multigpu-checkpoint-v1",
        "manifest": {"algorithm": "sha256", "state_sha256": hashlib.sha256(data).hexdigest()},
        "state": state,
    }
    if path.exists():
        load_checkpoint(path)
        atomic_bytes(output_dir / "checkpoint.previous.json", path.read_bytes())
    atomic_json(path, envelope)
    load_checkpoint(path)
    return path


def _relative(name: str) -> Path:
    normalized = str(name).replace("\\", "/")
    value = PurePosixPath(normalized)
    if (not normalized or value.is_absolute() or any(p in ("", ".", "..") for p in normalized.split("/"))
            or ":" in normalized or any(ord(c) < 32 for c in normalized)):
        raise IntegrityError(f"Unsafe artifact path: {name}")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if any(part.endswith((".", " ")) or part.split(".")[0].upper() in reserved for part in value.parts):
        raise IntegrityError(f"Non-portable artifact path: {name}")
    return Path(*value.parts)


def verify_export(path: Path) -> dict:
    path = Path(path).absolute()
    _check_path(path)
    manifest_path = path / "manifest.json"
    _check_path(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format") != "multigpu-export-v1" or not isinstance(manifest.get("files"), list):
        raise IntegrityError("Unsupported export manifest")
    seen = set()
    for item in manifest["files"]:
        relative = _relative(item["path"])
        if relative.as_posix().casefold() in seen or relative.as_posix().casefold() == "manifest.json":
            raise IntegrityError("Duplicate or reserved export path")
        seen.add(relative.as_posix().casefold())
        file = path / relative
        _check_path(file)
        if not file.is_file() or file.stat().st_size != item["size_bytes"]:
            raise IntegrityError(f"Artifact missing or size mismatch: {relative}")
        with file.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        if digest != item["sha256"]:
            raise IntegrityError(f"Artifact SHA-256 mismatch: {relative}")
    return manifest


def export_artifacts(files: dict[str, bytes | str | Path], destination: Path, metadata: dict | None = None,
                     *, expected_hashes: dict[str, str] | None = None) -> Path:
    destination = Path(destination).absolute()
    directory(destination.parent)
    _check_path(destination)
    entries = []
    prepared = []
    for name, content in files.items():
        relative = _relative(name)
        if relative.as_posix().casefold() == "manifest.json":
            raise IntegrityError("manifest.json is reserved")
        if isinstance(content, Path):
            _check_path(content.absolute())
            if not content.is_file():
                raise IntegrityError(f"Artifact source is not a regular file: {content}")
            size = content.stat().st_size
        else:
            content = content.encode("utf-8") if isinstance(content, str) else content
            if not isinstance(content, bytes):
                raise TypeError("Artifact contents must be bytes, text, or a Path")
            size = len(content)
        prepared.append((relative, content))
        entries.append(size)
    if not prepared:
        raise IntegrityError("An export must contain at least one artifact")
    _space(destination.parent, sum(entries) * 2)
    staging = directory(destination.parent / f".export-{uuid.uuid4().hex}")
    manifest_files = []
    try:
        for relative, content in prepared:
            target = staging / relative
            directory(target.parent)
            if isinstance(content, Path):
                shutil.copyfile(content, target)
            else:
                atomic_bytes(target, content)
            with target.open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
            if expected_hashes and relative.as_posix() in expected_hashes and digest != expected_hashes[relative.as_posix()]:
                raise IntegrityError(f"Source artifact changed during export: {relative}")
            manifest_files.append({"path": relative.as_posix(), "size_bytes": target.stat().st_size, "sha256": digest})
        atomic_json(staging / "manifest.json", {
            "format": "multigpu-export-v1", "algorithm": "sha256", "metadata": metadata or {}, "files": manifest_files,
        })
        verify_export(staging)
        previous = None
        if destination.exists():
            if not destination.is_dir():
                raise IntegrityError("Export destination is not a directory")
            previous = destination.with_name(f"{destination.name}.previous-{uuid.uuid4().hex}")
            os.replace(destination, previous)
        try:
            os.replace(staging, destination)
        except OSError:
            if previous is not None:
                os.replace(previous, destination)
            raise
        return destination
    finally:
        # The staging path is freshly created by this function and never supplied by the user.
        if staging.exists():
            _check_path(staging)
            if staging.resolve().parent != destination.parent.resolve():
                raise IntegrityError("Refusing to remove staging outside the export directory")
            shutil.rmtree(staging)


def export_verified(source_job_dir: Path, destination: Path) -> Path:
    """Copy a finished job's verified model files to the user's local folder."""
    source = Path(source_job_dir).absolute() / "export"
    manifest = verify_export(source)
    files = {item["path"]: source / _relative(item["path"]) for item in manifest["files"]}
    expected = {item["path"]: item["sha256"] for item in manifest["files"]}
    checkpoint = source.parent / "checkpoint.json"
    if checkpoint.exists():
        state = load_checkpoint(checkpoint)
        payload = canonical_json(state)
        files["checkpoint.json"] = canonical_json({"format": "multigpu-checkpoint-v1", "state": state,
            "manifest": {"algorithm": "sha256", "state_sha256": hashlib.sha256(payload).hexdigest()}})
    result = export_artifacts(files, destination, manifest.get("metadata", {}), expected_hashes=expected)
    verify_export(result)
    return result
