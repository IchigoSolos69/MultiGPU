"""Atomic checkpoints and portable exports with explicit integrity checks."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import random
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
    if path.stat().st_size > 64 * 1024 * 1024:
        raise IntegrityError("Checkpoint exceeds the 64 MB portable-state limit")
    with path.open("rb") as handle:
        return decode_checkpoint(handle.read(64 * 1024 * 1024 + 1))


def decode_checkpoint(contents: bytes) -> dict:
    """Validate the exact bytes that a caller will commit or hash."""
    if len(contents) > 64 * 1024 * 1024:
        raise IntegrityError("Checkpoint exceeds the 64 MB portable-state limit")
    try:
        envelope = json.loads(contents)
        state = envelope["state"]
        expected = envelope["manifest"]["state_sha256"]
        if envelope["format"] != "multigpu-checkpoint-v1" or not isinstance(state, dict):
            raise IntegrityError("Unsupported checkpoint format")
        if hashlib.sha256(canonical_json(state)).hexdigest() != expected:
            raise IntegrityError("Checkpoint SHA-256 mismatch")
        return state
    except IntegrityError:
        raise
    except (KeyError, TypeError, ValueError, RecursionError) as exc:
        raise IntegrityError("Invalid checkpoint manifest") from exc


def validate_checkpoint_payload(state: dict, spec: dict | None = None, dataset: dict | None = None) -> dict:
    """Validate JSON recipe state without importing Torch or loading a GPU model."""
    spec, dataset = spec or {}, dataset or {}
    metadata = dataset.get("metadata", {})
    def require(condition, message):
        if not condition:
            raise IntegrityError("Invalid checkpoint payload: " + message)
    def number(value):
        try:
            return type(value) in (int, float) and math.isfinite(value)
        except OverflowError:
            return False
    def vector(value, length):
        return isinstance(value, list) and len(value) == length and all(number(item) for item in value)
    def matrix(value, rows, columns):
        return isinstance(value, list) and len(value) == rows and all(vector(row, columns) for row in value)
    require(isinstance(state, dict), "state must be an object")
    total, step = state.get("total_steps"), state.get("step")
    require(type(total) is int and total > 0 and type(step) is int and 0 <= step <= total, "step is outside the training target")
    require(state.get("loss") is None or number(state["loss"]), "loss must be finite")
    require(number(state.get("elapsed_seconds", 0)) and state.get("elapsed_seconds", 0) >= 0, "elapsed time is invalid")
    recipe = state.get("recipe")
    require(recipe in ("starter", "pytorch", "image", "custom"), "unknown recipe")
    require(not spec or recipe == spec.get("recipe", "starter"), "recipe differs from the run")
    require("steps" not in spec or total == spec["steps"], "training target differs from the run")
    version = state.get("recipe_version", 1)
    require(type(version) is int and version in (1, 2), "unsupported recipe version")
    require(version == spec.get("recipe_version", version), "recipe version differs from the run")
    payload = state.get("payload")
    require(isinstance(payload, dict), "custom and built-in state must be JSON objects")
    if recipe == "custom":
        return state  # User-defined keys and model formats remain under the script's control.
    rng = payload.get("rng_state")
    require(isinstance(rng, (list, tuple)) and len(rng) == 3 and isinstance(rng[1], (list, tuple)) and len(rng[1]) == 625,
            "Python random state is missing or malformed")
    try:
        random.Random().setstate((rng[0], tuple(rng[1]), rng[2]))
    except (TypeError, ValueError, OverflowError):
        require(False, "Python random state is invalid")
    split = payload.get("split", {})
    if version == 2:
        require(isinstance(split, dict), "data split is missing")
        train, validation = split.get("train_indices"), split.get("validation_indices")
        require(isinstance(train, list) and isinstance(validation, list) and bool(train) and bool(validation), "training and validation splits are required")
        indices = train + validation
        require(len(indices) <= 1_000_000 and all(type(i) is int and i >= 0 for i in indices) and len(set(indices)) == len(indices), "split indices must be unique nonnegative integers")
        require(set(indices) == set(range(len(indices))), "split indices do not cover the dataset")
        if dataset and not dataset.get("frozen_metadata"):
            require(train == dataset.get("train_indices") and validation == dataset.get("validation_indices"), "saved data split differs from this run")
        elif dataset:
            require(len(indices) == metadata.get("rows") and len(train) == metadata.get("train_rows")
                    and len(validation) == metadata.get("validation_rows"), "saved split sizes differ from this run")
    if recipe in ("starter", "pytorch"):
        if recipe == "pytorch":
            require(isinstance(payload.get("architecture"), dict), "MLP architecture is missing")
        count = payload.get("feature_count") if recipe == "starter" else payload.get("architecture", {}).get("features")
        require(type(count) is int and 0 < count <= 1_000_000, "feature count is invalid")
        if metadata:
            require(count == metadata.get("feature_count"), "feature count differs from the dataset")
        if version == 2:
            normalization = payload.get("standardization", {})
            require(isinstance(normalization, dict) and vector(normalization.get("mean"), count) and vector(normalization.get("std"), count)
                    and all(value > 0 for value in normalization["std"]), "feature standardization is missing or invalid")
            if metadata:
                require(normalization == metadata["input_schema"]["standardization"], "standardization differs from the training split")
    if recipe == "starter":
        require(vector(payload.get("weights"), count) and number(payload.get("bias")), "linear weight dimensions or bias are invalid")
        optimizer = payload.get("optimizer", {})
        require(isinstance(optimizer, dict) and optimizer.get("name") == "SGD" and number(optimizer.get("learning_rate")), "SGD optimizer state is invalid")
        require("learning_rate" not in spec or optimizer["learning_rate"] == spec["learning_rate"], "optimizer learning rate differs from the run")
        return state
    for key in ("torch_rng", "cuda_rng"):
        values = payload.get(key, [])
        require(isinstance(values, list) and len(values) <= 100_000 and all(type(item) is int and 0 <= item <= 255 for item in values), key + " must contain RNG bytes")
        if key == "torch_rng":
            require(len(values) in (5048, 5056), "CPU Torch random state has an invalid size")
    if recipe == "pytorch":
        architecture = payload.get("architecture", {})
        require(architecture.get("name") == "binary-mlp" and architecture.get("hidden") == 16, "MLP architecture is invalid")
        model = payload.get("model", {})
        require(isinstance(model, dict) and set(model) == {"0.weight", "0.bias", "2.weight", "2.bias"}
                and matrix(model["0.weight"], 16, count) and vector(model["0.bias"], 16)
                and matrix(model["2.weight"], 1, 16) and vector(model["2.bias"], 1), "MLP tensor dimensions are invalid")
        shapes, momentum = [(16, count), (16,), (1, 16), (1,)], 0
    else:
        head = payload.get("head", {})
        require(payload.get("image_feature_cache", False) == spec.get("image_feature_cache", False), "feature-cache training mode differs from the run")
        classes = len(metadata.get("classes", [])) or (len(head.get("bias", [])) if isinstance(head, dict) else 0)
        require(version == 2 and 2 <= classes <= 50_000 and isinstance(head, dict) and set(head) == {"weight", "bias"}
                and matrix(head["weight"], classes, 512) and vector(head["bias"], classes), "image head dimensions are invalid")
        from .datasets import RESNET18_SHA256
        require(payload.get("weights_sha256") == RESNET18_SHA256 and payload.get("weights_asset") == "assets/base_weights.pth", "pretrained asset identity is invalid")
        require(not dataset or payload.get("dataset_manifest_sha256") == dataset.get("content_hash"), "image manifest differs from the dataset")
        epoch, cursor, order = payload.get("epoch"), payload.get("cursor"), payload.get("order")
        require(type(epoch) is int and epoch >= 0 and type(cursor) is int and isinstance(order, list) and 0 <= cursor <= len(order), "image training cursor is invalid")
        require(all(type(i) is int for i in order) and (not order or set(order) == set(split["train_indices"])) and len(order) == len(set(order)), "image order does not match the training split")
        if spec.get("batch_size"):
            batch = spec["batch_size"]
            require(cursor == len(order) or cursor % batch == 0, "image cursor falls inside a batch")
            expected = epoch * math.ceil(len(split["train_indices"]) / batch) + math.ceil(cursor / batch)
            require(expected == step and (step == 0 or bool(order)), "image cursor does not match the saved step")
        require("epochs" not in spec or epoch < spec["epochs"], "image epoch exceeds the target")
        shapes, momentum = [(classes, 512), (classes,)], spec.get("momentum", .9)
    optimizer = payload.get("optimizer", {})
    require(isinstance(optimizer, dict) and isinstance(optimizer.get("state"), dict) and isinstance(optimizer.get("param_groups"), list)
            and len(optimizer["param_groups"]) == 1, "optimizer groups are invalid")
    group = optimizer["param_groups"][0]
    require(isinstance(group, dict) and group.get("params") == list(range(len(shapes))) and number(group.get("lr"))
            and ("learning_rate" not in spec or group["lr"] == spec["learning_rate"]), "optimizer parameters or learning rate are invalid")
    require(group.get("momentum", 0) == momentum and group.get("dampening", 0) == 0 and group.get("weight_decay", 0) == 0
            and not group.get("nesterov", False) and not group.get("maximize", False), "optimizer settings differ from the recipe")
    states = optimizer["state"]
    require(all(str(key) in {str(i) for i in range(len(shapes))} and isinstance(value, dict) for key, value in states.items()), "optimizer parameter state is invalid")
    if not momentum:
        require(not states, "this recipe has no momentum state")
    else:
        require(step == 0 or {str(key) for key in states} == {"0", "1"}, "image momentum state is missing")
        for key, values in states.items():
            shape = shapes[int(key)]
            value = values.get("momentum_buffer")
            require(set(values) == {"momentum_buffer"} and (matrix(value, *shape) if len(shape) == 2 else vector(value, shape[0])), "momentum tensor dimensions are invalid")
    return state


def checkpoint_assets(state: dict, source: Path) -> dict[str, Path]:
    """Locate immutable companions in a run folder or a standalone model export."""
    if state.get("recipe") != "image":
        return {}
    payload = state.get("payload", {})
    from .datasets import RESNET18_SHA256
    if payload.get("weights_asset") != "assets/base_weights.pth" or payload.get("weights_sha256") != RESNET18_SHA256:
        raise IntegrityError("Checkpoint pretrained asset identity is invalid")
    source = Path(source).absolute()
    for asset in (source / "assets" / "base_weights.pth", source / "base_weights.pth", source / "export" / "base_weights.pth"):
        _check_path(asset)
        if asset.is_file():
            with asset.open("rb") as handle:
                if hashlib.file_digest(handle, "sha256").hexdigest() == RESNET18_SHA256:
                    return {"assets/base_weights.pth": asset}
            raise IntegrityError("Checkpoint base weights failed SHA-256 verification")
    raise IntegrityError("Image checkpoint is missing assets/base_weights.pth; download the complete results folder")


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
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (ValueError, RecursionError) as exc:
        raise IntegrityError("Invalid export manifest") from exc
    if not isinstance(manifest, dict) or manifest.get("format") != "multigpu-export-v1" or not isinstance(manifest.get("files"), list):
        raise IntegrityError("Unsupported export manifest")
    if not manifest["files"] or len(manifest["files"]) > 20000 or not isinstance(manifest.get("metadata", {}), dict):
        raise IntegrityError("Invalid export file list or metadata")
    seen = set()
    for item in manifest["files"]:
        if (not isinstance(item, dict) or not isinstance(item.get("path"), str) or type(item.get("size_bytes")) is not int
                or item["size_bytes"] < 0 or not isinstance(item.get("sha256"), str) or len(item["sha256"]) != 64
                or any(c not in "0123456789abcdef" for c in item["sha256"])):
            raise IntegrityError("Invalid export file entry")
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
        validate_checkpoint_payload(state)
        checkpoint_assets(state, checkpoint.parent)
        payload = canonical_json(state)
        files["checkpoint.json"] = canonical_json({"format": "multigpu-checkpoint-v1", "state": state,
            "manifest": {"algorithm": "sha256", "state_sha256": hashlib.sha256(payload).hexdigest()}})
    result = export_artifacts(files, destination, manifest.get("metadata", {}), expected_hashes=expected)
    verify_export(result)
    return result
