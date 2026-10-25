"""Reuse one verified prepared notebook/payload per job; never remote storage."""
import hashlib
import json
from pathlib import Path

from .artifacts import _check_path, atomic_json, canonical_json


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def asset_key(provider_id, spec_path):
    spec_path = Path(spec_path)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    assets, size = [], 0
    for key in ("dataset_path", "script_path", "resume_from", "weights_path"):
        if not spec.get(key):
            continue
        source = Path(spec[key]).expanduser()
        if not source.is_absolute():
            source = spec_path.parent / source
        _check_path(source.absolute())
        if not source.exists():
            raise ValueError("A selected staging input no longer exists")
        if source.is_dir() and key != "dataset_path":
            raise ValueError("Only a dataset input can be a directory")
        files = sorted(source.rglob("*")) if source.is_dir() else [source]
        for file in files:
            _check_path(file.absolute())
            if file.is_file():
                size += file.stat().st_size
                if size > 512 * 1024 * 1024:
                    raise ValueError("Selected inputs exceed this adapter's 512 MiB staging limit")
                assets.append([key, str(file.relative_to(source)) if source.is_dir() else file.name, file_hash(file)])
    code = [[name, file_hash(Path(__file__).parent / name)] for name in
            ("connectors.py", "worker.py", "artifacts.py", "datasets.py", "recipes.py", "prediction.py", "notebook_runtime.py", "performance.py", "image_pipeline.py", "staging.py")]
    return hashlib.sha256(canonical_json({"provider": provider_id, "spec": spec, "assets": assets, "code": code})).hexdigest()


def reusable(target, key):
    target = Path(target)
    _check_path(target.absolute())
    meta = target.with_suffix(target.suffix + ".cache.json")
    _check_path(meta.absolute())
    try:
        saved = json.loads(meta.read_text(encoding="utf-8"))
        limit = 8 * 1024 * 1024 if target.suffix == ".ipynb" else 512 * 1024 * 1024
        return (isinstance(saved, dict) and target.is_file() and target.stat().st_size < limit and saved.get("key") == key
                and saved.get("sha256") == file_hash(target))
    except (OSError, ValueError):
        return False


def remember(target, key):
    target = Path(target)
    try:
        atomic_json(target.with_suffix(target.suffix + ".cache.json"), {"key": key, "sha256": file_hash(target)})
    except OSError:
        pass  # The verified training file remains usable without a cache sidecar.
