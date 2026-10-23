"""Native-auth provider adapters. No provider passwords or tokens are stored here.

SDK calls are optional and deliberately lazy. An installed adapter is not proof
of GPU allocation, account eligibility, a free balance, or an enforceable cap.
"""
import base64
import csv
import hashlib
import importlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile

from .catalog import get_provider

_MAX_STAGE_BYTES = 512 * 1024 * 1024

class ConnectorError(RuntimeError):
    """An actionable failure; remote execution must not be inferred from it."""

    def __init__(self, message, *, code="provider", transient=False, intent=None):
        super().__init__(message)
        self.code, self.transient, self.intent = code, transient, intent


def _provider_error(provider, exc, action):
    name = type(exc).__name__
    lowered = name.lower()
    status = getattr(exc, "status_code", None)
    if status in {401, 403} or any(word in lowered for word in ("auth", "permission", "credential")):
        return ConnectorError(f"{provider} authentication or permission check failed. Sign in with its native tools again.", code="auth")
    if status == 404 or name in {"NotFoundError", "NotFound"}:
        return ConnectorError(f"{provider} did not find the saved resource during {action}. Inspect its console before retrying.", code="not_found")
    if status in {429, 500, 502, 503, 504} or any(word in lowered for word in ("connection", "timeout", "service", "unavailable")):
        return ConnectorError(f"{provider} is temporarily unreachable during {action}; the remote execution may still be running.", code="network", transient=True)
    return ConnectorError(f"{provider} {action} failed ({name}). Inspect its console; private provider error text was withheld.")


def _sdk(name, install):
    try:
        return importlib.import_module(name)
    except ImportError:
        raise ConnectorError(f"Install {install} in this Python environment and authenticate with its native tools.", code="missing_dependency") from None


def _check_config(config):
    forbidden = {"token", "api_key", "apikey", "password", "secret", "credentials", "access_token"}
    if any(str(key).lower() in forbidden for key in config):
        raise ConnectorError("Keep provider credentials in the provider's native credential store or environment, not in app settings.")


def _cli(name, args, timeout=60):
    executable = shutil.which(name)
    command = [executable, *args] if executable else [sys.executable, "-m", name, *args]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                                encoding="utf-8", errors="replace", check=False)
    except subprocess.TimeoutExpired:
        raise ConnectorError(f"{name} did not respond within {timeout} seconds. Check the provider console before retrying a submission.", code="network", transient=True) from None
    except OSError:
        raise ConnectorError(f"Could not start {name}. Install its current CLI in this Python environment.", code="missing_dependency") from None
    if result.returncode:
        # Provider errors can contain auth headers. Do not persist or echo them.
        diagnostic = (result.stdout + result.stderr).lower()
        code = "auth" if any(x in diagnostic for x in ("unauthorized", "forbidden", "invalid credentials", "401", "403")) else "not_found" if "404" in diagnostic or "not found" in diagnostic else "network" if any(x in diagnostic for x in ("timeout", "connection", "temporarily", "503", "502", "504", "429")) else "provider"
        raise ConnectorError(f"{name} command failed. Check the native CLI version, authentication and provider console; provider error text was withheld to protect credentials.", code=code, transient=code == "network")
    return result.stdout


def _quota():
    output = _cli("kaggle", ["quota", "--csv"])
    for row in csv.DictReader(io.StringIO(output.strip())):
        row = {str(k).strip().lower(): v for k, v in row.items() if k}
        if str(row.get("resource", "")).upper() == "GPU":
            try:
                result = dict(remaining_hours=float(row["remaining"].rstrip("h")), total_hours=float(row["total"].rstrip("h")),
                            used_hours=float(row["used"].rstrip("h")), resets_at=row.get("refreshat"),
                            source="kaggle quota --csv", observed_at=time.time(), verified=True)
                if not all(math.isfinite(result[k]) and result[k] >= 0 for k in ("remaining_hours", "total_hours", "used_hours")):
                    break
                return result
            except (KeyError, TypeError, ValueError):
                break
    raise ConnectorError("Kaggle returned no readable GPU quota. Upgrade its CLI and check accelerator access; free-only launch is blocked.")


def quota_admission(quota, runtime_seconds=3600):
    """One admission calculation used by preview and the fresh launch check."""
    remaining = quota.get("remaining_hours") if isinstance(quota, dict) else None
    valid = isinstance(remaining, (int, float)) and not isinstance(remaining, bool) and math.isfinite(remaining) and remaining >= 0
    usable = max(0, int(remaining * 3600) - 120) if valid else 0
    eligible = usable >= 30
    runtime = min(max(1, int(runtime_seconds)), usable) if eligible else 0
    return dict(eligible=eligible, runtime_seconds=runtime, timeout_seconds=runtime + 30 if eligible else 0,
                reserve_seconds=120, minimum_seconds=30,
                detail="Quota allows a bounded session with a checkpoint reserve." if eligible else "Kaggle quota is unknown, exhausted, or below the 150-second admission minimum.")


def _kaggle_identity(config):
    # config view prints only the native client's selected non-secret settings.
    output = _cli("kaggle", ["config", "view"])
    match = re.search(r"(?im)^\s*-?\s*username\s*:\s*([A-Za-z0-9_-]+)\s*$", output)
    if not match:
        raise ConnectorError("Kaggle did not report its authenticated username. Upgrade the native CLI and authenticate before checking again.", code="auth")
    actual = match.group(1)
    expected = str(config.get("username", "")).strip()
    if expected and expected.casefold() != actual.casefold():
        raise ConnectorError("The authenticated Kaggle account does not match the configured username. Change the setting or sign in to the intended account.", code="account_mismatch")
    return dict(provider="kaggle", username=actual, verified=True, source="provider")


def _modal_identity(config, handle=None):
    modal = _sdk("modal", "modal")
    expected = (handle or {}).get("identity", {}).get("workspace") or config.get("workspace")
    if not expected:
        raise ConnectorError("Enter the expected Modal workspace before connecting. The application will compare it with native authentication.", code="account_mismatch")
    try:
        workspace = modal.Workspace.from_context()
        actual = workspace.name
        actual = actual() if callable(actual) else actual
    except Exception as exc:
        raise _provider_error("Modal", exc, "workspace lookup") from None
    if not isinstance(actual, str) or not actual:
        raise ConnectorError("This Modal SDK cannot report its authenticated workspace. Upgrade modal; launch is blocked.", code="account_mismatch")
    if actual.casefold() != str(expected).strip().casefold():
        raise ConnectorError("The active Modal credentials belong to a different workspace. Select the intended native profile; this app never switches accounts automatically.", code="account_mismatch")
    return dict(provider="modal", workspace=actual, verified=True, source="provider")


def _studio(config):
    sdk = _sdk("lightning_sdk", "lightning-sdk")
    config = {**config, "studio": config.get("studio") or config.get("studio_name"),
              "teamspace": config.get("teamspace") or config.get("teamspace_name"),
              "user": config.get("user") or config.get("owner")}
    organization = config.get("organization")
    if organization and config.get("user"):
        raise ConnectorError("Choose either a Lightning user or an organization, not both.", code="account_mismatch")
    missing = [key for key in ("studio", "teamspace") if not config.get(key)]
    if not organization and not config.get("user"):
        missing.append("user or organization")
    if missing:
        raise ConnectorError("Set Lightning " + ", ".join(missing) + " after creating a Studio directly with Lightning AI.")
    try:
        owner = {"org": organization} if organization else {"user": config["user"]}
        return sdk.Studio(name=config["studio"], teamspace=config["teamspace"], create_ok=False, **owner)
    except Exception as exc:
        raise _provider_error("Lightning", exc, "Studio lookup") from None


def _machine_name(machine):
    value = getattr(machine, "name", None)
    return value if isinstance(value, str) else str(machine).removeprefix("Machine.") if machine is not None else None


def _lightning_identity(config, studio):
    owner = getattr(studio, "owner", None)
    actual_owner = getattr(owner, "name", None)
    teamspace = getattr(getattr(studio, "teamspace", None), "name", None)
    expected_owner = config.get("organization") or config.get("user") or config.get("owner")
    expected_team = config.get("teamspace") or config.get("teamspace_name")
    if not isinstance(actual_owner, str) or not isinstance(teamspace, str):
        raise ConnectorError("Lightning did not report the Studio owner and teamspace. Upgrade its SDK and verify the selected Studio.", code="account_mismatch")
    if actual_owner.casefold() != str(expected_owner).casefold() or teamspace.casefold() != str(expected_team).casefold():
        raise ConnectorError("Lightning Studio identity does not match the requested owner/teamspace.", code="account_mismatch")
    return dict(provider="lightning", owner=actual_owner, owner_type="organization" if config.get("organization") else "user",
                teamspace=teamspace, studio=studio.name, verified=True, source="provider",
                **{"organization" if config.get("organization") else "user": actual_owner})


def _lightning_machine(config, studio):
    sdk = _sdk("lightning_sdk", "lightning-sdk")
    requested = str(config.get("machine", "")).strip()
    if requested:
        try:
            return sdk.Machine.from_str(requested)
        except Exception:
            raise ConnectorError("Lightning does not recognize this machine. Use a current SDK machine name such as CPU, T4 or L4.", code="invalid_resource") from None
    machine = studio.machine
    if machine is None:
        raise ConnectorError("Choose an explicit Lightning machine before starting a stopped Studio.", code="invalid_resource")
    return machine


def probe(provider_id, config):
    """Read-only authentication/capability check; never establishes a billing cap."""
    provider = get_provider(provider_id)
    _check_config(config)
    result = dict(ready=False, account_verified=False, budget_verified=False,
                  capabilities=provider["capabilities"], detail="", checked_at=time.time(), connection_status="unknown")
    if provider_id in {"modal", "lightning"}:
        result["budget"] = dict(verified=False, source="user_reported",
                                max_out_of_pocket=config.get("native_spend_limit"))
    try:
        if provider_id == "local":
            result.update(ready=True, detail="Local CPU starter is available. GPU recipes require a compatible PyTorch installation.")
        elif provider_id == "kaggle":
            quota = _quota()
            identity = _kaggle_identity(config)
            result.update(ready=quota_admission(quota)["eligible"], account_verified=True, quota=quota, identity=identity,
                          detail="Kaggle authentication and GPU quota were read. Immediate remote cancellation remains unavailable.")
        elif provider_id == "modal":
            identity = _modal_identity(config)
            result.update(ready=True, account_verified=True, identity=identity,
                          requested_resource=dict(gpu=config.get("gpu")),
                          actual_resource=dict(status="not_allocated", gpu=None),
                          detail="Modal authenticated. Compute eligibility and billing caps are unverified; automatic free-only launch is blocked.")
        elif provider_id == "lightning":
            studio = _studio(config)
            status = str(studio.status)
            identity = _lightning_identity(config, studio)
            machine = _lightning_machine(config, studio)
            result.update(ready=True, account_verified=True, identity=identity,
                          requested_resource=dict(machine=_machine_name(machine)),
                          actual_resource=dict(machine=_machine_name(studio.machine), status=status),
                          machine_options=["CPU", "T4", "L4", "L40S", "A100"],
                          detail=f"Lightning Studio found ({status}). Free-only credit enforcement is unverified and blocked.")
        else:
            result.update(detail="Assisted setup only. Generate a notebook bundle and launch it yourself under the provider's current terms.")
        if result["account_verified"] or provider_id == "local":
            result["connection_status"] = "connected"
    except ConnectorError as exc:
        result["detail"] = str(exc)
        result["error_code"] = exc.code
        result["connection_status"] = "auth_required" if exc.code in {"auth", "account_mismatch"} else "retrying" if exc.transient else "unknown"
    except Exception as exc:
        result["detail"] = f"Provider verification failed ({type(exc).__name__}). Check native authentication and account permissions."
    return result


_RUNNER = '''import json, os
from pathlib import Path
from multigpu.worker import run
base = Path(__file__).resolve().parent
spec = json.loads((base / "spec.json").read_text(encoding="utf-8"))
for key in ("dataset_path", "script_path", "resume_from", "weights_path"):
    if spec.get(key):
        spec[key] = str(base / spec[key])
out = Path(os.environ.get("MULTIGPU_OUTPUT_DIR", str(Path.cwd() / "output"))).resolve()
out.mkdir(parents=True, exist_ok=True)
state_file = out / "connector-state.json"
def state(value):
    temporary = state_file.with_suffix(".tmp")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    temporary.replace(state_file)
state({"state": "running"})
try:
    result = run(spec, out)
    state({"state": result.get("status", "completed"), "metrics": result})
    if result.get("status") == "error":
        raise RuntimeError("Training worker failed; inspect output/events.jsonl")
except BaseException as exc:
    if not state_file.exists() or json.loads(state_file.read_text()).get("state") != "error":
        state({"state": "error", "detail": type(exc).__name__})
    raise
'''


def _stage(spec_path, job_dir):
    spec_path, job_dir = Path(spec_path), Path(job_dir)
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ConnectorError("The job specification could not be read as JSON.") from None
    if not isinstance(spec, dict):
        raise ConnectorError("The job specification must be a JSON object.")
    # A new staging folder excludes files generated by earlier bundle requests.
    stage = Path(tempfile.mkdtemp(prefix="payload-", dir=job_dir))
    (stage / "multigpu").mkdir(parents=True, exist_ok=True)
    (stage / "multigpu" / "__init__.py").write_text("", encoding="utf-8")
    for filename in ("worker.py", "artifacts.py", "datasets.py", "recipes.py", "prediction.py"):
        source = Path(__file__).parent / filename
        if not source.is_file() and filename in {"worker.py", "artifacts.py"}:
            raise ConnectorError(f"The application worker is incomplete: {filename} is missing.")
        if source.is_file():
            shutil.copyfile(source, stage / "multigpu" / filename)
    total_bytes = 0
    for key in ("dataset_path", "script_path", "resume_from", "weights_path"):
        if not spec.get(key):
            continue
        source = Path(spec[key]).expanduser()
        if not source.is_absolute():
            source = spec_path.parent / source
        if source.is_symlink() or (hasattr(source, "is_junction") and source.is_junction()) or not source.exists():
            raise ConnectorError(f"The configured {key} input does not exist or is a symbolic link.")
        files = list(source.rglob("*")) if source.is_dir() else [source]
        if source.is_dir() and key != "dataset_path":
            raise ConnectorError(f"The configured {key} must be a file.")
        for item in files:
            if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
                raise ConnectorError("Symbolic links are not accepted in staged datasets.")
            if item.is_file():
                total_bytes += item.stat().st_size
        if total_bytes > _MAX_STAGE_BYTES:
            raise ConnectorError("Selected inputs exceed this adapter's 512 MiB staging limit. Reduce the dataset or use provider-native storage and a custom training setup; no inputs were uploaded.", code="staging_limit")
        assets = stage / "assets"
        assets.mkdir(exist_ok=True)
        target = assets / (key if source.is_dir() else key + source.suffix)
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copyfile(source, target)
        if key == "weights_path":
            checksum = hashlib.sha256(source.read_bytes()).hexdigest()
            if spec.get("weights_sha256") and checksum != spec["weights_sha256"]:
                raise ConnectorError("Pretrained weights checksum does not match the prepared asset.")
            spec["weights_sha256"] = checksum
        spec[key] = "assets/" + target.name
    (stage / "spec.json").write_text(json.dumps(spec, indent=2), encoding="utf-8")
    (stage / "run.py").write_text(_RUNNER, encoding="utf-8")
    return stage, spec


def _payload_files(stage):
    for path in sorted(stage.rglob("*")):
        relative = path.relative_to(stage)
        if path.is_file() and not path.is_symlink() and (relative.parts[0] in {"multigpu", "assets"} or relative.as_posix() in {"spec.json", "run.py"}):
            yield path, relative.as_posix()


def _safe_relative(name):
    normalized = str(name).replace("\\", "/")
    relative = PurePosixPath(normalized)
    if (not normalized or relative.is_absolute() or ":" in normalized
            or any(p in {"", ".", ".."} for p in normalized.split("/"))
            or any(ord(c) < 32 for c in normalized)):
        raise ConnectorError("Provider returned an unsafe artifact path.")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if any(part.endswith((".", " ")) or part.split(".")[0].upper() in reserved for part in relative.parts):
        raise ConnectorError("Provider returned an unsafe artifact path.")
    return relative


def _embedded(stage):
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, relative in _payload_files(stage):
            archive.write(path, relative)
    encoded = base64.b64encode(payload.getvalue()).decode("ascii")
    return ("import base64, io, os, runpy, zipfile\nfrom pathlib import Path\n"
            "base = Path.cwd() / 'multigpu-payload'\nbase.mkdir(exist_ok=True)\n"
            f"archive = zipfile.ZipFile(io.BytesIO(base64.b64decode({encoded!r})))\n"
            "for item in archive.infolist():\n"
            "    target = (base / item.filename).resolve()\n"
            "    if not target.is_relative_to(base.resolve()) or ((item.external_attr >> 16) & 0o170000) == 0o120000:\n"
            "        raise ValueError('Unsafe bundle member')\n"
            "archive.extractall(base)\n"
            "import sys\nsys.path.insert(0, str(base))\n"
            "runpy.run_path(str(base / 'run.py'), run_name='__main__')\n")


def needs_payload(spec, base_dir=None):
    """Conservative import-size bound, shared by the guide and artifact builders."""
    size = 0
    for key in ("dataset_path", "script_path", "resume_from", "weights_path"):
        if not spec.get(key):
            continue
        source = Path(spec[key]).expanduser()
        if not source.is_absolute() and base_dir:
            source = Path(base_dir) / source
        if source.is_symlink() or (hasattr(source, "is_junction") and source.is_junction()) or not source.exists():
            raise ConnectorError(f"The configured {key} input is missing or is a symbolic link. Restore it before preparing training files.")
        for item in source.rglob("*") if source.is_dir() else [source]:
            if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
                raise ConnectorError("Symbolic links are not accepted in training inputs.")
            if item.is_file():
                # Include ZIP directory overhead and base64 expansion headroom.
                size += item.stat().st_size + 2 * len(str(item.relative_to(source) if source.is_dir() else item.name).encode("utf-8")) + 256
    return size > 4 * 1024 * 1024


def _training_manifest(stage):
    files = []
    for file, relative in _payload_files(stage):
        with file.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        files.append({"path": relative, "size_bytes": file.stat().st_size, "sha256": digest})
    if len(files) > 100000:
        raise ConnectorError("Training inputs contain too many individual files for notebook delivery.")
    return json.dumps({"format": "multigpu-training-payload-v1", "files": files}, sort_keys=True, separators=(",", ":")).encode("utf-8")


_EXTERNAL_PAYLOAD_RUNNER = r'''import hashlib, json, os, runpy, shutil, sys, zipfile
from pathlib import Path, PurePosixPath
root = Path.cwd()
name = "multigpu-payload-manifest.json"
archives = [root / "training-payload.zip"]
manifests = [root / name, root / "training-payload" / name]
if Path("/kaggle/input").is_dir():
    archives.extend(Path("/kaggle/input").rglob("training-payload.zip"))
    manifests.extend(Path("/kaggle/input").rglob(name))
archive, source, manifest_bytes = None, None, None
for candidate in archives:
    if candidate.is_file():
        trial = zipfile.ZipFile(candidate)
        try:
            content = trial.read(name)
        except KeyError:
            trial.close()
            continue
        if hashlib.sha256(content).hexdigest() == EXPECTED_MANIFEST:
            archive, manifest_bytes = trial, content
            break
        trial.close()
if manifest_bytes is None:
    for candidate in manifests:
        if candidate.is_file() and hashlib.sha256(candidate.read_bytes()).hexdigest() == EXPECTED_MANIFEST:
            source, manifest_bytes = candidate.parent.resolve(), candidate.read_bytes()
            break
if manifest_bytes is None:
    raise FileNotFoundError("Upload this notebook's matching training-payload.zip into its working folder, or attach its extracted files as a Kaggle input. Download both files again if they came from different preparations.")
manifest = json.loads(manifest_bytes)
if manifest.get("format") != "multigpu-training-payload-v1" or not 0 < len(manifest.get("files", [])) <= 100000:
    raise ValueError("Invalid training payload manifest")
if sum(item["size_bytes"] for item in manifest["files"]) > 520 * 1024 * 1024:
    raise ValueError("Training payload exceeds its size limit")
base = root / ("multigpu-payload-" + EXPECTED_MANIFEST[:12])
base.mkdir(exist_ok=True)
seen = set()
for item in manifest["files"]:
    relative = PurePosixPath(item["path"])
    if relative.is_absolute() or ":" in item["path"] or "\\" in item["path"] or any(p in ("", ".", "..") for p in item["path"].split("/")) or item["path"].casefold() in seen:
        raise ValueError("Unsafe training payload path")
    seen.add(item["path"].casefold())
    target = base.joinpath(*relative.parts)
    if not target.resolve().is_relative_to(base.resolve()):
        raise ValueError("Unsafe training payload destination")
    if archive is not None:
        content = archive.read(item["path"])
    else:
        origin = source.joinpath(*relative.parts)
        if not origin.resolve().is_relative_to(source):
            raise ValueError("Unsafe extracted training payload path")
        content = origin.read_bytes()
    if len(content) != item["size_bytes"] or hashlib.sha256(content).hexdigest() != item["sha256"]:
        raise ValueError("Training payload contents changed: " + item["path"])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
if archive is not None:
    archive.close()
sys.path.insert(0, str(base))
runpy.run_path(str(base / "run.py"), run_name="__main__")
'''


def _notebook_document(source, spec, separate):
    dependencies = "The starter needs only Python 3.11 or later."
    if spec.get("recipe") == "pytorch":
        dependencies = "Use a runtime with compatible PyTorch installed; CUDA is required only when this run explicitly selects CUDA."
    elif spec.get("recipe") == "image":
        dependencies = "Use a runtime with compatible torch, torchvision and Pillow installed. Verified ResNet-18 weights are included in the training payload."
    elif spec.get("recipe") == "custom":
        dependencies = "Install the dependencies required by your trusted custom script in this runtime before running training."
    introduction = "# MultiGPU training\n" + dependencies + "\n"
    introduction += "Upload training-payload.zip alongside this notebook before running the first cell.\n" if separate else "This notebook contains the selected training code, inputs and any recovery checkpoint.\n"
    save = ("import os, shutil\nfrom pathlib import Path\n"
            "output = Path(os.environ.get('MULTIGPU_OUTPUT_DIR', str(Path.cwd() / 'output'))).resolve()\n"
            "if not (output / 'checkpoint.json').is_file():\n    raise FileNotFoundError('Run training first; no checkpoint has been saved yet')\n"
            "result = shutil.make_archive(str(output.parent / 'multigpu-results'), 'zip', root_dir=output)\n"
            "print('Download this results file and import it into MultiGPU:', result)\n")
    def cell(kind, content):
        value = {"cell_type": kind, "metadata": {}, "source": content.splitlines(True)}
        if kind == "code":
            value.update(execution_count=None, outputs=[])
        return value
    return {"cells": [cell("markdown", introduction), cell("code", source),
                      cell("markdown", "## Save results\nAfter training completes or pauses, run this cell and download multigpu-results.zip. It contains the checkpoint, model files and required image weights. Keep it until the app verifies the import.\n"),
                      cell("code", save)],
            "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"}},
            "nbformat": 4, "nbformat_minor": 5}


def _remove_notebook_stage(stage, job_dir):
    stage = Path(stage)
    if stage.is_symlink() or (hasattr(stage, "is_junction") and stage.is_junction()) or stage.resolve().parent != Path(job_dir).resolve() or not stage.name.startswith("payload-"):
        raise ConnectorError("Refusing cleanup outside the prepared notebook directory.")
    shutil.rmtree(stage)


def build_notebook(provider_id, config, spec_path, job_dir):
    """Return the actual runnable notebook; setup instructions remain in the app."""
    get_provider(provider_id)
    _check_config(config)
    original = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    separate = needs_payload(original, Path(spec_path).parent)
    stage, spec = _stage(spec_path, job_dir)
    try:
        if separate:
            digest = hashlib.sha256(_training_manifest(stage)).hexdigest()
            source = "EXPECTED_MANIFEST = " + repr(digest) + "\n" + _EXTERNAL_PAYLOAD_RUNNER
        else:
            source = _embedded(stage)
        from .artifacts import atomic_json
        target = Path(job_dir) / "training.ipynb"
        atomic_json(target, _notebook_document(source, spec, separate))
        if target.stat().st_size >= 8 * 1024 * 1024:
            raise ConnectorError("This notebook exceeds the safe import size. Reduce selected inputs before preparing it again.")
        return target
    finally:
        _remove_notebook_stage(stage, job_dir)


def build_notebook_payload(provider_id, config, spec_path, job_dir):
    """Return selected training inputs, never an instruction archive."""
    get_provider(provider_id)
    _check_config(config)
    original = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    if not needs_payload(original, Path(spec_path).parent):
        raise ConnectorError("This small job's notebook already includes its training inputs; no separate payload is needed.")
    stage, _ = _stage(spec_path, job_dir)
    target = Path(job_dir) / "training-payload.zip"
    temporary = target.with_suffix(".zip.tmp")
    try:
        manifest = _training_manifest(stage)
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("multigpu-payload-manifest.json", manifest)
            for file, relative in _payload_files(stage):
                archive.write(file, relative)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
        _remove_notebook_stage(stage, job_dir)
    return target


def build_bundle(provider_id, config, spec_path, job_dir):
    """Create a self-contained notebook ZIP; this does not start remote compute."""
    get_provider(provider_id)
    _check_config(config)
    stage, _ = _stage(spec_path, job_dir)
    source = _embedded(stage)
    notebook = dict(cells=[dict(cell_type="markdown", metadata={}, source=[
        "# MultiGPU training bundle\nLaunch this notebook yourself on authorized compute. "
        "It contains your selected data/script/checkpoint. Review before uploading. "
        "The starter recipe uses CPU. Download output/ after completion."]),
        dict(cell_type="code", execution_count=None, metadata={}, outputs=[], source=source.splitlines(True))],
        metadata=dict(kernelspec=dict(display_name="Python 3", language="python", name="python3")),
        nbformat=4, nbformat_minor=5)
    (stage / "training.ipynb").write_text(json.dumps(notebook), encoding="utf-8")
    (stage / "README.md").write_text(
        "Extract training.ipynb from this ZIP, upload it, and run its cell.\n"
        "The notebook embeds worker.py, artifacts.py, spec.json and selected input files exactly once.\n"
        "Choose compute and configure billing directly with your provider.\n"
        "This bundle does not keep runtimes alive or connect them to a distributed cluster.\n"
        "Create output/STOP to request a checkpoint and graceful pause.\n"
        "Custom recipes need their dependencies installed and must be standalone Python scripts.\n"
        "Outputs remain with the provider until you download/import them.\n", encoding="utf-8")
    target = Path(job_dir) / "notebook-bundle.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(stage / "training.ipynb", "training.ipynb")
        archive.write(stage / "README.md", "README.md")
    return target


def _paid_policy(config, spec):
    if spec.get("free_only", config.get("free_only", True)):
        raise ConnectorError("Automatic free-only execution is blocked for this credit-backed provider: this app cannot independently verify an enforceable zero-spend cap. Use the guided bundle or explicitly choose paid execution with native billing controls.")
    if config.get("budget_confirmed") is not True or config.get("entitlement_confirmed") is not True:
        raise ConnectorError("Confirm your provider entitlement and configure native billing controls before authorizing paid execution. These confirmations are user assertions, not a verified free allowance.")
    limit, cap = config.get("native_spend_limit"), spec.get("paid_cap")
    if (isinstance(limit, bool) or isinstance(cap, bool) or not isinstance(limit, (int, float))
            or not isinstance(cap, (int, float)) or not math.isfinite(limit) or not math.isfinite(cap)
            or limit < 0 or cap <= 0 or limit > cap):
        raise ConnectorError("Enter the USD spending limit configured directly with your provider. It must be finite and no greater than this job's paid cap; this remains a user-reported limit.")


def _json_write(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temporary.replace(path)


def _record_path(intent, job_dir):
    attempt = str(intent.get("attempt_id", ""))
    if not re.fullmatch(r"[a-f0-9]{32}", attempt):
        raise ConnectorError("The saved submission identity is invalid; inspect the existing provider run before starting another attempt.")
    return Path(job_dir) / ("submission-" + attempt + ".json")


def _bound_config(config, value):
    """Existing runs retain their account and Studio scope after settings change."""
    identity = value.get("identity", {})
    if value.get("provider") == "lightning" and identity:
        config = {**config, "studio": identity["studio"], "teamspace": identity["teamspace"],
                  "owner": None, "user": None, "organization": None}
        config["organization" if identity.get("owner_type") == "organization" else "user"] = identity["owner"]
    return config


def prepare(provider_id, config, spec_path, job_dir):
    """Read-only remote preflight and local staging; safe to persist before submit."""
    get_provider(provider_id)
    _check_config(config)
    job_dir = Path(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(Path(spec_path).read_bytes()).hexdigest()
    saved = job_dir / ("intent-" + provider_id + ".json")
    if saved.is_file():
        prior = json.loads(saved.read_text(encoding="utf-8"))
        if prior.get("spec_sha256") == digest:
            prior_record = _record_path(prior, job_dir)
            prior_status = json.loads(prior_record.read_text(encoding="utf-8")).get("submission_status") if prior_record.is_file() else None
            if prior_status != "not_submitted":
                return prior
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    attempt = uuid.uuid4().hex
    intent = dict(provider=provider_id, attempt_id=attempt, job_id=spec.get("id"),
                  spec_sha256=digest, submission_status="prepared", prepared_at=time.time())
    if provider_id == "kaggle":
        quota = _quota()
        admission = quota_admission(quota, spec.get("runtime_seconds", 3600))
        if not admission["eligible"]:
            raise ConnectorError("Kaggle GPU quota is exhausted or too small for the 150-second admission minimum.", code="quota")
        identity = _kaggle_identity(config)
        intent.update(identity=identity, kernel=identity["username"] + "/multigpu-" + attempt[:20],
                      quota=quota, runtime_seconds=admission["runtime_seconds"], timeout_seconds=admission["timeout_seconds"])
    elif provider_id == "modal":
        _paid_policy(config, spec)
        intent.update(identity=_modal_identity(config), app_name="multigpu-" + attempt,
                      volume="multigpu-" + attempt, control="multigpu-control-" + attempt,
                      environment=str(config.get("environment") or "main"), requested_gpu=config.get("gpu"))
    elif provider_id == "lightning":
        _paid_policy(config, spec)
        _lightning_consent(config)
        studio = _studio(config)
        machine = _lightning_machine(config, studio)
        if "running" not in str(studio.status).lower() and config.get("start_studio") is not True:
            raise ConnectorError("The Lightning Studio is stopped. Authorize starting the dedicated Studio before launching.", code="invalid_resource")
        intent.update(identity=_lightning_identity(config, studio), remote_dir="multigpu-runs/" + attempt,
                      requested_machine=_machine_name(machine), dedicated_studio=True, stop_studio_after_run=True)
    if provider_id in {"local", "kaggle", "modal", "lightning"}:
        stage, staged_spec = _stage(spec_path, job_dir)
        if provider_id == "kaggle":
            staged_spec["runtime_seconds"] = intent["runtime_seconds"]
            _json_write(stage / "spec.json", staged_spec)
        intent["stage_path"] = str(stage.resolve())
    _json_write(saved, intent)
    return intent


def submit(provider_id, config, spec_path, job_dir, intent=None):
    get_provider(provider_id)
    _check_config(config)
    job_dir = Path(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    if provider_id not in {"local", "kaggle", "modal", "lightning"}:
        bundle = build_bundle(provider_id, config, spec_path, job_dir)
        return dict(provider=provider_id, state="awaiting_user", bundle_path=str(bundle),
                    detail="Bundle prepared locally. No provider runtime has been launched.")
    intent = intent or prepare(provider_id, config, spec_path, job_dir)
    if intent.get("provider") != provider_id or intent.get("spec_sha256") != hashlib.sha256(Path(spec_path).read_bytes()).hexdigest():
        raise ConnectorError("The submission intent does not match this provider and job specification.", code="intent_mismatch")
    record = _record_path(intent, job_dir)
    if record.exists():
        previous = json.loads(record.read_text(encoding="utf-8"))
        if previous.get("submission_status") == "confirmed":
            return previous["handle"]
        raise ConnectorError("This attempt may already have launched. Reconnect to its saved provider identity; automatic resubmission is blocked.", code="submission_unknown", intent=intent)
    config = _bound_config(config, intent)
    stage = Path(intent["stage_path"])
    if not stage.is_dir() or not stage.resolve().is_relative_to(job_dir.resolve()):
        raise ConnectorError("The locally staged payload is missing or outside this job folder. Prepare a new attempt only after resolving any previous provider execution.")
    spec = json.loads((stage / "spec.json").read_text(encoding="utf-8"))
    # Fresh checks happen before the first mutation. A failure here proves no launch.
    if provider_id in {"modal", "lightning"}:
        _paid_policy(config, spec)
    if provider_id == "modal":
        _modal_identity(config, intent)
    elif provider_id == "lightning":
        _lightning_consent(config)
        _lightning_identity(config, _studio(config))
    elif provider_id == "kaggle":
        identity = _kaggle_identity({**config, "username": intent["identity"]["username"]})
        quota = _quota()
        admission = quota_admission(quota, spec.get("runtime_seconds", 3600))
        if not admission["eligible"]:
            raise ConnectorError("Kaggle GPU quota is exhausted or too small for a safe checkpoint margin.", code="quota")
        spec["runtime_seconds"] = admission["runtime_seconds"]
        _json_write(stage / "spec.json", spec)
        intent.update(quota=quota, runtime_seconds=admission["runtime_seconds"], timeout_seconds=admission["timeout_seconds"])
    # Exclusive journal prevents two coordinator threads from submitting this attempt.
    try:
        with record.open("x", encoding="utf-8") as stream:
            json.dump({**intent, "submission_status": "submitting"}, stream)
    except FileExistsError:
        raise ConnectorError("Another submission owns this attempt. Reconnect before retrying.", code="submission_unknown", intent=intent) from None
    try:
        if provider_id == "kaggle":
            slug = intent["kernel"].split("/")[1]
            metadata = dict(id=intent["kernel"], title=slug, code_file="train.py", language="python",
                            kernel_type="script", is_private=True, enable_gpu=True, enable_internet=True,
                            dataset_sources=[], competition_sources=[], kernel_sources=[])
            _json_write(stage / "kernel-metadata.json", metadata)
            (stage / "train.py").write_text(_embedded(stage), encoding="utf-8")
            _cli("kaggle", ["kernels", "push", "-p", str(stage), "--timeout", str(intent["timeout_seconds"]),
                            "--accelerator", "NvidiaTeslaT4"], timeout=120)
            handle = dict(**intent, state="queued", dashboard_url=f"https://www.kaggle.com/code/{intent['kernel']}")
        elif provider_id == "modal":
            handle = _submit_modal(config, spec, stage, intent)
        elif provider_id == "lightning":
            handle = _submit_lightning(config, stage, intent)
        else:
            log = (job_dir / "training.log").open("w", encoding="utf-8")
            try:
                child = subprocess.Popen([sys.executable, str((stage / "run.py").resolve())], cwd=job_dir, stdout=log, stderr=subprocess.STDOUT)
            finally:
                log.close()
            handle = dict(**intent, pid=child.pid, state="running")
        handle["submission_status"] = "confirmed"
        _json_write(record, {**intent, "submission_status": "confirmed", "handle": handle})
        return handle
    except Exception as exc:
        if isinstance(exc, ConnectorError) and exc.code == "preflight_failed":
            _json_write(record, {**intent, "submission_status": "not_submitted"})
            raise
        _json_write(record, {**intent, "submission_status": "unknown"})
        if isinstance(exc, ConnectorError) and exc.code == "submission_unknown":
            raise
        raise ConnectorError("Submission acknowledgment was not saved. The provider may have started this attempt; reconnect before retrying. " + (str(exc) if isinstance(exc, ConnectorError) else f"Provider failure: {type(exc).__name__}."), code="submission_unknown", intent=intent) from None


def _submit_modal(config, spec, stage, intent):
    modal = _sdk("modal", "modal")
    name = intent["volume"]
    environment = intent.get("environment", "main")
    control = modal.Dict.from_name(intent["control"], create_if_missing=True, environment_name=environment)
    if not control.put("submission_claimed", intent["attempt_id"], skip_if_exists=True):
        raise ConnectorError("Modal already has this submission claim. Reconnect instead of starting another function.", code="submission_unknown", intent=intent)
    volume = modal.Volume.from_name(name, create_if_missing=True, environment_name=environment)
    image = modal.Image.debian_slim(python_version="3.11")
    if spec.get("recipe") == "pytorch":
        image = image.pip_install("torch")
    elif spec.get("recipe") == "image":
        image = image.pip_install("torch", "torchvision", "Pillow")
    image = image.add_local_dir(str(stage.resolve()), remote_path="/payload")
    app = modal.App(intent["app_name"])
    options = dict(image=image, volumes={"/output": volume}, serialized=True, include_source=False,
                   timeout=int(spec.get("runtime_seconds", 3600)) + 60)
    if intent.get("requested_gpu"):
        options["gpu"] = str(intent["requested_gpu"])

    @app.function(**options)
    def train():
        import os
        import runpy
        import sys
        import threading
        import time
        import modal
        from pathlib import Path
        handle = {**intent, "call_id": modal.current_function_call_id(), "state": "running", "submission_status": "confirmed"}
        control.put("handle", handle)
        done = threading.Event()
        def flush():
            last_commit = time.monotonic()
            while not done.is_set():
                try:
                    if control.get("pause", False):
                        Path("/output/STOP").touch()
                    if time.monotonic() - last_commit >= 30:
                        volume.commit()
                        last_commit = time.monotonic()
                except Exception:
                    pass  # A transient control outage must not kill training.
                done.wait(1)
        # Honor a pause accepted while the function was still provisioning.
        if control.get("pause", False):
            Path("/output/STOP").touch()
        thread = threading.Thread(target=flush, daemon=True)
        thread.start()
        try:
            os.environ["MULTIGPU_OUTPUT_DIR"] = "/output"
            sys.path.insert(0, "/payload")
            runpy.run_path("/payload/run.py", run_name="__main__")
            import json
            return json.loads(Path("/output/connector-state.json").read_text())
        finally:
            done.set()
            thread.join(timeout=5)
            volume.commit()

    try:
        with app.run(detach=True, environment_name=environment):
            intent["app_id"] = app.app_id
            call = train.spawn()
            handle = dict(**intent, call_id=call.object_id, state="queued", submission_status="confirmed")
            control.put("handle", handle)
            return handle
    except Exception as exc:
        raise ConnectorError(f"Modal submission was not acknowledged ({type(exc).__name__}). Reconnect using this attempt's control record; a function may be running.", code="submission_unknown", intent=intent) from None


def _lightning_consent(config):
    if config.get("dedicated_studio") is not True or config.get("stop_studio_after_run") is not True:
        raise ConnectorError("Lightning automatic execution requires a dedicated Studio and explicit permission to stop it after artifact collection. Shared Studios must use the guided bundle.")


def _submit_lightning(config, stage, intent=None):
    _lightning_consent(config)
    if intent is None:
        raise ConnectorError("Prepare a durable Lightning submission identity before starting its Studio.")
    studio = _studio(config)
    identity = _lightning_identity(config, studio)
    machine = _lightning_machine({**config, "machine": intent["requested_machine"]}, studio)
    remote = intent["remote_dir"]
    runner = stage / "embedded-run.py"
    runner.write_text(_embedded(stage), encoding="utf-8")
    launch_attempted = False
    try:
        if "running" not in str(studio.status).lower():
            if config.get("start_studio") is not True:
                raise ConnectorError("Authorize starting the dedicated Lightning Studio before launching.")
            studio.start(machine)
        elif _machine_name(studio.machine).casefold() != _machine_name(machine).casefold():
            studio.switch_machine(machine)
        if _machine_name(studio.machine) != _machine_name(machine):
            raise ConnectorError("Lightning did not confirm the requested machine after provisioning. Inspect the dedicated Studio before retrying.", code="invalid_resource")
        studio.run("mkdir -p " + shlex.quote(remote))
        studio.upload_file(str(runner), remote_path=remote + "/runner.py")
        spec = json.loads((stage / "spec.json").read_text(encoding="utf-8"))
        modules = ["torch", "torchvision", "PIL"] if spec.get("recipe") == "image" else ["torch"] if spec.get("recipe") == "pytorch" else []
        check = "import importlib.util,json; print(json.dumps([n for n in " + repr(modules) + " if importlib.util.find_spec(n) is None]))"
        missing = json.loads(studio.run("python3 -c " + shlex.quote(check)).strip().splitlines()[-1])
        if missing:
            raise ConnectorError("Install the recipe's required packages in the dedicated Studio before launch: " + ", ".join(missing), code="missing_dependency")
        # Exclusive persistent launch record survives a lost SDK acknowledgment.
        launch = ("import json,os,subprocess,sys\nfrom pathlib import Path\n"
                  f"p=Path({remote!r}).resolve()\nr=p/'launch.json'\n"
                  "try:\n f=r.open('x')\nexcept FileExistsError:\n print(r.read_text()); sys.exit(0)\n"
                  "with f:\n"
                  " log=(p/'training.log').open('w')\n"
                  " c=subprocess.Popen([sys.executable,'runner.py'],cwd=p,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)\n"
                  f" value={{'pid':c.pid,'attempt_id':{intent['attempt_id']!r}}}\n"
                  " json.dump(value,f); f.flush(); os.fsync(f.fileno())\n"
                  "print(json.dumps(value))")
        launch_attempted = True
        value = json.loads(studio.run("python3 -c " + shlex.quote(launch)).strip().splitlines()[-1])
        if value.get("attempt_id") != intent["attempt_id"] or not isinstance(value.get("pid"), int):
            raise ConnectorError("Lightning's durable launch record is incomplete. Reconnect or inspect the Studio before retrying.", code="submission_unknown", intent=intent)
        return dict(**intent, pid=value["pid"], state="running", studio_stopped=False,
                    actual_resource=dict(machine=_machine_name(studio.machine), status=str(studio.status)))
    except Exception as exc:
        if not launch_attempted:
            try:
                _stop_studio(studio, intent)
            except Exception:
                intent.setdefault("cleanup_status", "failed")
            detail = str(exc) if isinstance(exc, ConnectorError) else f"Provider setup failed ({type(exc).__name__})."
            raise ConnectorError("No Lightning training process was submitted. " + detail +
                                 (" The dedicated Studio was stopped." if intent["cleanup_status"] == "verified" else " Stop the dedicated Studio in its console to prevent continuing compute charges."),
                                 code="preflight_failed", intent=intent) from None
        if isinstance(exc, ConnectorError):
            raise
        raise _provider_error("Lightning", exc, "submission") from None


def _read_local_state(output):
    path = Path(output) / "connector-state.json"
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return dict(state="running", detail="Waiting for the worker's next durable state.")


def _stop_studio(studio, handle):
    studio.stop()
    status = str(studio.status).lower()
    if "stopped" not in status and "notcreated" not in status:
        handle["cleanup_status"] = "pending"
        raise ConnectorError("Lightning has not yet confirmed that the dedicated Studio stopped. Check its console; compute charges may continue.", code="cleanup_pending", transient=True)
    handle.update(studio_stopped=True, cleanup_status="verified")


def _ingest_logs(text, job_dir):
    job_dir = Path(job_dir)
    (job_dir / "worker.log").write_text(text[-50000:], encoding="utf-8")
    path = job_dir / "events.jsonl"
    existing = set(path.read_text(encoding="utf-8").splitlines()) if path.exists() else set()
    events = []
    for line in text.splitlines():
        start = line.find("{")
        if start < 0:
            continue
        try:
            event = json.loads(line[start:])
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") in {"started", "metric", "summary", "checkpoint", "completed", "paused", "error", "export", "heartbeat"}:
            serialized = json.dumps(event, sort_keys=True, separators=(",", ":"))
            if serialized not in existing:
                events.append(serialized)
                existing.add(serialized)
    if events:
        with path.open("a", encoding="utf-8") as stream:
            stream.write("\n".join(events) + "\n")


def _modal_snapshot(modal, handle, job_dir):
    try:
        volume = modal.Volume.from_name(handle["volume"], environment_name=handle.get("environment", "main"))
    except Exception as exc:
        raise ConnectorError(f"Modal Volume lookup failed ({type(exc).__name__}). Check native authentication and provider storage.") from None
    for name in ("events.jsonl", "checkpoint.json", "connector-state.json"):
        try:
            content = b"".join(volume.read_file(name))
        except Exception as exc:
            if isinstance(exc, FileNotFoundError) or type(exc).__name__ in {"NotFoundError", "NotFound"}:
                continue
            raise _provider_error("Modal", exc, "artifact snapshot") from None
        if name == "events.jsonl":
            _ingest_logs(content.decode("utf-8", errors="replace"), job_dir)
        else:
            target = Path(job_dir) / name
            temporary = target.with_suffix(".download.tmp")
            temporary.write_bytes(content)
            if name == "checkpoint.json":
                from .artifacts import checkpoint_assets, load_checkpoint, validate_checkpoint_payload
                try:
                    checkpoint = load_checkpoint(temporary)
                    expected = json.loads((Path(job_dir) / "spec.json").read_text(encoding="utf-8"))
                    if (expected.get("id") and checkpoint.get("job_id") != expected["id"]) or (expected.get("fingerprint") and checkpoint.get("fingerprint") != expected["fingerprint"]):
                        raise ValueError("Snapshot identity differs from the current run")
                    validate_checkpoint_payload(checkpoint, expected)
                except (ValueError, OSError):
                    temporary.unlink(missing_ok=True)
                    continue  # Keep the last good local checkpoint while a remote snapshot is incomplete.
                payload = checkpoint.get("payload", {})
                if payload.get("weights_asset"):
                    relative = _safe_relative(payload["weights_asset"])
                    asset = Path(job_dir).joinpath(*relative.parts)
                    expected_hash = payload.get("weights_sha256")
                    if not isinstance(expected_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_hash):
                        raise ConnectorError("Remote checkpoint has no valid weights checksum.")
                    if not asset.is_file() or hashlib.sha256(asset.read_bytes()).hexdigest() != expected_hash:
                        from .artifacts import atomic_bytes
                        content = b"".join(volume.read_file(relative.as_posix()))
                        if hashlib.sha256(content).hexdigest() != expected_hash:
                            raise ConnectorError("Remote checkpoint weights failed their checksum; the last good checkpoint was preserved.")
                        atomic_bytes(asset, content)
                checkpoint_assets(checkpoint, job_dir)
            temporary.replace(target)
    return _read_local_state(job_dir)


def _kaggle_state(output):
    match = re.search(r"\bstatus\s*[:=]?\s*['\"]?(complete|completed|error|failed|cancelled|canceled|running|queued|pending)\b", output, re.I)
    value = match.group(1).lower() if match else output.strip().lower()
    return {"complete": "completed", "completed": "completed", "error": "error", "failed": "error",
            "cancelled": "cancelled", "canceled": "cancelled", "running": "running", "queued": "queued", "pending": "queued"}.get(value, "unknown")


def reconcile(provider_id, config, value, job_dir):
    """Resolve saved identity using read-only calls; never launches or provisions."""
    _check_config(config)
    if not isinstance(value, dict) or value.get("provider") not in {None, provider_id}:
        return dict(state="unknown", submission_status="unknown", detail="No matching saved provider identity is available.")
    handle = dict(value)
    if handle.get("attempt_id"):
        record = _record_path(handle, job_dir)
        if record.is_file():
            data = json.loads(record.read_text(encoding="utf-8"))
            if data.get("handle"):
                handle = data["handle"]
    config = _bound_config(config, handle)
    try:
        if provider_id == "modal" and not handle.get("call_id"):
            _modal_identity(config, handle)
            if not handle.get("control"):
                return dict(state="unknown", submission_status="unknown", detail="No Modal call identity or recovery record was saved. Inspect the provider console.")
            modal = _sdk("modal", "modal")
            control = modal.Dict.from_name(handle["control"], environment_name=handle.get("environment", "main"))
            recovered = control.get("handle", None)
            if not isinstance(recovered, dict) or recovered.get("attempt_id") != handle.get("attempt_id") or not recovered.get("call_id"):
                return dict(state="unknown", submission_status="unknown", detail="Modal has not confirmed this call identity. It may still be provisioning; do not resubmit.")
            handle = {**handle, **recovered}
        elif provider_id == "lightning" and not handle.get("pid"):
            studio = _studio(config)
            _lightning_identity(config, studio)
            if "running" not in str(studio.status).lower():
                return dict(state="unknown", submission_status="unknown", detail="The saved Lightning Studio is stopped. Inspect its persistent launch record before another attempt; reconnect does not restart it.")
            remote = handle.get("remote_dir")
            if not remote:
                return dict(state="unknown", submission_status="unknown", detail="No saved Lightning run directory is available.")
            code = f"from pathlib import Path; p=Path({remote!r})/'launch.json'; print(p.read_text() if p.exists() else '{{}}')"
            recovered = json.loads(studio.run("python3 -c " + shlex.quote(code)).strip().splitlines()[-1])
            if recovered.get("attempt_id") != handle.get("attempt_id") or not isinstance(recovered.get("pid"), int):
                return dict(state="unknown", submission_status="unknown", detail="Lightning has no complete matching launch acknowledgment. Inspect the Studio before retrying.")
            handle.update(pid=recovered["pid"], studio_stopped=False)
        state = poll(provider_id, config, handle, job_dir)
        if state.get("state") == "unknown":
            return {**state, "submission_status": "unknown", "connection_status": "unknown"}
        handle["submission_status"] = "confirmed"
        if handle.get("attempt_id"):
            _json_write(_record_path(handle, job_dir), {"submission_status": "confirmed", "handle": handle})
        return {**state, "submission_status": "confirmed", "connection_status": "connected", "handle": handle}
    except ConnectorError as exc:
        if exc.code == "not_found":
            return dict(state="unknown", submission_status="unknown", connection_status="unknown",
                        detail="The provider did not find the saved execution. This is not proof that submission never happened; inspect its console before retrying.")
        raise
    except Exception as exc:
        raise _provider_error(provider_id, exc, "reconnection") from None


def poll(provider_id, config, handle, job_dir):
    _check_config(config)
    config = _bound_config(config, handle)
    if provider_id == "kaggle":
        _kaggle_identity({**config, "username": handle["kernel"].split("/")[0]})
        output = _cli("kaggle", ["kernels", "status", handle["kernel"]])
        state = _kaggle_state(output)
        detail = "Kaggle reports " + state + "."
        try:
            logs = _cli("kaggle", ["kernels", "logs", handle["kernel"]])
            _ingest_logs(logs, job_dir)
        except ConnectorError:
            detail += " Logs are temporarily unavailable; the training status was read successfully."
        return dict(state=state, detail=detail, log_path=str(Path(job_dir) / "worker.log"))
    if provider_id == "modal":
        _modal_identity(config, handle)
        modal = _sdk("modal", "modal")
        try:
            call = modal.FunctionCall.from_id(handle["call_id"])
            result = call.get(timeout=0)
            _modal_snapshot(modal, handle, job_dir)
            return result if isinstance(result, dict) else dict(state="completed")
        except ConnectorError:
            raise
        except Exception as exc:
            state = _modal_snapshot(modal, handle, job_dir)
            if isinstance(exc, TimeoutError) or type(exc).__name__ == "TimeoutError":
                return state
            if state.get("state") in {"error", "paused", "completed"}:
                return state
            if type(exc).__name__ in {"FunctionTimeoutError", "ExecutionError", "RemoteError", "UserCodeException"}:
                return dict(state="error", detail=f"Modal function failed ({type(exc).__name__}); inspect provider logs and the last committed checkpoint.")
            raise _provider_error("Modal", exc, "function status") from None
    if provider_id == "lightning":
        remote = handle["remote_dir"]
        code = ("import json,os; from pathlib import Path; "
                f"p=Path({remote!r})/'output'; "
                "s=json.loads((p/'connector-state.json').read_text()) if (p/'connector-state.json').exists() else {'state':'queued'}; "
                "s['event_log']=(p/'events.jsonl').read_text()[-200000:] if (p/'events.jsonl').exists() else ''; "
                f"pid={int(handle['pid'])}; "
                "\ntry: os.kill(pid,0)\nexcept ProcessLookupError: s={'state':'error','detail':'Worker exited before confirming its result.'} if s.get('state') in {'queued','running'} else s\n"
                "print(json.dumps(s))")
        try:
            studio = _studio(config)
            if handle.get("identity"):
                _lightning_identity(config, studio)
            if "running" not in str(studio.status).lower():
                return dict(state="unknown", detail="The saved Lightning Studio is not running. Its last durable result must be inspected before resuming.")
            state = json.loads(studio.run("python3 -c " + shlex.quote(code)).strip())
            _ingest_logs(state.pop("event_log", ""), job_dir)
            return state
        except ConnectorError:
            raise
        except Exception as exc:
            raise _provider_error("Lightning", exc, "state lookup") from None
    if provider_id == "local":
        return _read_local_state(Path(job_dir) / "output")
    return dict(state="awaiting_user", detail="The user must launch the notebook and import its outputs. No remote session is controlled by this app.")


def stop(provider_id, config, handle, job_dir):
    _check_config(config)
    config = _bound_config(config, handle)
    if provider_id == "modal":
        _modal_identity(config, handle)
        if not handle.get("control"):
            return dict(stopped=False, state="stop_unavailable", stop_mode="unavailable", cancel_available=True,
                        detail="This legacy Modal run has no cooperative pause channel. Use explicit cancellation or the provider console; cancellation cannot guarantee a final checkpoint.")
        modal = _sdk("modal", "modal")
        try:
            modal.Dict.from_name(handle["control"], create_if_missing=True, environment_name=handle.get("environment", "main")).put("pause", True)
        except Exception as exc:
            raise _provider_error("Modal", exc, "pause request") from None
        return dict(stopped=False, state="pausing", stop_mode="cooperative", detail="Modal accepted a durable pause request. The worker will save a checkpoint at its next safe boundary; a provisioning worker checks this request before training.")
    if provider_id == "lightning":
        code = ("from pathlib import Path; "
                f"p=Path({handle['remote_dir']!r})/'output'; p.mkdir(parents=True,exist_ok=True); (p/'STOP').touch()")
        try:
            studio = _studio(config)
            if handle.get("identity"):
                _lightning_identity(config, studio)
            studio.run("python3 -c " + shlex.quote(code))
        except ConnectorError:
            raise
        except Exception as exc:
            raise ConnectorError(f"Lightning pause request failed ({type(exc).__name__}). Stop the dedicated Studio from its provider console if necessary.") from None
        return dict(stopped=False, state="pausing", stop_mode="cooperative", detail="A cooperative checkpoint-and-pause request was written. Monitor until the worker confirms pause; the Studio remains running.")
    if provider_id == "local":
        output = Path(job_dir) / "output"
        output.mkdir(parents=True, exist_ok=True)
        (output / "STOP").touch()
        return dict(stopped=False, state="pausing", stop_mode="cooperative", detail="The worker will checkpoint and pause at its next safe boundary.")
    return dict(stopped=False, state="stop_unavailable", detail="Immediate remote cancellation is unavailable. Stop the notebook in the provider console; the submitted runtime cap remains in effect.",
                dashboard_url=handle.get("dashboard_url"))


def cancel(provider_id, config, handle, job_dir):
    """Explicit destructive cancellation, distinct from checkpoint-and-pause."""
    _check_config(config)
    if provider_id == "modal":
        _modal_identity(config, handle)
        if not handle.get("call_id"):
            return dict(stopped=False, state="unknown", detail="Reconnect the saved Modal attempt to recover its call ID before cancellation.")
        try:
            _sdk("modal", "modal").FunctionCall.from_id(handle["call_id"]).cancel(terminate_containers=True)
        except Exception as exc:
            raise _provider_error("Modal", exc, "cancellation") from None
        return dict(stopped=True, state="cancelled", stop_mode="immediate_cancel", detail="Modal accepted cancellation. A final checkpoint is not guaranteed; recover the last committed artifacts.")
    return dict(stopped=False, state="stop_unavailable", detail="Immediate cancellation is not implemented for this adapter. Use cooperative pause or the provider console.", dashboard_url=handle.get("dashboard_url"))


def _publish_outputs(download, job_dir):
    """Normalize provider folder wrappers into the coordinator's artifact layout."""
    from .artifacts import (IntegrityError, atomic_bytes, checkpoint_assets, export_artifacts,
                            load_checkpoint, validate_checkpoint_payload, verify_export)
    download, job_dir = Path(download), Path(job_dir)
    candidates = []
    for folder in [download, *(p for p in download.rglob("*") if p.is_dir())]:
        score = (8 if (folder / "export" / "manifest.json").is_file() else 0)
        score += 4 if (folder / "checkpoint.json").is_file() else 0
        score += 1 if (folder / "events.jsonl").is_file() else 0
        if score:
            candidates.append((score, folder))
    if not candidates:
        raise ConnectorError("The provider returned no training outputs. Check the run logs and download its output folder.")
    best = max(score for score, _ in candidates)
    matches = [folder for score, folder in candidates if score == best]
    if len(matches) != 1:
        raise ConnectorError("Provider output contains multiple training folders; select one run before importing.")
    source = matches[0]
    if source.is_symlink() or (hasattr(source, "is_junction") and source.is_junction()):
        raise ConnectorError("Symbolic links are not accepted in downloaded artifacts.")
    for path in source.rglob("*"):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ConnectorError("Symbolic links are not accepted in downloaded artifacts.")
        _safe_relative(path.relative_to(source).as_posix())
    expected = json.loads((job_dir / "spec.json").read_text(encoding="utf-8")) if (job_dir / "spec.json").exists() else {}
    checkpoint = source / "checkpoint.json"
    if checkpoint.is_file():
        checkpoint_state = load_checkpoint(checkpoint)
        if expected.get("id") and (checkpoint_state.get("job_id") != expected["id"] or checkpoint_state.get("recipe") != expected.get("recipe")):
            raise ConnectorError("Downloaded checkpoint belongs to a different training run.")
        if expected.get("fingerprint") and checkpoint_state.get("fingerprint") != expected["fingerprint"]:
            raise ConnectorError("Downloaded checkpoint uses different training inputs.")
        try:
            validate_checkpoint_payload(checkpoint_state, expected)
            for name, asset in checkpoint_assets(checkpoint_state, source).items():
                relative = _safe_relative(name)
                contents = asset.read_bytes()
                if hashlib.sha256(contents).hexdigest() != checkpoint_state["payload"]["weights_sha256"]:
                    raise IntegrityError("Checkpoint base weights changed during collection")
                atomic_bytes(job_dir.joinpath(*relative.parts), contents)
        except IntegrityError as exc:
            raise ConnectorError(str(exc)) from exc
    if (source / "export" / "manifest.json").is_file():
        manifest = verify_export(source / "export")
        if expected.get("id") and manifest.get("metadata", {}).get("job_id") != expected["id"]:
            raise ConnectorError("Downloaded model belongs to a different training run.")
        files = {entry["path"]: source / "export" / entry["path"] for entry in manifest["files"]}
        export_artifacts(files, job_dir / "export", manifest.get("metadata", {}),
                         expected_hashes={entry["path"]: entry["sha256"] for entry in manifest["files"]})
    if checkpoint.is_file():
        atomic_bytes(job_dir / "checkpoint.json", checkpoint.read_bytes())
    if (source / "events.jsonl").is_file():
        _ingest_logs((source / "events.jsonl").read_text(encoding="utf-8"), job_dir)
    if (source / "connector-state.json").is_file():
        atomic_bytes(job_dir / "connector-state.json", (source / "connector-state.json").read_bytes())
    return job_dir


def collect(provider_id, config, handle, job_dir):
    _check_config(config)
    config = _bound_config(config, handle)
    job_dir = Path(job_dir)
    if provider_id == "local":
        return _publish_outputs(job_dir / "output", job_dir)
    if provider_id not in {"kaggle", "modal", "lightning"}:
        raise ConnectorError("Download the assisted notebook's output folder from its provider and import it into this app.")
    with tempfile.TemporaryDirectory(prefix="download-", dir=job_dir) as temporary:
        output = Path(temporary)
        if provider_id == "kaggle":
            _kaggle_identity({**config, "username": handle["kernel"].split("/")[0]})
            pattern = r"^(?:output/)?(?:checkpoint(?:\.previous)?\.json|events\.jsonl|connector-state\.json|assets/base_weights\.pth|export/(?!.*\.\.)[A-Za-z0-9_./ -]+)$"
            _cli("kaggle", ["kernels", "output", handle["kernel"], "-p", str(output), "--force", "--file-pattern", pattern], timeout=300)
        elif provider_id == "lightning":
            studio = _studio(config)
            if handle.get("identity"):
                _lightning_identity(config, studio)
            try:
                studio.download_folder(remote_path=handle["remote_dir"] + "/output", target_path=str(output))
                return _publish_outputs(output, job_dir)
            except ConnectorError:
                raise
            except Exception as exc:
                raise ConnectorError(f"Lightning artifact recovery failed ({type(exc).__name__}). Outputs remain in the Studio's persistent storage; download them manually.") from None
            finally:
                if handle.get("dedicated_studio") is True and handle.get("stop_studio_after_run") is True and not handle.get("studio_stopped"):
                    try:
                        _stop_studio(studio, handle)
                    except ConnectorError:
                        raise
                    except Exception as exc:
                        handle["cleanup_status"] = "failed"
                        raise ConnectorError(f"Dedicated Lightning Studio could not be stopped ({type(exc).__name__}). Stop it in the provider console to prevent further compute charges; retrieve persistent artifacts there.") from None
        else:
            _modal_identity(config, handle)
            modal = _sdk("modal", "modal")
            try:
                volume = modal.Volume.from_name(handle["volume"], environment_name=handle.get("environment", "main"))
                for entry in volume.iterdir("/", recursive=True):
                    if int(entry.type) != 1:  # Modal FileEntryType.FILE is 1.
                        continue
                    relative = _safe_relative(entry.path.lstrip("/"))
                    target = output.joinpath(*relative.parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with target.open("wb") as stream:
                        for chunk in volume.read_file(entry.path):
                            stream.write(chunk)
            except ConnectorError:
                raise
            except Exception as exc:
                raise ConnectorError(f"Modal artifacts could not be downloaded ({type(exc).__name__}). They remain in the provider Volume.") from None
        return _publish_outputs(output, job_dir)
