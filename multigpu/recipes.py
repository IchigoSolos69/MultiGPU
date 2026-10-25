"""Built-in training recipes and explicit preparation of official model assets."""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
from pathlib import Path
import random
import tempfile
import urllib.request

from .artifacts import atomic_bytes, canonical_json, directory
from .datasets import RESNET18_URL, RESNET18_ID, RESNET18_SHA256, weights_info
from .prediction import classification_metrics, inference_files, sigmoid, tabular_probability


def tuples(value):
    return tuple(tuples(item) for item in value) if isinstance(value, list) else value


def _torch(ctx):
    try:
        import torch
    except ImportError as exc:
        raise RuntimeError("PyTorch is not installed. Prepare the selected environment or use the CPU starter.") from exc
    if ctx.spec["device"] == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but PyTorch cannot access a CUDA GPU")
    use_cuda = ctx.spec["device"] != "cpu" and torch.cuda.is_available()
    ctx.gpu_index = None
    if use_cuda:
        index = ctx.spec.get("gpu_index", 0) if ctx.spec.get("execution_provider", "local") == "local" else 0
        if type(index) is not int or not 0 <= index < torch.cuda.device_count():
            raise RuntimeError("The selected GPU index is unavailable in this training runtime")
        torch.cuda.set_device(index)
        ctx.gpu_index, ctx.device = index, f"cuda:{index}"
    else:
        ctx.device = "cpu"
    torch.manual_seed(ctx.spec["seed"])
    ctx._torch_runtime = torch
    if use_cuda:
        torch.cuda.reset_peak_memory_stats(ctx.device)
    return torch


def _evaluate_tabular(model: dict, data: dict) -> dict:
    indices = data["validation_indices"] or list(range(len(data["labels"])))
    probabilities = [tabular_probability(model, data["features"][i], standardize=False) for i in indices]
    result = classification_metrics([[1 - value, value] for value in probabilities], [int(data["labels"][i]) for i in indices], [0, 1])
    result["split"] = "validation" if data["validation_indices"] else "training_only_legacy"
    result["warning"] = "Held-out validation is a small estimate, not a guarantee on future data." if data["validation_indices"] else "Legacy recipe has no held-out validation; these are training-set metrics."
    return result


def _finish_tabular(ctx, model, data):
    ctx.phase = "evaluating"
    schema = data["metadata"]["input_schema"]
    if schema.get("standardization"):
        model["standardization"] = schema["standardization"]
    ctx.evaluation = _evaluate_tabular(model, data)
    ctx.export({"model.json": canonical_json(model), "evaluation.json": canonical_json(ctx.evaluation),
                "input_schema.json": canonical_json(schema),
                "training.json": canonical_json({"steps": ctx.step, "final_batch_loss": ctx.loss,
                    "feature_count": len(schema["features"]), "dataset_rows": len(data["labels"]),
                    "learning_rate": ctx.spec["learning_rate"], "seed": ctx.spec["seed"], "recipe_version": ctx.spec["recipe_version"]}),
                "README.txt": "Read evaluation.json for held-out metrics and input_schema.json for exact feature order.\n"
                              "Run: python predict.py --features '[0.1, 0.2, 0.3, 0.4]' (replace with your feature count).\n"
                              "Built-in tabular prediction needs only Python. Generated data is a demonstration, not task validation.\n",
                **inference_files()})


def train_starter(ctx, data):
    if ctx.spec["device"] == "cuda":
        raise ValueError("The dependency-free starter runs on CPU. Select the PyTorch or image recipe for CUDA.")
    ctx.device = "cpu"
    x, y, train = data["features"], data["labels"], data["train_indices"]
    rng = random.Random(ctx.spec["seed"])
    weights, bias = [0.0] * len(x[0]), 0.0
    if ctx.resume_state:
        weights, bias = ctx.resume_state["weights"], ctx.resume_state["bias"]
        rng.setstate(tuples(ctx.resume_state["rng_state"]))
    def state():
        result = {"weights": weights, "bias": bias, "rng_state": rng.getstate(),
                  "optimizer": {"name": "SGD", "learning_rate": ctx.spec["learning_rate"]},
                  "sampling": "seeded sampling with replacement", "feature_count": len(x[0])}
        if ctx.spec["recipe_version"] == 2:
            result["split"] = {"train_indices": train, "validation_indices": data["validation_indices"]}
            result["standardization"] = data["metadata"]["input_schema"]["standardization"]
        return result
    ctx._state_factory = state
    ctx.checkpoint(state())
    while ctx.step < ctx.spec["steps"] and not ctx.should_stop():
        gradient, bias_gradient, loss = [0.0] * len(weights), 0.0, 0.0
        for _ in range(ctx.spec["batch_size"]):
            index = train[rng.randrange(len(train))]
            score = sum(weight * value for weight, value in zip(weights, x[index])) + bias
            probability = sigmoid(score)
            loss += max(score, 0) - score * y[index] + math.log1p(math.exp(-abs(score)))
            error = probability - y[index]
            for column, value in enumerate(x[index]):
                gradient[column] += error * value
            bias_gradient += error
        scale = ctx.spec["learning_rate"] / ctx.spec["batch_size"]
        weights = [weight - scale * value for weight, value in zip(weights, gradient)]
        bias -= scale * bias_gradient
        ctx.report(loss / ctx.spec["batch_size"])
        ctx.performance.samples += ctx.spec["batch_size"]
        ctx.maybe_checkpoint()
    ctx.checkpoint(state())
    if ctx.step == ctx.spec["steps"]:
        _finish_tabular(ctx, {"format": "multigpu-linear-binary-v1", "weights": weights, "bias": bias}, data)


def train_pytorch(ctx, data):
    ctx.phase = "preparation"
    torch = _torch(ctx)
    x, y, train = data["features"], data["labels"], data["train_indices"]
    # Keep the same Python sampler; construct host tensors only once.
    host_features = torch.tensor(x, dtype=torch.float32)
    host_labels = torch.tensor(y, dtype=torch.float32)
    rng = random.Random(ctx.spec["seed"])
    model = torch.nn.Sequential(torch.nn.Linear(len(x[0]), 16), torch.nn.Tanh(), torch.nn.Linear(16, 1)).to(ctx.device)
    optimizer = torch.optim.SGD(model.parameters(), lr=ctx.spec["learning_rate"])
    if ctx.resume_state:
        model.load_state_dict({key: torch.tensor(value, dtype=model.state_dict()[key].dtype) for key, value in ctx.resume_state["model"].items()})
        saved = ctx.resume_state["optimizer"]
        saved["state"] = {int(key): value for key, value in saved["state"].items()}
        optimizer.load_state_dict(saved)
        rng.setstate(tuples(ctx.resume_state["rng_state"]))
        torch.set_rng_state(torch.tensor(ctx.resume_state["torch_rng"], dtype=torch.uint8))
        if ctx.gpu_index is not None and ctx.resume_state.get("cuda_rng"):
            torch.cuda.set_rng_state(torch.tensor(ctx.resume_state["cuda_rng"], dtype=torch.uint8), device=ctx.device)
    def state():
        result = {"model": {key: value.detach().cpu().tolist() for key, value in model.state_dict().items()},
                  "optimizer": optimizer.state_dict(), "rng_state": rng.getstate(), "torch_rng": torch.get_rng_state().tolist(),
                  "cuda_rng": torch.cuda.get_rng_state(device=ctx.device).tolist() if ctx.gpu_index is not None else [],
                  "architecture": {"name": "binary-mlp", "features": len(x[0]), "hidden": 16}, "sampling": "seeded sampling with replacement"}
        if ctx.spec["recipe_version"] == 2:
            result["split"] = {"train_indices": train, "validation_indices": data["validation_indices"]}
            result["standardization"] = data["metadata"]["input_schema"]["standardization"]
        return result
    ctx._state_factory = state
    ctx.checkpoint(state())
    # Keep the dataset in host memory; only the current minibatch uses GPU memory.
    ctx.phase = "training"
    while ctx.step < ctx.spec["steps"] and not ctx.should_stop():
        indices = [train[rng.randrange(len(train))] for _ in range(ctx.spec["batch_size"])]
        features = host_features[indices].to(ctx.device)
        labels = host_labels[indices].to(ctx.device)
        optimizer.zero_grad(set_to_none=True)
        loss = torch.nn.functional.binary_cross_entropy_with_logits(model(features).squeeze(-1), labels)
        loss.backward()
        optimizer.step()
        ctx.report(loss.detach().item())
        ctx.performance.samples += len(indices)
        ctx.maybe_checkpoint()
    ctx.checkpoint(state())
    if ctx.step == ctx.spec["steps"]:
        _finish_tabular(ctx, {"format": "multigpu-pytorch-mlp-v1", **state()}, data)


def prepare_image_weights(cache_dir: Path, allow_download=False, cancelled=None) -> dict:
    def check_cancelled():
        if cancelled is not None and cancelled():
            raise InterruptedError("Model preparation was cancelled")
    check_cancelled()
    cache = directory(cache_dir)
    target = cache / "resnet18-f37072fd.pth"
    found = weights_info({"weights_path": str(target)})
    check_cancelled()
    if found["ready"]:
        return {"weights_path": found["path"], "weights_sha256": found["sha256"], "weights_identity": RESNET18_ID}
    if not allow_download:
        raise ValueError(found.get("error", "Official model weights must be prepared first"))
    if target.exists():
        raise ValueError("Cached model weights are corrupt; remove or quarantine that file before preparing again")
    import shutil
    if shutil.disk_usage(cache).free < 100 * 1024 * 1024:
        raise OSError("At least 100 MB free space is required to prepare official model weights")
    descriptor, temporary = tempfile.mkstemp(prefix=".resnet18-", suffix=".download", dir=cache)
    try:
        digest, size = hashlib.sha256(), 0
        with os.fdopen(descriptor, "wb") as output, urllib.request.urlopen(RESNET18_URL, timeout=10) as response:
            while True:
                check_cancelled()
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > 60 * 1024 * 1024:
                    raise ValueError("Official model download exceeded its expected size")
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        check_cancelled()
        if digest.hexdigest() != RESNET18_SHA256:
            raise ValueError("Official ResNet18 download failed its SHA-256 check")
        check_cancelled()
        os.replace(temporary, target)
        return {"weights_path": str(target.absolute()), "weights_sha256": digest.hexdigest(), "weights_identity": RESNET18_ID}
    finally:
        Path(temporary).unlink(missing_ok=True)


def train_image(ctx, data):
    ctx.phase = "preparation"
    torch = _torch(ctx)
    try:
        from PIL import Image
        from torchvision.models import resnet18, ResNet18_Weights
    except (ImportError, RuntimeError) as exc:
        raise RuntimeError("Image training requires compatible torchvision and Pillow; prepare the image environment first") from exc
    info = weights_info(ctx.spec)
    if not info["ready"]:
        raise ValueError(info.get("error", "Prepare official ResNet18 weights first"))
    digest = info["sha256"]
    saved = ctx.resume_state
    if saved:
        from .artifacts import validate_checkpoint_payload
        validate_checkpoint_payload({"step": ctx.step, "total_steps": ctx.spec["steps"], "recipe": "image", "recipe_version": 2,
                                     "payload": saved}, ctx.spec, data)
    if saved and saved.get("weights_sha256") != digest:
        raise ValueError("Resume requires the exact same verified pretrained base weights")
    asset = ctx.output_dir / "assets" / "base_weights.pth"
    if not asset.is_file() or hashlib.sha256(asset.read_bytes()).hexdigest() != digest:
        atomic_bytes(asset, Path(info["path"]).read_bytes())
    model = resnet18(weights=None)
    model.load_state_dict(torch.load(asset, map_location="cpu", weights_only=True))
    for parameter in model.parameters():
        parameter.requires_grad_(False)
    model.fc = torch.nn.Linear(model.fc.in_features, len(data["metadata"]["classes"]))
    model.to(ctx.device).eval()
    optimizer = torch.optim.SGD(model.fc.parameters(), lr=ctx.spec["learning_rate"], momentum=ctx.spec["momentum"])
    rng = random.Random(ctx.spec["seed"])
    epoch, cursor, order = 0, 0, []
    if saved:
        model.fc.load_state_dict({key: torch.tensor(value) for key, value in saved["head"].items()})
        restored = saved["optimizer"]
        restored["state"] = {int(key): {name: torch.tensor(value, device=ctx.device) if isinstance(value, list) else value
                                      for name, value in values.items()} for key, values in restored["state"].items()}
        optimizer.load_state_dict(restored)
        rng.setstate(tuples(saved["rng_state"]))
        epoch, cursor, order = saved["epoch"], saved["cursor"], saved["order"]
        torch.set_rng_state(torch.tensor(saved["torch_rng"], dtype=torch.uint8))
        if ctx.gpu_index is not None and saved.get("cuda_rng"):
            torch.cuda.set_rng_state(torch.tensor(saved["cuda_rng"], dtype=torch.uint8), device=ctx.device)
    transform = ResNet18_Weights.IMAGENET1K_V1.transforms()
    from .image_pipeline import ImageReader, cached_features
    mode = ctx.spec.get("image_loader", "sync")
    if mode == "prefetch" and ctx.spec["batch_size"] * 3 * 224 * 224 * 4 * 2 > 128 * 1024 * 1024:
        raise ValueError("Image prefetch buffers exceed 128 MiB; use synchronous loading or a smaller batch")
    reader = ImageReader(ctx.spec["dataset_path"], data["entries"], transform, torch, mode)
    def state():
        opt = optimizer.state_dict()
        opt["state"] = {str(key): {name: value.detach().cpu().tolist() if torch.is_tensor(value) else value
                                  for name, value in values.items()} for key, values in opt["state"].items()}
        result = {"head": {key: value.detach().cpu().tolist() for key, value in model.fc.state_dict().items()},
                "optimizer": opt, "rng_state": rng.getstate(), "torch_rng": torch.get_rng_state().tolist(),
                "cuda_rng": torch.cuda.get_rng_state(device=ctx.device).tolist() if ctx.gpu_index is not None else [],
                "epoch": epoch, "cursor": cursor, "order": order, "weights_sha256": digest,
                "weights_asset": "assets/base_weights.pth", "dataset_manifest_sha256": data["content_hash"],
                "split": {"train_indices": data["train_indices"], "validation_indices": data["validation_indices"]}}
        if ctx.spec.get("image_feature_cache"):
            result["image_feature_cache"] = True
        return result
    try:
        ctx._state_factory = state
        ctx.checkpoint(state())
        features = cached_features(ctx, data, model, reader, digest) if ctx.spec.get("image_feature_cache") else None
        if ctx.spec.get("image_feature_cache") and features is None:
            return
        ctx.phase = "training"
        pending = None
        while ctx.step < ctx.spec["steps"] and not ctx.should_stop():
            if not order or cursor >= len(order):
                if order:
                    epoch += 1
                order = list(data["train_indices"])
                rng.shuffle(order)
                cursor = 0
                pending = None
            batch = order[cursor:cursor + ctx.spec["batch_size"]]
            if features is not None:
                inputs = features[batch].to(ctx.device)
            else:
                if pending is None:
                    pending = iter(reader.batches([order[start:start + ctx.spec["batch_size"]] for start in range(cursor, len(order), ctx.spec["batch_size"])]))
                _, inputs = next(pending)
                inputs = inputs.to(ctx.device)
            labels = torch.tensor([data["labels"][index] for index in batch], device=ctx.device)
            optimizer.zero_grad(set_to_none=True)
            loss = torch.nn.functional.cross_entropy(model.fc(inputs) if features is not None else model(inputs), labels)
            loss.backward()
            optimizer.step()
            cursor += len(batch)
            ctx.report(loss.detach().item(), epoch=epoch + 1, epochs=ctx.spec["epochs"])
            ctx.performance.samples += len(batch)
            ctx.maybe_checkpoint()
        ctx.checkpoint(state())
        if ctx.step == ctx.spec["steps"]:
            ctx.phase = "evaluating"
            probabilities, labels = [], []
            with torch.no_grad():
                batches = [data["validation_indices"][start:start + ctx.spec["batch_size"]] for start in range(0, len(data["validation_indices"]), ctx.spec["batch_size"])]
                inputs_iter = ((batch, features[batch]) for batch in batches) if features is not None else reader.batches(batches)
                for batch, inputs in inputs_iter:
                    output = model.fc(inputs.to(ctx.device)) if features is not None else model(inputs.to(ctx.device))
                    probabilities.extend(torch.softmax(output, 1).cpu().tolist())
                    labels.extend(data["labels"][index] for index in batch)
            ctx.evaluation = {"split": "validation", **classification_metrics(probabilities, labels, data["metadata"]["classes"])}
            exported = {"format": "multigpu-image-resnet18-v1", "classes": data["metadata"]["classes"],
                        "head": state()["head"], "base_weights": "base_weights.pth", "weights_sha256": digest,
                        "weights_identity": RESNET18_ID}
            ctx.export({"model.json": canonical_json(exported), "base_weights.pth": asset,
                        "input_schema.json": canonical_json(data["metadata"]["input_schema"]),
                        "evaluation.json": canonical_json(ctx.evaluation),
                        "README.txt": "ResNet18 frozen ImageNet backbone with a trained classification head.\n"
                                      "Install compatible torch, torchvision and Pillow, then run: python predict.py --image your-image.jpg\n"
                                      "The verified pretrained base asset is included; inference needs no download.\n", **inference_files()})
    finally:
        reader.close()
