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
    if "guided" in raw and not isinstance(raw["guided"], bool):
        raise ValueError("guided must be true or false.")
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
            dataset_folder = key == "dataset_path" and (image or spec["recipe"] == "custom" and spec["recipe_version"] == 2)
            valid_path = path.is_dir() if key == "dataset_path" and image else path.is_file() or dataset_folder and path.is_dir()
            if not valid_path:
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


def _unresolved_submission(job):
    resolution = job.get("submission_resolution") or {}
    attempt = (job.get("spec") or {}).get("attempt_id")
    resolved = bool(attempt and resolution.get("source") == "user_confirmed" and resolution.get("attempt_id") == attempt
                    and resolution.get("outcome") in ("stopped", "absent"))
    return job.get("submission_status") == "unknown" and not resolved


def _unresolved_cleanup(job):
    resolution = job.get("submission_resolution") or {}
    attempt = (job.get("spec") or {}).get("attempt_id")
    stopped = bool(attempt and resolution.get("attempt_id") == attempt
                   and resolution.get("source") == "user_confirmed" and resolution.get("outcome") == "stopped")
    return job.get("provider_id") == "lightning" and job.get("cleanup_status") in ("pending", "failed") and not stopped


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
        mode = provider.get("integration")
        mode = mode if mode in ("automatic", "development") else "guided"
        reason = connection.get("detail", "Connect and check this provider first.")
        eligible = bool(ready and mode == "automatic")
        blocker = None if eligible else "connection_not_ready"
        preparable = bool(pid != "local" and mode != "development" and provider.get("capabilities", {}).get("notebook_bundle"))
        if pid == "local":
            mode, eligible, reason = "automatic", True, "Runs on your computer; no cloud bill. Electricity and internet costs remain yours."
            blocker = None
            if spec["recipe"] in ("pytorch", "image") and not hardware.get("torch_available"):
                eligible, reason = False, "Install PyTorch in this Python environment to use this recipe."
                blocker = "missing_dependency"
            if spec["recipe"] == "image" and not hardware.get("torchvision_available"):
                eligible, reason = False, "Install the image training environment (PyTorch, torchvision and Pillow)."
                blocker = "missing_dependency"
            if spec["device"] == "cuda" and (spec["recipe"] == "starter" or not hardware.get("cuda_available")):
                eligible, reason = False, "CUDA requires a compatible PyTorch installation and the PyTorch or custom recipe."
                blocker = "cuda_unavailable"
            elif spec["device"] != "cpu" and hardware.get("cuda_available") and spec["recipe"] in ("pytorch", "image"):
                # An explicit local index must not silently fall back to GPU zero.
                if spec.get("gpu_index", 0) >= len(hardware.get("gpus", [])):
                    eligible, reason, blocker = False, "The selected local GPU index is unavailable. Choose an attached GPU.", "gpu_unavailable"
        elif mode == "development":
            mode, eligible, reason = "development", False, "Repository development helper; excluded from the general training pool."
            blocker = "development_only"
        elif mode != "automatic":
            reason = "Follow the in-app setup guide and run the training notebook in your own provider account. " + provider.get("free_summary", "")
            eligible = False
            blocker = "guided_required"
        if pid != "local" and eligible:
            if spec["recipe"] == "custom" and not config.get("allow_custom_scripts"):
                eligible, reason = False, "Confirm custom-script execution in this provider's connection settings first."
                blocker = "custom_script_consent"
            quota = connection.get("quota") or {}
            if pid == "kaggle":
                from .connectors import quota_admission
                admission = quota_admission(quota, spec["runtime_seconds"])
                if not admission["eligible"]:
                    eligible, reason = False, admission["detail"]
                    blocker = "quota_unavailable"
            else:
                # These adapters cannot independently verify/enforce a zero bill.
                # A saved balance assertion must not make preview more permissive
                # than the fresh connector launch policy.
                if spec["free_only"]:
                    eligible, reason = False, "Free-only jobs require a verified zero out-of-pocket cap, including storage and transfer. Use the in-app guided setup while this guardrail is unverified."
                    blocker = "free_cap_unverified"
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
                        blocker = "paid_consent_required"
                    else:
                        reason = "Paid execution explicitly enabled. Your reported provider-side limit applies; the app cannot independently verify that billing control."
                        warnings.append(f"{provider['name']}: the spending limit is user-reported. Configure and verify it with the provider, including storage and transfer charges.")
            if pid == "lightning" and eligible and (config.get("dedicated_studio") is not True or config.get("stop_studio_after_run") is not True):
                eligible, reason = False, "Lightning automatic runs require a dedicated Studio and permission to stop it after collecting results."
                blocker = "dedicated_studio_required"
        if spec["free_only"] and preparable:
            if pid == "colab-enterprise":
                preparable, reason, blocker = False, "Colab Enterprise is metered cloud compute. This app cannot verify that credits cover this session; enable paid compute with a spending cap to prepare it.", "free_cap_unverified"
            elif pid in {"deepnote", "hex", "datalore"}:
                if spec["device"] == "cuda":
                    preparable, reason, blocker = False, f"{provider['name']}'s free plan does not include GPU compute. Choose CPU compute or explicitly enable paid compute with a spending cap.", "paid_gpu_required"
                else:
                    warnings.append(f"{provider['name']}: select the free CPU machine for this free-only notebook. GPU compute requires a paid entitlement; the app cannot select or verify this provider's runtime for you.")
        if spec["recipe"] == "starter" and spec["device"] == "cuda":
            eligible, preparable, reason, blocker = False, False, "The starter recipe runs on CPU. Choose CPU or Auto, or select a GPU-capable recipe.", "recipe_cpu_only"
        if pid != "local" and any(j.get("provider_id") == pid and (j.get("status") in ("queued", "running", "pausing") or _unresolved_submission(j) or _unresolved_cleanup(j)) for j in active_jobs):
            eligible, reason = False, "A job already uses this account. Wait for it to finish so allowances are not double-reserved."
            preparable, blocker = False, "account_reserved"
        decisions.append({"provider_id": pid, "eligible": eligible, "preparation_eligible": preparable,
                          "mode": mode, "reason": reason, "blocker_code": blocker})
    selected = next((d for d in decisions if d["eligible"]), None)
    execution_mode = "automatic" if selected else None
    if spec.get("guided"):
        selected = decisions[0] if decisions[0]["preparation_eligible"] else None
        execution_mode = "guided" if selected else None
    elif selected is None and decisions[0]["mode"] == "guided" and decisions[0]["preparation_eligible"]:
        selected, execution_mode = decisions[0], "guided"
    if len(spec["providers"]) > 1:
        warnings.append("Auto chooses the first eligible selected resource for this training target. Cross-provider same-model training is not yet qualified; separate experiments are never created automatically.")
    if spec["free_only"] and execution_mode == "automatic":
        warnings.append("Training checkpoints and pauses at its runtime limit" + (" or the admitted provider GPU quota" if selected["provider_id"] == "kaggle" else "") + "; the model and requested step target stay unchanged.")
    if execution_mode == "guided":
        warnings.append("The notebook enforces its training runtime, but this app cannot stop a manually managed provider session or enforce its billing limits. Check the provider's compute, storage and transfer settings.")
    if spec["recipe"] == "custom":
        warnings.append("Your script must implement cooperative stopping and resumable checkpoints. It runs as trusted code.")
    automatic = execution_mode == "automatic"
    guided = execution_mode == "guided"
    pid = selected["provider_id"] if selected else None
    enforcement = "manual_unverified" if guided else "local" if pid == "local" else "provider_quota" if pid == "kaggle" else "user_reported" if pid in {"modal", "lightning"} else None
    device = None
    if selected:
        if spec["recipe"] == "custom" and spec["device"] == "auto":
            device = "script_defined"
        elif spec["recipe"] == "starter" or spec["device"] == "cpu":
            device = "cpu"
        elif pid == "local":
            device = f"cuda:{spec.get('gpu_index', 0)}" if hardware.get("cuda_available") else "cpu"
        else:
            device = "runtime_gpu"
    return {"eligible": automatic, "preparation_eligible": guided, "execution_mode": execution_mode,
            "selected_provider": pid, "limit_enforcement": enforcement, "resolved_device": device,
            "allowed_actions": ["launch"] if automatic else ["prepare_guided"] if guided else [],
            "decisions": decisions, "warnings": warnings}
