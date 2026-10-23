"""Verified local inference for built-in exports; also bundled with each model."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from .artifacts import verify_export


def sigmoid(value: float) -> float:
    return 1 / (1 + math.exp(-max(-700.0, min(700.0, value))))


def tabular_probability(model: dict, row: list[float], *, standardize=True) -> float:
    if standardize and model.get("standardization"):
        transform = model["standardization"]
        row = [(value - mean) / std for value, mean, std in zip(row, transform["mean"], transform["std"])]
    if model["format"] == "multigpu-linear-binary-v1":
        return sigmoid(sum(a * b for a, b in zip(model["weights"], row)) + model["bias"])
    if model["format"] == "multigpu-pytorch-mlp-v1":
        weights = model["model"]
        hidden = [math.tanh(sum(a * b for a, b in zip(weight, row)) + bias)
                  for weight, bias in zip(weights["0.weight"], weights["0.bias"])]
        return sigmoid(sum(a * b for a, b in zip(weights["2.weight"][0], hidden)) + weights["2.bias"][0])
    raise ValueError("This model has no built-in tabular inference adapter")


def classification_metrics(probabilities: list[list[float]], labels: list[int], classes: list) -> dict:
    matrix = [[0 for _ in classes] for _ in classes]
    losses = []
    for values, label in zip(probabilities, labels):
        prediction = max(range(len(values)), key=values.__getitem__)
        matrix[int(label)][prediction] += 1
        losses.append(-math.log(max(1e-15, min(1.0, values[int(label)]))))
    return {"samples": len(labels), "loss": sum(losses) / len(losses) if losses else None,
            "accuracy": sum(matrix[i][i] for i in range(len(classes))) / len(labels) if labels else None,
            "classes": classes, "confusion_matrix": matrix}


def _image_predict(folder: Path, model: dict, image_path: str) -> dict:
    try:
        import torch
        from PIL import Image
        from torchvision.models import resnet18, ResNet18_Weights
    except (ImportError, RuntimeError) as exc:
        raise ValueError("Image prediction needs a compatible PyTorch, torchvision and Pillow installation") from exc
    base_path = folder / model["base_weights"]
    network = resnet18(weights=None)
    network.load_state_dict(torch.load(base_path, map_location="cpu", weights_only=True))
    network.fc = torch.nn.Linear(network.fc.in_features, len(model["classes"]))
    network.fc.load_state_dict({key: torch.tensor(value) for key, value in model["head"].items()})
    network.eval()
    path = Path(image_path)
    if not path.is_file():
        raise ValueError("Choose a readable local image file")
    with Image.open(path) as image:
        if image.width * image.height > 40_000_000:
            raise ValueError("Prediction image exceeds 40 million pixels")
        batch = ResNet18_Weights.IMAGENET1K_V1.transforms()(image.convert("RGB")).unsqueeze(0)
    with torch.no_grad():
        values = torch.softmax(network(batch), dim=1)[0].tolist()
    label = max(range(len(values)), key=values.__getitem__)
    return {"prediction": model["classes"][label], "probability": values[label],
            "classes": model["classes"], "probabilities": values}


def predict(export_dir: Path, inputs: dict) -> dict:
    folder = Path(export_dir)
    manifest = verify_export(folder)
    included = {item["path"] for item in manifest["files"]}
    if "model.json" not in included:
        raise ValueError("This export has no built-in prediction adapter")
    model = json.loads((folder / "model.json").read_text(encoding="utf-8"))
    if model.get("format") == "multigpu-image-resnet18-v1":
        if model.get("base_weights") not in included:
            raise ValueError("Image model's verified base weights are missing")
        return _image_predict(folder, model, inputs.get("image_path", ""))
    if model.get("format") not in ("multigpu-linear-binary-v1", "multigpu-pytorch-mlp-v1"):
        raise ValueError("Custom model exports use their own prediction adapter; see the script's inference instructions")
    schema = json.loads((folder / "input_schema.json").read_text(encoding="utf-8")) if "input_schema.json" in included else None
    raw = inputs.get("features")
    if isinstance(raw, dict):
        if not schema or set(raw) != set(schema["features"]):
            raise ValueError("Supply exactly the named features from input_schema.json")
        raw = [raw[name] for name in schema["features"]]
    expected = len(schema["features"]) if schema else len(model.get("weights", [])) or model.get("architecture", {}).get("features")
    if not isinstance(raw, list) or len(raw) != expected:
        raise ValueError(f"Supply {expected} numeric features in the saved schema order")
    try:
        row = [float(value) for value in raw]
    except (TypeError, ValueError):
        raise ValueError("Prediction features must be numeric") from None
    if not all(math.isfinite(value) for value in row) or any(isinstance(value, bool) for value in raw):
        raise ValueError("Prediction features must be finite numbers")
    probability = tabular_probability(model, row)
    return {"prediction": int(probability >= 0.5), "probability": probability,
            "classes": [0, 1], "probabilities": [1 - probability, probability]}


predict_model = predict


def inference_files() -> dict:
    runtime = Path(__file__).parent
    return {"predict.py": "from pathlib import Path\nfrom multigpu_runtime.prediction import main\n"
                          "if __name__ == '__main__':\n    raise SystemExit(main(default_folder=Path(__file__).parent))\n",
            "multigpu_runtime/__init__.py": "",
            "multigpu_runtime/prediction.py": runtime / "prediction.py",
            "multigpu_runtime/artifacts.py": runtime / "artifacts.py"}


def main(argv=None, default_folder=None) -> int:
    parser = argparse.ArgumentParser(description="Predict using a verified MultiGPU export")
    parser.add_argument("--model", type=Path, default=default_folder or Path.cwd())
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--features", help="JSON array or object in saved input schema")
    group.add_argument("--image", help="Path to a local image")
    args = parser.parse_args(argv)
    try:
        values = {"features": json.loads(args.features)} if args.features is not None else {"image_path": args.image}
        print(json.dumps(predict(args.model, values), indent=2))
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
