"""Read-only input inspection shared by preview, imports and portable workers."""
from __future__ import annotations

import ast
import csv
import hashlib
import math
from pathlib import Path
import random

from .artifacts import canonical_json, _check_path

RESNET18_URL = "https://download.pytorch.org/models/resnet18-f37072fd.pth"
RESNET18_ID = "torchvision/resnet18/IMAGENET1K_V1/f37072fd"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}


def prepare_spec(spec: dict) -> dict:
    image = spec.get("recipe") == "image"
    result = {"recipe": "starter", "recipe_version": 2 if image else 1, "steps": 100,
              "learning_rate": 0.001 if image else 0.1, "batch_size": 16 if image else 32,
              "seed": 42, "device": "auto", "runtime_seconds": 3600,
              "checkpoint_interval_seconds": 60, "summary_interval_seconds": 300,
              "heartbeat_interval_seconds": 30, **spec}
    if image:
        result = {"architecture": "resnet18", "epochs": 5, "momentum": 0.9, "image_size": 224, **result}
    if result["recipe"] not in ("starter", "pytorch", "image", "custom"):
        raise ValueError("Choose starter, pytorch, image, or custom recipe")
    if result["recipe_version"] not in (1, 2) or (image and result["recipe_version"] != 2):
        raise ValueError("Unsupported recipe version")
    if result["device"] not in ("auto", "cpu", "cuda"):
        raise ValueError("Device must be auto, cpu, or cuda")
    for key in ("steps", "batch_size", "seed"):
        if type(result[key]) is not int or result[key] < (0 if key == "seed" else 1):
            raise ValueError(f"{key} must be a {'nonnegative' if key == 'seed' else 'positive'} integer")
    for key in ("learning_rate", "checkpoint_interval_seconds", "summary_interval_seconds", "heartbeat_interval_seconds"):
        value = result[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{key} must be a finite positive number")
    if not isinstance(result["runtime_seconds"], (int, float)) or not math.isfinite(result["runtime_seconds"]) or result["runtime_seconds"] < 0:
        raise ValueError("runtime_seconds must be finite and nonnegative")
    if image and (result["architecture"] != "resnet18" or result["image_size"] != 224 or
                  type(result["epochs"]) is not int or not 1 <= result["epochs"] <= 1000 or
                  not isinstance(result["momentum"], (float, int)) or not 0 <= result["momentum"] < 1):
        raise ValueError("Image recipe requires ResNet18, image size 224, 1–1000 epochs, and momentum in [0,1)")
    return result


def stratified_split(labels: list, seed: int) -> tuple[list[int], list[int]]:
    rng = random.Random(seed + 17)
    train, validation = [], []
    for label in sorted(set(labels)):
        indices = [index for index, value in enumerate(labels) if value == label]
        if len(indices) < 2:
            raise ValueError(f"Class {label} needs at least two rows for separate training and validation")
        rng.shuffle(indices)
        count = min(len(indices) - 1, max(1, round(len(indices) * 0.2)))
        validation.extend(indices[:count])
        train.extend(indices[count:])
    rng.shuffle(train)
    return train, sorted(validation)


def _finite_row(row: list[str]) -> list[float]:
    values = [float(value) for value in row]
    if not all(math.isfinite(value) for value in values):
        raise ValueError("nonfinite value")
    return values


def load_tabular(spec: dict) -> dict:
    spec = prepare_spec(spec)
    version = spec["recipe_version"]
    features, labels, columns = [], [], None
    path = Path(spec["dataset_path"]) if spec.get("dataset_path") else None
    if path:
        _check_path(path.absolute())
        if not path.is_file():
            raise ValueError("Choose a readable CSV file")
        if version == 2 and path.stat().st_size > 256 * 1024 * 1024:
            raise ValueError("Classifier CSV exceeds 256 MB; use a custom streaming recipe")
        label_index = None
        with path.open(newline="", encoding="utf-8-sig") as handle:
            for line, row in enumerate(csv.reader(handle), 1):
                if not row or all(not value.strip() for value in row):
                    continue
                if version == 1:
                    try:
                        values = [float(value) for value in row]
                    except ValueError:
                        if line == 1:
                            columns = row
                            continue
                        raise ValueError(f"CSV row {line} must contain numeric features and a binary label") from None
                    label_index = len(values) - 1
                else:
                    if any(not value.strip() for value in row):
                        raise ValueError(f"CSV row {line} contains missing values")
                    if not features and label_index is None:
                        try:
                            _finite_row(row)
                            header = False
                        except ValueError:
                            def nonnumeric(value):
                                try:
                                    float(value)
                                    return False
                                except ValueError:
                                    return True
                            header = all(nonnumeric(value) for value in row)
                            if not header:
                                raise ValueError(f"CSV row {line} mixes text and numeric or nonfinite data; provide a complete text header or numeric rows") from None
                        if header:
                            columns = [value.strip() for value in row]
                            if len(set(columns)) != len(columns):
                                raise ValueError("CSV header names must be unique")
                        else:
                            columns = [f"feature_{i + 1}" for i in range(len(row) - 1)] + ["label"]
                        selected = spec.get("label_column")
                        if selected in (None, ""):
                            label_index = len(columns) - 1
                        elif type(selected) is int or (isinstance(selected, str) and selected.isdigit() and selected not in columns):
                            label_index = int(selected)
                        elif selected in columns:
                            label_index = columns.index(selected)
                        else:
                            raise ValueError(f"Label column {selected!r} was not found; available columns: {', '.join(columns)}")
                        if not 0 <= label_index < len(columns):
                            raise ValueError("Label column index is outside the CSV columns (indices start at 0)")
                        if header:
                            continue
                    try:
                        values = _finite_row(row)
                    except ValueError:
                        raise ValueError(f"CSV row {line} requires finite numeric features and a 0/1 label") from None
                if len(values) < 2 or not all(math.isfinite(value) for value in values) or values[label_index] not in (0.0, 1.0):
                    raise ValueError(f"CSV row {line} needs finite features and a binary 0/1 label")
                if features and len(values) != len(features[0]) + 1:
                    raise ValueError(f"CSV row {line} has a different number of columns")
                if columns is not None and len(values) != len(columns):
                    raise ValueError(f"CSV row {line} does not match the header")
                features.append(values[:label_index] + values[label_index + 1:])
                labels.append(values[label_index])
                if len(features) > 1_000_000 or (version == 2 and len(features) * (len(features[0]) * 40 + 96) > 512 * 1024 * 1024):
                    raise ValueError("Dataset exceeds the in-memory classifier limit; use a custom streaming recipe")
        if not features:
            raise ValueError("Dataset contains no training rows")
        if columns is None:
            columns = [f"feature_{i + 1}" for i in range(len(features[0]))] + ["label"]
            label_index = len(columns) - 1
    else:
        rng = random.Random(spec["seed"] + 1)
        features = [[rng.uniform(-1, 1) for _ in range(4)] for _ in range(512)]
        labels = [float(2 * row[0] - row[1] + 0.5 * row[2] - 0.2 * row[3] > 0) for row in features]
        columns, label_index = ["feature_1", "feature_2", "feature_3", "feature_4", "label"], 4
    train, validation = stratified_split(labels, spec["seed"]) if version == 2 else (list(range(len(labels))), [])
    raw_hash = hashlib.sha256(canonical_json([features, labels])).hexdigest()
    schema = {"kind": "tabular_binary", "features": [name for i, name in enumerate(columns) if i != label_index],
              "label_column": columns[label_index], "label_index": label_index, "classes": [0, 1]}
    content_hash = raw_hash if version == 1 else hashlib.sha256(canonical_json({"values": raw_hash, "schema": schema})).hexdigest()
    warnings = ["Generated demonstration data; validation results do not establish quality on your task."] if path is None else []
    if version == 2 and set(labels) != {0.0, 1.0}:
        raise ValueError("Binary classification requires both label 0 and label 1")
    counts = {str(int(label)): labels.count(label) for label in sorted(set(labels))}
    return {"features": features, "labels": labels, "train_indices": train, "validation_indices": validation,
            "content_hash": content_hash, "warnings": warnings,
            "metadata": {"kind": "tabular_binary", "source": "csv" if path else "synthetic", "rows": len(features),
                         "feature_count": len(features[0]), "columns": columns, "label_column": columns[label_index],
                         "class_counts": counts, "train_rows": len(train), "validation_rows": len(validation),
                         "sha256": content_hash, "estimated_memory_bytes": len(features) * (len(features[0]) * 40 + 96),
                         "estimated_batch_bytes": spec["batch_size"] * (len(features[0]) + 1) * 4, "input_schema": schema}}


def _file_hash(path: Path) -> str:
    _check_path(path.absolute())
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def weights_info(spec: dict) -> dict:
    path = Path(spec.get("weights_path") or Path.home() / ".cache" / "multigpu" / "models" / "resnet18-f37072fd.pth")
    result = {"ready": False, "path": str(path.absolute()), "identity": RESNET18_ID}
    try:
        if not path.is_file():
            result["error"] = "Prepare official ResNet18 weights before starting this recipe"
            return result
        digest = _file_hash(path)
        if not digest.startswith("f37072fd") or (spec.get("weights_sha256") and spec["weights_sha256"] != digest):
            raise ValueError("Cached ResNet18 weights failed their SHA-256 identity check")
        result.update(ready=True, sha256=digest)
    except (OSError, ValueError) as exc:
        result["error"] = str(exc)
    return result


def load_image_dataset(spec: dict) -> dict:
    spec = prepare_spec(spec)
    path = Path(spec.get("dataset_path") or "")
    _check_path(path.absolute())
    if not spec.get("dataset_path") or not path.is_dir():
        raise ValueError("Select a folder containing one subfolder per image class")
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError as exc:
        raise ValueError("Install Pillow to inspect and validate image datasets") from exc
    classes = sorted(folder.name for folder in path.iterdir() if folder.is_dir() and not folder.name.startswith("."))
    if len(classes) < 2:
        raise ValueError("Image classification needs at least two class subfolders")
    entries, warnings = [], []
    for label, name in enumerate(classes):
        folder = path / name
        _check_path(folder.absolute())
        count = 0
        for file in sorted(folder.rglob("*")):
            _check_path(file.absolute())
            if not file.is_file() or file.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            try:
                with Image.open(file) as image:
                    if image.width * image.height > 40_000_000:
                        raise ValueError("image exceeds 40 million pixels")
                    image.load()
                    width, height = image.size
            except (OSError, ValueError, UnidentifiedImageError) as exc:
                raise ValueError(f"Unreadable image {file.relative_to(path)}: {exc}") from exc
            entries.append({"path": file.relative_to(path).as_posix(), "label": label, "sha256": _file_hash(file),
                            "size_bytes": file.stat().st_size, "width": width, "height": height})
            count += 1
            if len(entries) > 50_000:
                raise ValueError("Image recipe supports at most 50000 images; use a custom recipe for larger datasets")
        if count < 5:
            raise ValueError(f"Class {name!r} has {count} readable images; at least 5 per class are required")
    labels = [item["label"] for item in entries]
    train, validation = stratified_split(labels, spec["seed"])
    digest = hashlib.sha256(canonical_json({"classes": classes, "files": entries})).hexdigest()
    if len({entry["sha256"] for entry in entries}) < len(entries):
        warnings.append("Duplicate image contents were detected; validation may be optimistic")
    weights = weights_info(spec)
    if not weights["ready"]:
        warnings.append(weights.get("error", "Prepare official model weights before training"))
    total_steps = spec["epochs"] * math.ceil(len(train) / spec["batch_size"])
    return {"entries": entries, "labels": labels, "train_indices": train, "validation_indices": validation,
            "content_hash": digest, "warnings": warnings,
            "metadata": {"kind": "image_class_directory", "source": "folder", "rows": len(entries), "classes": classes,
                         "class_counts": {name: labels.count(i) for i, name in enumerate(classes)},
                         "train_rows": len(train), "validation_rows": len(validation), "sha256": digest,
                         "total_steps": total_steps, "epochs": spec["epochs"], "weights": weights,
                         "estimated_memory_bytes": len(entries) * 256, "estimated_batch_bytes": spec["batch_size"] * 3 * 224 * 224 * 4,
                         "input_schema": {"kind": "image", "classes": classes, "image_size": 224, "color_mode": "RGB",
                                          "resize": 256, "crop": "center", "mean": [0.485, 0.456, 0.406], "std": [0.229, 0.224, 0.225]}}}


def _custom_dataset(spec: dict) -> dict:
    path = Path(spec.get("script_path") or "")
    _check_path(path.absolute())
    if not spec.get("script_path") or not path.is_file() or path.suffix.lower() != ".py":
        raise ValueError("Custom training needs a readable .py script defining train(ctx)")
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    if not any(isinstance(node, ast.FunctionDef) and node.name == "train" for node in tree.body):
        raise ValueError("Custom Python script must define a top-level train(ctx) function")
    hashes = {"script": _file_hash(path)}
    if spec.get("dataset_path"):
        data = Path(spec["dataset_path"])
        if data.is_dir() and spec["recipe_version"] == 2:
            hashes["dataset"] = hashlib.sha256(canonical_json([
                [file.relative_to(data).as_posix(), _file_hash(file)] for file in sorted(data.rglob("*")) if file.is_file()
            ])).hexdigest()
        elif data.is_file():
            hashes["dataset"] = _file_hash(data)
        else:
            raise ValueError("Custom dataset path is not a readable file or directory")
    return {"content_hash": hashlib.sha256(canonical_json(hashes)).hexdigest(),
            "warnings": ["Custom scripts define their own data schema, dependencies, evaluation and checkpoint contents."],
            "metadata": {"kind": "custom", "source": "custom", "sha256": hashes.get("dataset"), "script_sha256": hashes["script"]}}


def fingerprint(spec: dict, content_hash: str, metadata: dict | None = None) -> str:
    spec = prepare_spec(spec)
    config = {key: spec[key] for key in ("recipe", "steps", "learning_rate", "batch_size", "seed")}
    if spec["recipe_version"] == 2:
        config.update(recipe_version=2, validation_split="seeded-stratified-80-20-v1")
    if spec["recipe"] == "image":
        config.update(steps=(metadata or {})["total_steps"], architecture="resnet18", epochs=spec["epochs"],
                      momentum=spec["momentum"], image_size=224, weights_identity=RESNET18_ID)
    return hashlib.sha256(canonical_json({"config": config, "data": content_hash})).hexdigest()


def load_dataset(spec: dict) -> dict:
    spec = prepare_spec(spec)
    data = _custom_dataset(spec) if spec["recipe"] == "custom" else load_image_dataset(spec) if spec["recipe"] == "image" else load_tabular(spec)
    data["fingerprint"] = fingerprint(spec, data["content_hash"], data["metadata"])
    return data


def inspect_dataset(spec: dict) -> dict:
    try:
        data = load_dataset(spec)
        return {"errors": [], "warnings": data["warnings"], "dataset": data["metadata"], "fingerprint": data["fingerprint"]}
    except (OSError, ValueError, TypeError, IndexError, SyntaxError) as exc:
        return {"errors": [str(exc)], "warnings": [], "dataset": {}, "fingerprint": None}


def fingerprint_for_spec(spec: dict) -> str:
    inspected = inspect_dataset(spec)
    if inspected["errors"]:
        raise ValueError("; ".join(inspected["errors"]))
    return inspected["fingerprint"]
