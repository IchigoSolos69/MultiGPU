"""Capability admission and per-job budgets; never assumes subscription equals free."""
import math
from pathlib import Path

RECIPES = [
    {"id": "starter", "name": "Tabular classifier", "description": "Binary classification on numeric CSV data, with a held-out evaluation and prediction example. No extra dependencies.", "defaults": {"batch_size":32,"learning_rate":0.05,"steps":500}},
    {"id": "pytorch", "name": "PyTorch tabular classifier", "description": "A small neural network on CUDA or CPU, with validation and reusable model export.", "defaults": {"batch_size":32,"learning_rate":0.05,"steps":500}},
    {"id": "image", "name": "Image classifier", "description": "Pretrained ResNet-18 with a trainable classification head. Choose a folder with one subfolder per class.", "defaults": {"batch_size":16,"learning_rate":0.001,"epochs":5}},
    {"id": "custom", "name": "Custom PyTorch / Python", "description": "Your train(ctx) script with reporting, checkpoint, resume and export hooks."},
]


def validate_spec(raw, default_output):
    if not isinstance(raw, dict):
        raise ValueError("Training settings must be a JSON object.")
    spec = {k: raw[k] for k in ("name", "recipe", "dataset_path", "script_path", "output_dir", "device", "seed", "guided", "recipe_version", "label_column", "epochs", "weights_path", "gpu_index") if k in raw}
    spec["recipe_version"] = raw.get("recipe_version", 2)
    if spec["recipe_version"] not in (1, 2):
        raise ValueError("Unsupported recipe version.")
    spec["name"] = str(spec.get("name", "Untitled training")).strip()[:120]
    if not spec["name"]:
        raise ValueError("Give the training run a name.")
    spec["recipe"] = spec.get("recipe", "starter")
    if spec["recipe"] not in {r["id"] for r in RECIPES}:
        raise ValueError("Choose a supported recipe.")
    providers = raw.get("providers", raw.get("selected_providers", ["local"]))
    if not isinstance(providers, list) or not providers or not all(isinstance(p, str) for p in providers):
        raise ValueError("Select at least one provider.")
    spec["providers"] = list(dict.fromkeys(providers))
    image = spec["recipe"] == "image"
    for key, default, minimum, maximum in [("steps", 500, 1, 10_000_000), ("batch_size", 16 if image else 32, 1, 65536), ("seed", 42, 0, 2**31-1), ("epochs", 5, 1, 1000), ("gpu_index", 0, 0, 255)]:
        value = raw.get(key, default)
        try:
            valid = not isinstance(value, bool) and math.isfinite(float(value)) and int(value) == float(value) and minimum <= int(value) <= maximum
        except (ValueError, TypeError, OverflowError):
            valid = False
        if not valid:
            raise ValueError(f"{key} must be an integer between {minimum} and {maximum}.")
        spec[key] = int(value)
    for key, default, minimum, maximum in [("learning_rate", 0.001 if image else 0.05, 0.000001, 10), ("paid_cap", 0, 0, 100000), ("runtime_minutes", 30, 0.05, 1440)]:
        if isinstance(raw.get(key), bool):
            raise ValueError(f"{key} must be a number, not a boolean.")
        value = float(raw.get(key, default))
        if not math.isfinite(value) or not minimum <= value <= maximum:
            raise ValueError(f"{key} must be between {minimum} and {maximum}.")
        spec[key] = value
    if "free_only" in raw and not isinstance(raw["free_only"], bool):
        raise ValueError("free_only must be true or false.")
    spec["free_only"] = raw.get("free_only", True)
    if not spec["free_only"] and spec["paid_cap"] <= 0:
        raise ValueError("Set a positive spending cap before enabling paid compute.")
    spec["runtime_seconds"] = int(spec["runtime_minutes"] * 60)
    spec["device"] = spec.get("device", "auto")
    if spec["device"] not in ("auto", "cpu", "cuda"):
        raise ValueError("Device must be auto, cpu or cuda.")
    spec["output_dir"] = str(Path(spec.get("output_dir") or default_output).expanduser().resolve())
    for key in ("dataset_path", "script_path"):
        if spec.get(key):
            path = Path(spec[key]).expanduser().resolve()
            if not (path.is_dir() if key == "dataset_path" and image else path.is_file()):
                raise ValueError(f"{key} does not point to a readable local file.")
            spec[key] = str(path)
    if spec["recipe"] == "custom" and not spec.get("script_path"):
        raise ValueError("Custom training requires a Python script implementing train(ctx).")
    if spec["recipe"] == "custom" and raw.get("trust_script") is not True:
        raise ValueError("Confirm that you trust this script. It runs with your user permissions.")
    spec["trust_script"] = raw.get("trust_script", False)
    spec["summary_interval_seconds"] = 300
    spec["checkpoint_interval_seconds"] = 60
    return spec


def preview(spec, providers, connections, hardware, active_jobs=()):
    lookup = {p["id"]: p for p in providers}
    decisions = []
    warnings = []
    for pid in spec["providers"]:
        if pid not in lookup:
            raise ValueError(f"Unknown provider: {pid}")
        provider = lookup[pid]
        connection = connections.get(pid, {})
        config = connection.get("config", {})
        ready = connection.get("ready", False)
        mode = "automatic" if provider.get("integration") == "automatic" else "guided"
        reason = connection.get("detail", "Connect and check this provider first.")
        eligible = bool(ready and mode == "automatic")
        if pid == "local":
            mode, eligible, reason = "automatic", True, "Runs on your computer; no cloud bill. Electricity and internet costs remain yours."
            if spec["recipe"] in ("pytorch", "image") and not hardware.get("torch_available"):
                eligible, reason = False, "Install PyTorch in this Python environment to use this recipe."
            if spec["recipe"] == "image" and not hardware.get("torchvision_available"):
                eligible, reason = False, "Install the image training environment (PyTorch, torchvision and Pillow)."
            if spec["device"] == "cuda" and (spec["recipe"] == "starter" or not hardware.get("cuda_available")):
                eligible, reason = False, "CUDA requires a compatible PyTorch installation and the PyTorch or custom recipe."
        elif pid == "codespaces":
            mode, eligible, reason = "development", False, "Repository development helper; excluded from the general training pool."
        elif mode != "automatic":
            reason = "Follow the in-app setup guide and run the training notebook in your own provider account. " + provider.get("free_summary", "")
            eligible = False
        if pid != "local" and eligible:
            if spec["recipe"] == "custom" and not config.get("allow_custom_scripts"):
                eligible, reason = False, "Confirm custom-script execution in this provider's connection settings first."
            quota = connection.get("quota") or {}
            if pid == "kaggle":
                from .connectors import quota_admission
                admission = quota_admission(quota, spec["runtime_seconds"])
                if not admission["eligible"]:
                    eligible, reason = False, admission["detail"]
            else:
                # shortcut: native billing caps are not verified by every SDK; fail closed until a connector can prove them.
                budget = connection.get("budget") or {}
                if spec["free_only"]:
                    if not budget.get("verified") or budget.get("max_out_of_pocket") != 0:
                        eligible, reason = False, "Free-only jobs require a verified zero out-of-pocket cap, including storage and transfer. Use the in-app guided setup while this guardrail is unverified."
                else:
                    reported_limit = config.get("native_spend_limit")
                    try:
                        if isinstance(reported_limit, bool):
                            raise ValueError("A boolean is not a spending limit.")
                        reported_limit = float(reported_limit)
                    except (ValueError, TypeError):
                        reported_limit = math.inf
                    if not config.get("entitlement_confirmed") or not config.get("budget_confirmed") or not math.isfinite(reported_limit) or not 0 < reported_limit <= spec["paid_cap"]:
                        eligible, reason = False, "Confirm your own premium entitlement and a positive provider-side spending limit no higher than this job's cap."
                    else:
                        reason = "Paid execution explicitly enabled. Your reported provider-side limit applies; the app cannot independently verify that billing control."
                        warnings.append(f"{provider['name']}: the spending limit is user-reported. Configure and verify it with the provider, including storage and transfer charges.")
            if pid == "lightning" and eligible and (config.get("dedicated_studio") is not True or config.get("stop_studio_after_run") is not True):
                eligible, reason = False, "Lightning automatic runs require a dedicated Studio and permission to stop it after collecting results."
        if eligible and pid != "local" and any(j.get("provider_id") == pid and (j.get("status") in ("queued", "running", "pausing") or j.get("submission_status") == "unknown") for j in active_jobs):
            eligible, reason = False, "A job already uses this account. Wait for it to finish so allowances are not double-reserved."
        decisions.append({"provider_id": pid, "eligible": eligible, "mode": mode, "reason": reason})
    selected = next((d["provider_id"] for d in decisions if d["eligible"]), None)
    if len(spec["providers"]) > 1:
        warnings.append("Auto chooses the first eligible selected resource for this training target. Cross-provider same-model training is not yet qualified; separate experiments are never created automatically.")
    if spec["free_only"]:
        warnings.append("Training pauses when allowance or runtime ends; the model and requested step target stay unchanged.")
    if spec["recipe"] == "custom":
        warnings.append("Your script must implement cooperative stopping and resumable checkpoints. It runs as trusted code.")
    return {"eligible": selected is not None, "selected_provider": selected, "decisions": decisions, "warnings": warnings}
