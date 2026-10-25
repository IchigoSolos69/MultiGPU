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
from urllib.parse import urlsplit, urlunsplit

from .catalog import MODAL_NOTEBOOK_RETIREMENT, get_provider

_MAX_STAGE_BYTES = 512 * 1024 * 1024

class ConnectorError(RuntimeError):
    """An actionable failure; remote execution must not be inferred from it."""

    def __init__(self, message, *, code="provider", transient=False, intent=None, diagnostic=None,
                 submission_outcome=None, submission_evidence=None):
        super().__init__(message)
        self.code, self.transient, self.intent = code, transient, intent
        self.diagnostic = diagnostic
        if submission_outcome not in (None, "not_started", "unknown", "confirmed"):
            raise ValueError("Invalid submission outcome")
        self.submission_outcome = submission_outcome
        self.submission_evidence = submission_evidence


_SECRET_FIELD = re.compile(r"(?i)(authorization|cookie|password|credential|token|secret|api[_-]?key|private[_-]?key|(?:^|[_-])key$)")


def redact_diagnostic(value):
    if isinstance(value, dict):
        return {str(key): "[credential value redacted]" if _SECRET_FIELD.search(str(key)) else redact_diagnostic(item)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_diagnostic(item) for item in value]
    return redact_provider_output(value) if isinstance(value, (str, bytes)) else value


def redact_provider_output(value):
    """Keep useful provider errors while removing credentials before persistence."""
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    if text.lstrip().startswith(("{", "[")):
        try:
            return json.dumps(redact_diagnostic(json.loads(text)), ensure_ascii=False)[-8000:]
        except (ValueError, TypeError):
            pass
    def url(match):
        try:
            parsed = urlsplit(match.group())
            host = parsed.hostname or ""
            if parsed.port:
                host += ":" + str(parsed.port)
            return urlunsplit((parsed.scheme, host, parsed.path, "[redacted]" if parsed.query else "", ""))
        except ValueError:
            return "[redacted URL]"
    text = re.sub(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s<>\"']+", url, text)
    field = re.compile(r"(?i)(?:authorization|cookie|[A-Za-z0-9_.-]*(?:password|credential|token|secret|api[_-]?key|private[_-]?key|[_-]key)|\bkey)\s*[\"']?\s*[:=]")
    lines, hide_next = [], False
    for line in text.splitlines():
        if field.search(line):
            lines.append("[credential field redacted]")
            hide_next = bool(re.search(r"[:=]\s*$", line))
        elif hide_next:
            lines.append("[credential value redacted]")
            hide_next = not bool(line.strip())
        else:
            lines.append(line)
    text = "\n".join(lines)
    text = re.sub(r"(?i)\b(?:bearer|basic)\s+\S+", "[authorization redacted]", text)
    text = re.sub(r"\b(?:KGAT_|kgat_)[A-Za-z0-9_.-]+", "[token redacted]", text)
    text = re.sub(r"\beyJ[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+){1,2}", "[token redacted]", text)
    text = re.sub(r"[A-Za-z0-9_+/=-]{28,}", "[opaque value redacted]", text)
    return text[-8000:]


def _provider_error(provider, exc, action):
    name = type(exc).__name__
    lowered = name.lower()
    status = getattr(exc, "status_code", None)
    diagnostic = {"client": provider, "operation": action, "exception_type": name,
                  "error": redact_provider_output(str(exc))}
    if status in {401, 403} or any(word in lowered for word in ("auth", "permission", "credential")):
        return ConnectorError(f"{provider} authentication or permission check failed. Sign in with its native tools again.", code="auth", diagnostic=diagnostic)
    if status == 404 or name in {"NotFoundError", "NotFound"}:
        return ConnectorError(f"{provider} did not find the saved resource during {action}. Inspect its console before retrying.", code="not_found", diagnostic=diagnostic)
    if status in {429, 500, 502, 503, 504} or any(word in lowered for word in ("connection", "timeout", "service", "unavailable")):
        return ConnectorError(f"{provider} is temporarily unreachable during {action}; the remote execution may still be running.", code="network", transient=True, diagnostic=diagnostic)
    return ConnectorError(f"{provider} {action} failed ({name}). Inspect its console; private provider error text was withheld.", diagnostic=diagnostic)


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
    # Setup and execution must use the same app-owned interpreter, never PATH.
    command = [sys.executable, "-m", name, *args]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                                encoding="utf-8", errors="replace", check=False,
                                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as exc:
        raise ConnectorError(f"{name} did not respond within {timeout} seconds. Check the provider console before retrying a submission.", code="network", transient=True,
                             diagnostic={"client": name, "command": [name], "python": sys.executable, "timeout_seconds": timeout,
                                         "exception_type": type(exc).__name__, "stdout": redact_provider_output(exc.stdout or ""),
                                         "stderr": redact_provider_output(exc.stderr or "")}) from None
    except OSError as exc:
        raise ConnectorError(f"Could not start {name}. Install its current CLI in this Python environment.", code="missing_dependency",
                             submission_outcome="not_started", submission_evidence={"stage": "client_spawn", "reason": "Operating system did not start the provider client."},
                             diagnostic={"client": name, "command": [name], "python": sys.executable,
                                         "exception_type": type(exc).__name__, "error": redact_provider_output(str(exc))}) from None
    rejected = name == "kaggle" and args[:2] == ["kernels", "push"] and bool(re.search(r"(?im)^kernel push error:", result.stdout))
    if result.returncode or rejected:
        # Classify known failures; never persist or echo provider error text.
        diagnostic = (result.stdout + result.stderr).lower()
        if any(x in diagnostic for x in ("no module named", "cannot be directly executed")):
            code, detail = "missing_dependency", "The provider client is missing or incomplete in this app. Open provider setup and prepare its tools again."
        elif any(x in diagnostic for x in ("unrecognized arguments", "invalid choice:", "unrecognized argument")):
            code, detail = "unsupported_cli", "The app's provider client does not support this command. Open provider setup and update its tools."
        elif any(x in diagnostic for x in ("cannot access kernel", "permission", "forbidden", "403")):
            code, detail = "permission", "Kaggle denied access to this notebook or action. Check the selected account and notebook URL in Kaggle. A missing or private notebook can produce the same denial."
        elif any(x in diagnostic for x in ("unauthorized", "invalid credentials", "401", "auth login", "could not find kaggle.json", "not authenticated")):
            code, detail = "auth", "Native authentication is missing or expired. Open provider setup and sign in with this app's native client again."
        elif "404" in diagnostic or "not found" in diagnostic or "could not find kernel" in diagnostic:
            code, detail = "not_found", "The provider did not find the saved notebook. Check its URL and account; this does not prove a previous submission never started."
        elif any(x in diagnostic for x in ("timeout", "connection", "temporarily", "503", "502", "504", "429")):
            code, detail = "network", "The provider is temporarily unreachable or rate-limited. Recheck the saved execution before retrying any launch."
        elif "quota" in diagnostic and any(x in diagnostic for x in ("exceed", "exhaust", "limit")):
            code, detail = "quota", "The provider rejected the request because of an account or compute quota. Check the provider's current allowance."
        elif any(x in diagnostic for x in ("accelerator", "machine_shape", "machine shape", "capacity")):
            code, detail = "invalid_resource", "The provider rejected the requested accelerator or has no available capacity. Check the available compute in its console."
        elif any(x in diagnostic for x in ("400", "bad request", "invalid argument", "validationerror")):
            code, detail = "invalid_resource", "The provider rejected the submitted notebook settings. Check its runtime permissions and configuration in the provider console."
        else:
            code, detail = "provider", "The provider client failed. Recheck the saved attempt and provider setup before another submission."
        no_dispatch = bool(
            code == "missing_dependency" and re.search(r"(?im)^(?:[^\r\n]*python[0-9.]*(?:\.exe)?:\s*)?No module named ['\"]?" + re.escape(name) + r"(?:\.__main__)?(?:['\";\s]|$)", result.stderr)
            or code == "unsupported_cli" and result.returncode == 2 and re.search(r"(?im)^(?:[\w. -]+:\s*error:\s*)?(?:unrecognized arguments?:|invalid choice:)", result.stderr))
        raise ConnectorError(f"{detail} Client exit code: {result.returncode}. Private provider text was withheld.",
                             code=code, transient=code == "network",
                             submission_outcome="not_started" if no_dispatch else None,
                             submission_evidence={"stage": "client_initialization", "reason": "The Python client could not load or rejected its command-line syntax before dispatch."} if no_dispatch else None,
                             diagnostic={"client": name, "error_code": code, "command": [name, *[a for a in args[:2] if a in {"quota", "config", "view", "kernels", "push", "status", "logs", "output"}]],
                                         "python": sys.executable, "exit_code": result.returncode,
                                         "stdout": redact_provider_output(result.stdout), "stderr": redact_provider_output(result.stderr)})
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
    if not match or match.group(1).casefold() in {"none", "null"}:
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
            result.update(detail="Follow the in-app setup guide, download the training notebook, and run it in your provider account under its current terms.")
        if result["account_verified"] or provider_id == "local":
            result["connection_status"] = "connected"
    except ConnectorError as exc:
        result["detail"] = str(exc)
        result["error_code"] = exc.code
        if exc.diagnostic:
            result["diagnostic"] = exc.diagnostic
        result["connection_status"] = "auth_required" if exc.code in {"auth", "account_mismatch"} else "retrying" if exc.transient else "unknown"
    except Exception as exc:
        result["detail"] = f"Provider verification failed ({type(exc).__name__}). Check native authentication and account permissions."
    return result


_RUNNER = '''import json, os
from pathlib import Path
from multigpu.worker import run
base = Path(__file__).resolve().parent
spec = json.loads(Path(os.environ.get("MULTIGPU_SPEC_PATH", base / "spec.json")).read_text(encoding="utf-8"))
for key in ("dataset_path", "script_path", "resume_from", "weights_path"):
    if spec.get(key):
        spec[key] = str(Path(spec[key]) if Path(spec[key]).is_absolute() else base / spec[key])
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
    for filename in ("worker.py", "artifacts.py", "datasets.py", "recipes.py", "prediction.py", "notebook_runtime.py", "performance.py", "image_pipeline.py", "staging.py"):
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


def _stage_for_provider(provider_id, spec_path, job_dir):
    stage, spec = _stage(spec_path, job_dir)
    # GPU indices identify devices on one host. Never send the laptop's index to
    # a different runtime, including a notebook that resumes a local checkpoint.
    spec["execution_provider"] = provider_id
    if provider_id != "local":
        spec["gpu_index"] = 0
    resource = spec.get("resolved_resource", {})
    if resource.get("provider_id") == provider_id and resource.get("device") in {"cpu", "cuda"}:
        spec["device"] = resource["device"]
    _json_write(stage / "spec.json", spec)
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


def _embedded(stage, *, notebook=False):
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, relative in _payload_files(stage):
            archive.write(path, relative)
    encoded = base64.b64encode(payload.getvalue()).decode("ascii")
    if notebook:
        from .notebook_runtime import job_key
        spec = json.loads((stage / "spec.json").read_text(encoding="utf-8"))
        key = job_key(spec)
        digest = hashlib.sha256(payload.getvalue()).hexdigest()
        prefix = ("RECOVERY_DIR = globals().get('RECOVERY_DIR')  # The optional setup cell owns this choice.\n"
                  "import base64, io, os, zipfile\nfrom pathlib import Path\n"
                  "root = Path.cwd()\n"
                  f"base = root / 'multigpu-runs' / {key!r} / {'payload-' + digest[:16]!r}\n"
                  "for parent in [base, *base.parents]:\n"
                  "    if parent.is_symlink() or (hasattr(parent, 'is_junction') and parent.is_junction()):\n"
                  "        raise ValueError('Symbolic links are not accepted in notebook output folders')\n"
                  "base.mkdir(parents=True, exist_ok=True)\n")
        suffix = ("import sys\nsys.path.insert(0, str(base))\n"
                  "from multigpu.notebook_runtime import launch_notebook\n"
                  "MULTIGPU_NOTEBOOK_OUTPUT = launch_notebook(base, root, recovery_dir=RECOVERY_DIR)\n")
    else:
        prefix = ("import base64, io, os, runpy, zipfile\nfrom pathlib import Path\n"
                  "base = Path.cwd() / 'multigpu-payload'\nbase.mkdir(exist_ok=True)\n")
        suffix = ("import sys\nsys.path.insert(0, str(base))\n"
                  "runpy.run_path(str(base / 'run.py'), run_name='__main__')\n")
    return (prefix +
            f"archive = zipfile.ZipFile(io.BytesIO(base64.b64decode({encoded!r})))\n"
            "for item in archive.infolist():\n"
            "    target = (base / item.filename).resolve()\n"
            "    if not target.is_relative_to(base.resolve()) or ((item.external_attr >> 16) & 0o170000) == 0o120000:\n"
            "        raise ValueError('Unsafe bundle member')\n"
            "archive.extractall(base)\n"
            + suffix)


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


_EXTERNAL_PAYLOAD_RUNNER = r'''import hashlib, json, os, runpy, sys, zipfile
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
base = root / "multigpu-runs" / NOTEBOOK_JOB_KEY / ("payload-" + EXPECTED_MANIFEST[:16])
for parent in [base, *base.parents]:
    if parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction()):
        raise ValueError("Symbolic links are not accepted in notebook output folders")
base.mkdir(parents=True, exist_ok=True)
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
from multigpu.notebook_runtime import launch_notebook
MULTIGPU_NOTEBOOK_OUTPUT = launch_notebook(base, root, recovery_dir=RECOVERY_DIR)
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
    from .notebook_runtime import job_key
    introduction += ("\nOptional durable recovery is off by default. In the setup cell, enable Colab Drive recovery and supply an existing folder, "
                     "or set RECOVERY_DIR to your own already mounted folder. Failed configured recovery pauses training; old verified copies are kept.\n")
    recovery_setup = ("# Optional Colab Drive recovery. This cell does not access Drive unless enabled.\n"
                      "ENABLE_DRIVE_RECOVERY = False\nDRIVE_RECOVERY_FOLDER = ''  # Set an existing folder, e.g. /content/drive/MyDrive/MultiGPU\n"
                      "RECOVERY_DIR = None  # Or set your own already mounted folder.\n"
                      "if ENABLE_DRIVE_RECOVERY:\n"
                      "    if not DRIVE_RECOVERY_FOLDER:\n        raise ValueError('Set DRIVE_RECOVERY_FOLDER to your existing recovery folder first')\n"
                      "    from google.colab import drive\n    drive.mount('/content/drive')\n"
                      "    from pathlib import Path\n    recovery = Path(DRIVE_RECOVERY_FOLDER).expanduser()\n"
                      "    if not recovery.is_dir():\n        raise FileNotFoundError('Create the chosen recovery folder in Drive before enabling recovery')\n"
                      "    RECOVERY_DIR = str(recovery.resolve())\n")
    save = ("from pathlib import Path\nfrom multigpu.notebook_runtime import package_results\n"
            f"output = Path.cwd() / 'multigpu-runs' / {job_key(spec)!r} / 'output'\n"
            "result = package_results(output)\n"
            "print('Download and import this verified results file:', result)\n"
            "try:\n    from google.colab import files\nexcept ImportError:\n    pass\nelse:\n    files.download(str(result))\n")
    def cell(kind, content):
        value = {"cell_type": kind, "metadata": {}, "source": content.splitlines(True)}
        if kind == "code":
            value.update(execution_count=None, outputs=[])
        return value
    return {"cells": [cell("markdown", introduction), cell("code", recovery_setup), cell("code", source),
                      cell("markdown", "## Save results\nAfter training completes or pauses, run this cell and download multigpu-results.zip. It contains the checkpoint, model files and required image weights. Keep it until the app verifies the import.\n"),
                      cell("code", save)],
            "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"}},
            "nbformat": 4, "nbformat_minor": 5}


def _remove_notebook_stage(stage, job_dir):
    stage = Path(stage)
    if stage.is_symlink() or (hasattr(stage, "is_junction") and stage.is_junction()) or stage.resolve().parent != Path(job_dir).resolve() or not stage.name.startswith("payload-"):
        raise ConnectorError("Refusing cleanup outside the prepared notebook directory.")
    shutil.rmtree(stage)


def _require_notebook_provider(provider_id):
    if not get_provider(provider_id)["capabilities"].get("notebook_bundle"):
        detail = MODAL_NOTEBOOK_RETIREMENT if provider_id == "modal" else "This resource is not a notebook training backend."
        raise ConnectorError(detail, code="notebook_retired" if provider_id == "modal" else "notebook_unavailable")


def build_notebook(provider_id, config, spec_path, job_dir):
    """Return the actual runnable notebook; setup instructions remain in the app."""
    _require_notebook_provider(provider_id)
    _check_config(config)
    from .staging import asset_key, reusable, remember
    key = asset_key(provider_id, spec_path)
    target = Path(job_dir) / "training.ipynb"
    if reusable(target, key):
        return target
    original = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    separate = needs_payload(original, Path(spec_path).parent)
    stage, spec = _stage_for_provider(provider_id, spec_path, job_dir)
    try:
        if separate:
            digest = hashlib.sha256(_training_manifest(stage)).hexdigest()
            from .notebook_runtime import job_key
            source = "RECOVERY_DIR = globals().get('RECOVERY_DIR')  # The optional setup cell owns this choice.\nNOTEBOOK_JOB_KEY = " + repr(job_key(spec)) + "\nEXPECTED_MANIFEST = " + repr(digest) + "\n" + _EXTERNAL_PAYLOAD_RUNNER
        else:
            source = _embedded(stage, notebook=True)
        from .artifacts import atomic_json
        target = Path(job_dir) / "training.ipynb"
        atomic_json(target, _notebook_document(source, spec, separate))
        if target.stat().st_size >= 8 * 1024 * 1024:
            raise ConnectorError("This notebook exceeds the safe import size. Reduce selected inputs before preparing it again.")
        if asset_key(provider_id, spec_path) != key:
            raise ConnectorError("Inputs changed during preparation. Preview training again.")
        remember(target, key)
        return target
    finally:
        _remove_notebook_stage(stage, job_dir)


def build_notebook_payload(provider_id, config, spec_path, job_dir):
    """Return selected training inputs, never an instruction archive."""
    _require_notebook_provider(provider_id)
    _check_config(config)
    original = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    if not needs_payload(original, Path(spec_path).parent):
        raise ConnectorError("This small job's notebook already includes its training inputs; no separate payload is needed.")
    from .staging import asset_key, reusable, remember
    key = asset_key(provider_id, spec_path)
    target = Path(job_dir) / "training-payload.zip"
    if reusable(target, key):
        return target
    stage, _ = _stage_for_provider(provider_id, spec_path, job_dir)
    target = Path(job_dir) / "training-payload.zip"
    temporary = target.with_suffix(".zip.tmp")
    try:
        manifest = _training_manifest(stage)
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("multigpu-payload-manifest.json", manifest)
            for file, relative in _payload_files(stage):
                archive.write(file, relative)
        os.replace(temporary, target)
        if asset_key(provider_id, spec_path) != key:
            raise ConnectorError("Inputs changed during preparation. Preview training again.")
        remember(target, key)
    finally:
        temporary.unlink(missing_ok=True)
        _remove_notebook_stage(stage, job_dir)
    return target


def build_bundle(provider_id, config, spec_path, job_dir):
    """Create a self-contained notebook ZIP; this does not start remote compute."""
    _require_notebook_provider(provider_id)
    _check_config(config)
    stage, spec = _stage_for_provider(provider_id, spec_path, job_dir)
    try:
        source = _embedded(stage, notebook=True)
        notebook = _notebook_document(source, spec, False)
        (stage / "training.ipynb").write_text(json.dumps(notebook), encoding="utf-8")
        (stage / "README.md").write_text(
            "Extract training.ipynb from this ZIP, upload it, and run its cell.\n"
            "The notebook embeds worker.py, artifacts.py, spec.json and selected input files exactly once.\n"
            "Choose compute and configure billing directly with your provider.\n"
            "This bundle does not keep runtimes alive or connect them to a distributed cluster.\n"
            "Use the scoped output folder printed by the worker; its STOP file requests a graceful pause.\n"
            "Custom recipes need their dependencies installed and must be standalone Python scripts.\n"
            "Outputs remain with the provider until you download/import them.\n", encoding="utf-8")
        target = Path(job_dir) / "notebook-bundle.zip"
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(stage / "training.ipynb", "training.ipynb")
            archive.write(stage / "README.md", "README.md")
        return target

    finally:
        _remove_notebook_stage(stage, job_dir)


def _modal_resource(spec):
    resource = spec.get("resolved_resource") or {}
    if (resource.get("provider_id") != "modal" or resource.get("device") not in {"cpu", "cuda"}
            or resource["device"] == "cuda" and (not isinstance(resource.get("gpu"), str) or not resource["gpu"].strip())
            or resource["device"] == "cpu" and resource.get("gpu") is not None
            or spec.get("recipe", "starter") == "starter" and resource["device"] != "cpu"):
        raise ConnectorError("Preview this Modal resource again; its resolved CPU/GPU allocation is missing or invalid.", code="invalid_resource")
    return dict(resource)


def _paid_policy(config, spec):
    if spec.get("free_only", config.get("free_only", True)):
        raise ConnectorError("Automatic free-only execution is blocked for this credit-backed provider: this app cannot independently verify an enforceable zero-spend cap. Choose local compute or another assisted notebook provider, or explicitly choose paid execution with native billing controls.")
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
    try:
        return _prepare(provider_id, config, spec_path, job_dir)
    except ConnectorError as exc:
        if exc.submission_outcome is None:
            exc.submission_outcome = "not_started"
            exc.submission_evidence = {"stage": "preflight", "reason": "Preparation does not launch provider training."}
        raise
    except Exception as exc:
        raise ConnectorError(f"Submission preparation failed ({type(exc).__name__}); no provider launch was attempted.", code="preflight_failed",
                             submission_outcome="not_started", submission_evidence={"stage": "preflight", "reason": "Local preparation failed before provider mutation."}) from None


def _prepare(provider_id, config, spec_path, job_dir):
    """Read-only remote preflight and local staging; safe to persist before submit."""
    get_provider(provider_id)
    _check_config(config)
    job_dir = Path(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(Path(spec_path).read_bytes()).hexdigest()
    saved = job_dir / ("intent-" + provider_id + ".json")
    if saved.is_file():
        try:
            prior = json.loads(saved.read_text(encoding="utf-8"))
            if not isinstance(prior, dict):
                raise ValueError("Invalid saved intent")
        except (OSError, ValueError):
            raise ConnectorError("The existing provider intent cannot be read. Inspect that attempt before preparing another launch.", code="submission_unknown", submission_outcome="unknown",
                                 submission_evidence={"stage": "existing_journal", "reason": "The saved submission identity cannot be verified."}) from None
        if prior.get("spec_sha256") == digest:
            prior_record = _record_path(prior, job_dir)
            try:
                prior_data = json.loads(prior_record.read_text(encoding="utf-8")) if prior_record.is_file() else {}
                if not isinstance(prior_data, dict):
                    raise ValueError("Invalid submission journal")
                prior_status = prior_data.get("submission_status")
            except (OSError, ValueError):
                raise ConnectorError("The existing submission journal cannot be read. Inspect that attempt before preparing another launch.", code="submission_unknown", intent=prior,
                                     submission_outcome="unknown", submission_evidence={"stage": "existing_journal", "reason": "The saved submission outcome cannot be verified."}) from None
            if prior_status not in {"not_submitted", "not_started"}:
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
        resource = _modal_resource(spec)
        intent.update(identity=_modal_identity(config), app_name="multigpu-" + attempt,
                      volume="multigpu-" + attempt, control="multigpu-control-" + attempt,
                      environment=str(config.get("environment") or "main"), requested_gpu=resource.get("gpu"), resolved_resource=resource)
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
        stage, staged_spec = _stage_for_provider(provider_id, spec_path, job_dir)
        if provider_id == "kaggle":
            staged_spec["runtime_seconds"] = intent["runtime_seconds"]
            _json_write(stage / "spec.json", staged_spec)
        intent["stage_path"] = str(stage.resolve())
    _json_write(saved, intent)
    return intent


def submit(provider_id, config, spec_path, job_dir, intent=None):
    """Return a confirmed handle, or carry explicit evidence of launch uncertainty."""
    try:
        return _submit(provider_id, config, spec_path, job_dir, intent)
    except ConnectorError as exc:
        if exc.submission_outcome is None:
            exc.submission_outcome = "not_started"
            exc.submission_evidence = {"stage": "preflight", "reason": "Failed before a provider launch was attempted."}
        exc.intent = exc.intent or intent
        raise
    except Exception as exc:
        raise ConnectorError(f"Could not prepare submission ({type(exc).__name__}); no provider launch was attempted.",
                             code="preflight_failed", intent=intent, submission_outcome="not_started",
                             submission_evidence={"stage": "preflight", "reason": "Local preparation failed before provider mutation."}) from None


def _submit(provider_id, config, spec_path, job_dir, intent=None):
    get_provider(provider_id)
    _check_config(config)
    job_dir = Path(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    if provider_id not in {"local", "kaggle", "modal", "lightning"}:
        bundle = build_bundle(provider_id, config, spec_path, job_dir)
        return dict(provider=provider_id, state="awaiting_user", bundle_path=str(bundle),
                    detail="Training files prepared locally. Follow the in-app guide; no provider runtime has been launched.")
    intent = intent or prepare(provider_id, config, spec_path, job_dir)
    if intent.get("provider") != provider_id or intent.get("spec_sha256") != hashlib.sha256(Path(spec_path).read_bytes()).hexdigest():
        raise ConnectorError("The submission intent does not match this provider and job specification.", code="intent_mismatch")
    record = _record_path(intent, job_dir)
    if record.exists():
        try:
            previous = json.loads(record.read_text(encoding="utf-8"))
            if not isinstance(previous, dict):
                raise ValueError("Invalid submission journal")
        except (OSError, ValueError):
            raise ConnectorError("The existing submission journal cannot be read. Inspect that attempt before retrying.", code="submission_unknown", intent=intent,
                                 submission_outcome="unknown", submission_evidence={"stage": "existing_journal", "reason": "The saved submission outcome cannot be verified."}) from None
        if previous.get("submission_status") == "confirmed":
            confirmed = previous.get("handle")
            if not _matching_attempt(confirmed, intent, ("provider", "attempt_id", "job_id", "spec_sha256")):
                raise ConnectorError("The saved acknowledgment has no matching execution identity. Reconcile this attempt before retrying.", code="submission_unknown", intent=intent,
                                     submission_outcome="unknown", submission_evidence={"stage": "existing_journal", "reason": "A launch was acknowledged but its handle cannot be verified."})
            return confirmed
        if previous.get("submission_status") in {"not_started", "not_submitted"}:
            raise ConnectorError("This attempt did not start. Prepare a fresh attempt after fixing its setup.", code="not_started", intent=intent,
                                 submission_outcome="not_started", submission_evidence=previous.get("submission_evidence"))
        raise ConnectorError("This attempt may already have launched. Reconnect to its saved provider identity; automatic resubmission is blocked.", code="submission_unknown", intent=intent,
                             submission_outcome="unknown", submission_evidence={"stage": "existing_journal", "reason": "This attempt already has an unresolved submission claim."})
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
        raise ConnectorError("Another submission owns this attempt. Reconnect before retrying.", code="submission_unknown", intent=intent,
                             submission_outcome="unknown", submission_evidence={"stage": "exclusive_claim", "reason": "Another submitter claimed this attempt."}) from None
    mutation_started = False
    def mutation():
        nonlocal mutation_started
        mutation_started = True
    try:
        if provider_id == "kaggle":
            slug = intent["kernel"].split("/")[1]
            metadata = dict(id=intent["kernel"], title=slug, code_file="train.py", language="python",
                            kernel_type="script", is_private=True, enable_gpu=True, enable_internet=True,
                            dataset_sources=[], competition_sources=[], kernel_sources=[])
            _json_write(stage / "kernel-metadata.json", metadata)
            (stage / "train.py").write_text(_embedded(stage), encoding="utf-8")
            mutation()
            _cli("kaggle", ["kernels", "push", "-p", str(stage), "--timeout", str(intent["timeout_seconds"]),
                            "--accelerator", "NvidiaTeslaT4"], timeout=120)
            handle = dict(**intent, state="queued", dashboard_url=f"https://www.kaggle.com/code/{intent['kernel']}")
        elif provider_id == "modal":
            handle = _submit_modal(config, spec, stage, intent, on_mutation=mutation)
        elif provider_id == "lightning":
            handle = _submit_lightning(config, stage, intent, on_mutation=mutation)
        else:
            log = (job_dir / "training.log").open("w", encoding="utf-8")
            try:
                try:
                    child = subprocess.Popen([sys.executable, str((stage / "run.py").resolve())], cwd=job_dir, stdout=log, stderr=subprocess.STDOUT)
                except OSError:
                    # Popen reports an OS-level failure before creating the child.
                    raise ConnectorError("The operating system could not start the local worker.", code="worker_start_failed", submission_outcome="not_started") from None
                mutation()
            finally:
                log.close()
            handle = dict(**intent, pid=child.pid, state="running")
        handle["submission_status"] = "confirmed"
        handle["submission_evidence"] = {"outcome": "confirmed", "stage": "provider_acknowledgment", "attempt_id": intent["attempt_id"]}
        try:
            _json_write(record, {**intent, "submission_status": "confirmed", "handle": handle})
        except OSError:
            # The provider already returned its exact identity. Preserve it for
            # the service to persist, even if this redundant journal is full.
            handle["journal_warning"] = "The provider acknowledged this attempt, but its local submission journal could not be updated."
        return handle
    except Exception as exc:
        proven = isinstance(exc, ConnectorError) and exc.submission_outcome == "not_started"
        outcome = "not_started" if proven or not mutation_started else "unknown"
        evidence = {"outcome": outcome, "stage": "provider_mutation" if mutation_started else "local_preparation", "attempt_id": intent["attempt_id"],
                    "reason": "No launch was dispatched." if outcome == "not_started" else "A provider mutation was attempted without a complete acknowledgment."}
        if isinstance(exc, ConnectorError) and exc.submission_evidence:
            evidence.update(exc.submission_evidence)
        try:
            _json_write(record, {**intent, "submission_status": outcome, "submission_evidence": evidence})
        except OSError:
            pass  # Preserve the original outcome/evidence even on a full drive.
        if outcome == "not_started":
            if isinstance(exc, ConnectorError):
                exc.submission_outcome, exc.submission_evidence, exc.intent = outcome, evidence, intent
                raise
            raise ConnectorError(f"Local submission preparation failed ({type(exc).__name__}); no provider launch was attempted.", code="preflight_failed", intent=intent,
                                 submission_outcome=outcome, submission_evidence=evidence) from None
        raise ConnectorError("Submission acknowledgment was not saved. The provider may have started this attempt; reconnect before retrying. " + (str(exc) if isinstance(exc, ConnectorError) else f"Provider failure: {type(exc).__name__}."), code="submission_unknown", intent=intent,
                             submission_outcome="unknown", submission_evidence=evidence,
                             diagnostic=exc.diagnostic if isinstance(exc, ConnectorError) else None) from None


def _submit_modal(config, spec, stage, intent, on_mutation=lambda: None):
    resource = _modal_resource(spec)
    if intent.get("resolved_resource") != resource or intent.get("requested_gpu") != resource.get("gpu"):
        raise ConnectorError("The saved Modal intent differs from the reviewed resource; preview again before launching.", code="intent_mismatch")
    modal = _sdk("modal", "modal")
    name = intent["volume"]
    environment = intent.get("environment", "main")
    on_mutation()
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
    if resource["device"] == "cuda":
        options["gpu"] = resource["gpu"]

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
        sys.path.insert(0, "/payload")
        from multigpu.notebook_runtime import publish_progress
        progress_cursor = {}
        progress_lock = threading.Lock()
        done = threading.Event()
        def flush():
            last_commit = time.monotonic()
            while not done.is_set():
                try:
                    if control.get("pause", False):
                        Path("/output/STOP").touch()
                    if time.monotonic() - last_commit >= 30:
                        with progress_lock:
                            publish_progress("/output", progress_cursor)
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
            with progress_lock:
                publish_progress("/output", progress_cursor)
                volume.commit()

    try:
        with app.run(detach=True, environment_name=environment):
            intent["app_id"] = app.app_id
            call = train.spawn()
            handle = {**intent, "call_id": call.object_id, "state": "queued", "submission_status": "confirmed"}
            control.put("handle", handle)
            return handle
    except Exception as exc:
        diagnostic = exc.diagnostic if isinstance(exc, ConnectorError) else _provider_error("Modal", exc, "submission").diagnostic
        raise ConnectorError(f"Modal submission was not acknowledged ({type(exc).__name__}). Reconnect using this attempt's control record; a function may be running.", code="submission_unknown", intent=intent,
                             diagnostic=diagnostic, submission_outcome="unknown") from None


def _lightning_consent(config):
    if config.get("dedicated_studio") is not True or config.get("stop_studio_after_run") is not True:
        raise ConnectorError("Lightning automatic execution requires a dedicated Studio and explicit permission to stop it after artifact collection. Use the in-app notebook guide for a shared Studio.")


def _submit_lightning(config, stage, intent=None, on_mutation=lambda: None):
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
        on_mutation()
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
                                 code="preflight_failed", intent=intent, submission_outcome="not_started",
                                 submission_evidence={"stage": "lightning_setup", "reason": "The training launcher was never invoked.", "cleanup_status": intent.get("cleanup_status", "failed")},
                                 diagnostic=exc.diagnostic if isinstance(exc, ConnectorError) else None) from None
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
    compact = False
    progress_seen = False
    try:
        content = b"".join(volume.read_file("progress.json"))
        progress_seen = True
        if len(content) > 1024 * 1024:
            raise ValueError("Oversized Modal progress snapshot")
        progress = json.loads(content)
        count = progress.get("history_count")
        if progress.get("format") != "multigpu-progress-v1" or type(count) is not int or not 0 <= count <= 1_000_000:
            raise ValueError("Invalid Modal progress snapshot")
        cursor_path = Path(job_dir) / "modal-history-cursor.json"
        cursor = json.loads(cursor_path.read_text()) if cursor_path.exists() else {}
        first = cursor.get("history_count", 0) if cursor.get("volume") == handle["volume"] else 0
        if type(first) is not int or not 0 <= first <= count:
            first = 0
        for number in range(first, count):
            chunk = b"".join(volume.read_file(f"history/{number:08d}.jsonl"))
            if len(chunk) > 32 * 1024 * 1024:
                raise ValueError("Oversized Modal event history chunk")
            _ingest_logs(chunk.decode("utf-8", errors="replace"), job_dir)
            _json_write(cursor_path, {"volume": handle["volume"], "history_count": number + 1})
        compact = True
    except Exception as exc:
        if progress_seen and (isinstance(exc, FileNotFoundError) or type(exc).__name__ in {"NotFoundError", "NotFound"}):
            raise ConnectorError("Modal progress is not fully visible yet; the last local checkpoint was retained.", code="network", transient=True) from None
        if not isinstance(exc, FileNotFoundError) and type(exc).__name__ not in {"NotFoundError", "NotFound"}:
            raise _provider_error("Modal", exc, "progress snapshot") from None
    names = ("checkpoint.json", "connector-state.json") if compact else ("events.jsonl", "checkpoint.json", "connector-state.json")
    for name in names:
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


def _attach_refused(detail):
    return ConnectorError(detail + " No execution was attached. Inspect the saved attempt in the provider console.",
                          code="attach_unverified", submission_outcome="unknown")


def _matching_attempt(saved, expected, keys):
    return isinstance(saved, dict) and all(saved.get(key) == expected.get(key) for key in keys)


def attach(provider_id, config, execution_id, intent, job_dir):
    """Attach only an independently observed run belonging to this saved attempt.

    Provider operations here are lookups/reads only: no resource creation, start,
    upload, pause or cancellation. An ID entered by a user is never itself proof.
    """
    _check_config(config)
    if (provider_id not in {"kaggle", "modal", "lightning"} or not isinstance(intent, dict) or intent.get("provider") != provider_id
            or not isinstance(intent.get("identity"), dict) or not re.fullmatch(r"[a-f0-9]{64}", str(intent.get("spec_sha256", "")))):
        raise _attach_refused("This provider has no attachable saved submission identity.")
    try:
        record = _record_path(intent, job_dir)
        saved = json.loads(record.read_text(encoding="utf-8"))
        if not isinstance(saved, dict):
            raise ValueError("Invalid saved journal")
        saved = saved.get("handle") or saved
    except (OSError, ValueError, TypeError, ConnectorError):
        raise _attach_refused("A readable launch journal for this exact attempt is required.") from None
    bound_keys = ("provider", "attempt_id", "job_id", "spec_sha256", "identity")
    if not _matching_attempt(saved, intent, bound_keys) or not intent.get("identity"):
        raise _attach_refused("The supplied identity differs from this attempt's saved launch journal.")
    handle = dict(intent)  # Never accept arbitrary submitted handle fields.
    config = _bound_config(config, handle)
    requested = str(execution_id or "").strip()
    if not requested or len(requested) > 2048:
        raise _attach_refused("Enter this attempt's provider execution ID.")
    try:
        if provider_id == "kaggle":
            kernel = handle.get("kernel", "")
            expected = handle["identity"].get("username", "") + "/multigpu-" + handle["attempt_id"][:20]
            if kernel != expected or saved.get("kernel") != kernel:
                raise _attach_refused("The saved Kaggle notebook does not match this account and attempt.")
            if "://" in requested:
                parsed = urlsplit(requested)
                if parsed.scheme != "https" or parsed.hostname not in {"kaggle.com", "www.kaggle.com"} or parsed.username or parsed.password or parsed.port not in (None, 443):
                    raise _attach_refused("Use the saved notebook ID or its official Kaggle HTTPS URL.")
                match = re.fullmatch(r"/code/([A-Za-z0-9_-]+/[A-Za-z0-9_-]+)/?", parsed.path)
                requested = match.group(1) if match else ""
            if requested.casefold() != kernel.casefold():
                raise _attach_refused("A different Kaggle notebook cannot be attached to this training attempt.")
            _kaggle_identity({**config, "username": handle["identity"]["username"]})
        elif provider_id == "modal":
            if not re.fullmatch(r"fc-[A-Za-z0-9-]+", requested):
                raise _attach_refused("Enter the Modal function call ID from this attempt's control record.")
            _modal_identity(config, handle)
            if not handle.get("control") or not _matching_attempt(saved, handle, ("control", "volume", "environment", "app_name")):
                raise _attach_refused("The saved Modal control identity is incomplete or changed.")
            modal = _sdk("modal", "modal")
            control = modal.Dict.from_name(handle["control"], environment_name=handle.get("environment", "main"))
            recovered = control.get("handle", None)
            if (not _matching_attempt(recovered, handle, (*bound_keys, "control", "volume", "environment", "app_name"))
                    or recovered.get("call_id") != requested):
                raise _attach_refused("Modal's read-only control record does not bind this call to the saved attempt.")
            handle.update(call_id=requested, app_id=recovered.get("app_id"))
        else:
            remote = "multigpu-runs/" + handle["attempt_id"]
            if handle.get("remote_dir") != remote or saved.get("remote_dir") != remote:
                raise _attach_refused("Lightning's saved run directory does not match this attempt.")
            studio = _studio(config)
            identity = _lightning_identity(config, studio)
            if not _matching_attempt(identity, handle["identity"], ("owner", "owner_type", "teamspace", "studio")):
                raise _attach_refused("The selected Lightning Studio differs from this attempt's saved account and Studio.")
            if "running" not in str(studio.status).lower():
                raise _attach_refused("The dedicated Studio must already be running to read its persistent launch record; attachment never starts it.")
            code = f"from pathlib import Path; p=Path({remote!r})/'launch.json'; print(p.read_text() if p.exists() else '{{}}')"
            recovered = json.loads(studio.run("python3 -c " + shlex.quote(code)).strip().splitlines()[-1])
            pid = recovered.get("pid")
            if recovered.get("attempt_id") != handle["attempt_id"] or type(pid) is not int or pid <= 0 or requested not in {str(pid), remote}:
                raise _attach_refused("Lightning's persistent launch record does not bind this PID or run directory to the saved attempt.")
            handle.update(pid=pid, studio_stopped=False)
        state = poll(provider_id, config, handle, job_dir)
        if state.get("state") == "unknown":
            raise _attach_refused("The provider could not confirm this execution's current state.")
        handle.update(submission_status="confirmed", submission_evidence={"outcome": "confirmed", "stage": "read_only_attach", "attempt_id": handle["attempt_id"]})
        return {**state, "handle": handle, "submission_status": "confirmed", "connection_status": "connected"}
    except ConnectorError:
        raise
    except (ValueError, IndexError, KeyError, TypeError):
        raise _attach_refused("The provider returned no valid identity for this attempt.") from None
    except Exception as exc:
        raise _provider_error(provider_id, exc, "attachment lookup") from None


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
