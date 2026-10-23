"""Fixed in-app setup guidance; acknowledgement is never account verification."""
from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import sys

from .catalog import get_provider


IMPORT_HELP = {
    "colab": ("https://support.google.com/qwiklabs/answer/16671106", "Open Colab, choose Upload in the open-notebook dialog, and select training.ipynb.", "Choose Runtime > Change runtime type. Use CPU for the starter, or a GPU you are entitled to use for a GPU recipe."),
    "kaggle": ("https://www.kaggle.com/docs/notebooks", "Create a notebook, then use File > Import Notebook to select training.ipynb.", "Use Settings > Session options to choose the accelerator. Inspect current quota and keep the notebook private when it contains private data."),
    "paperspace": ("https://docs.digitalocean.com/products/paperspace/notebooks/how-to/create-notebooks/", "Create a Python notebook environment, open JupyterLab, and upload training.ipynb in its file browser.", "Choose an available machine in the Paperspace notebook settings. Check the displayed price; available free capacity is not guaranteed."),
    "lightning": ("https://lightning.ai/docs/platform/overview/getting-started/", "Open your own Studio in JupyterLab and upload training.ipynb using its file browser.", "Inspect the Studio machine and billing before selecting a GPU. Stop the Studio yourself when manual training is finished."),
    "modal": ("https://modal.com/docs/guide/notebooks", "Open an authorized Modal notebook and upload training.ipynb through its notebook file interface.", "Choose an authorized CPU or GPU notebook configuration and inspect compute, storage and transfer charges in Modal."),
    "deepnote": ("https://deepnote.com/docs/importing-and-exporting-jupyter-notebooks", "Drag training.ipynb into the project's NOTEBOOKS section. A notebook uploaded under FILES must be moved to Notebooks before it can run.", "Select suitable project hardware in your workspace. The free CPU machine does not imply free GPU access."),
    "hex": ("https://learn.hex.tech/docs/explore-data/projects/import-export", "Choose Import on the Hex home page and upload training.ipynb as a project. An Admin or Editor role is required.", "Have a workspace administrator enable an authorized compute profile. Advanced GPU compute requires an eligible paid workspace; free trials do not unlock it."),
    "databricks": ("https://docs.databricks.com/aws/en/notebooks/notebook-export-import", "In Workspace, open the target folder's menu, choose Import, and select training.ipynb.", "Attach a Python runtime that permits this recipe and local file operations. Eligible verified Free Edition accounts may have limited AI Runtime; availability is account-specific."),
    "datalore": ("https://www.jetbrains.com/help/datalore/datalore-notebooks.html", "On the Datalore home page, select your workspace, choose Import > Import notebook, and select training.ipynb.", "Choose a Python kernel in Jupyter computation mode and a permitted machine. GPU compute depends on your plan or purchased credits."),
    "jupyterhub": ("https://jupyterlab.readthedocs.io/en/stable/user/files.html", "Sign in to your institution's Hub, start your allocated server, and upload training.ipynb using the JupyterLab file browser.", "Ask the Hub administrator for a Python environment and GPU allocation suitable for the recipe. JupyterHub itself supplies no hardware or free quota."),
    "colab-enterprise": ("https://docs.cloud.google.com/colab/docs/create-console-quickstart", "In Google Cloud Colab Enterprise > My notebooks, select your region, choose Import, and select training.ipynb from Your computer.", "Use a project with billing, IAM permission and sufficient runtime/GPU quota. Confirm the runtime's filesystem and package-install permissions before uploading data."),
}


def _step(id, title, description, *, automatic=False, ready=False, instructions=(), commands=(), links=(), actions=(), blocking_reason=None):
    result = {"id": id, "title": title, "description": description,
              "instructions": list(instructions), "commands": list(commands), "links": list(links),
              "completion": "automatic" if automatic else "acknowledgement",
              "ready": bool(ready), "can_complete": bool(ready) if automatic else True,
              "actions": list(actions)}
    if blocking_reason and not result["can_complete"]:
        result["blocking_reason"] = blocking_reason
    return result


def _link(label, url):
    return {"label": label, "url": url}


def _external(label, url):
    return {"type": "external", "label": label, "url": url}


def _cli_command(provider_id, hardware):
    python = Path(hardware.get("python") or sys.executable)
    if provider_id == "modal":
        argv = [str(python), "-m", "modal", "setup"]
    else:
        scripts = python.parent if python.parent.name.lower() in ("scripts", "bin") else python.parent / ("Scripts" if os.name == "nt" else "bin")
        argv = [str(scripts / (provider_id if provider_id == "kaggle" else "lightning")) + (".exe" if os.name == "nt" else ""), *(["auth", "login"] if provider_id == "kaggle" else ["login"])]
    if os.name == "nt":
        return "& " + " ".join("'" + value.replace("'", "''") + "'" for value in argv)
    import shlex
    return shlex.join(argv)


def provider_setup(provider_id, connection=None, hardware=None):
    provider = get_provider(provider_id)
    connection, hardware = connection or {}, hardware or {}
    steps = []
    if provider_id == "local":
        steps.append(_step("tools", "Choose a local training environment", "The tabular starter already runs on CPU without another account.",
                           automatic=True, ready=True,
                           instructions=["Prepare the image or PyTorch environment only for recipes that need it.", "CUDA requires a compatible NVIDIA GPU and driver; setup does not prove a model will fit."],
                           actions=[{"type": "environment", "label": "Prepare PyTorch CPU", "profile": "cpu"},
                                    {"type": "environment", "label": "Prepare image training", "profile": "image"},
                                    {"type": "environment", "label": "Prepare CUDA", "profile": "cuda"}]))
    else:
        url = provider["signup_url"]
        steps.append(_step("account", "Open your provider account", "Sign up or sign in directly with " + provider["name"] + ".",
                           instructions=["Keep credentials and payment details with the provider; this app does not accept them."],
                           actions=[_external("Open " + provider["name"], url)], links=[_link("Official documentation", provider["docs_url"])]))
        if provider_id in ("kaggle", "modal", "lightning"):
            package = {"kaggle": "kaggle", "modal": "modal", "lightning": "lightning_sdk"}[provider_id]
            try:
                installed = importlib.util.find_spec(package) is not None
            except (ImportError, ValueError):
                installed = False
            installed = bool(hardware.get(provider_id + "_available", installed) or connection.get("account_verified"))
            steps.append(_step("tools", "Prepare the native provider tools", "Install the provider client into the app's own Python environment.",
                               automatic=True, ready=installed, instructions=["Use the preparation button; installing into another Python environment will not prepare this app."],
                               actions=[{"type": "environment", "label": "Prepare " + provider["name"], "profile": provider_id}],
                               blocking_reason="Prepare the provider tools before continuing."))
            auth_url = {"kaggle": "https://github.com/Kaggle/kaggle-cli/blob/main/skills/references/auth.md",
                        "modal": "https://modal.com/docs/guide/getting-started",
                        "lightning": "https://lightning.ai/docs/platform/developers/cli/"}[provider_id]
            steps.append(_step("signin", "Sign in with the native client", "Run this command in Windows PowerShell on this computer, then finish the provider's browser sign-in.",
                               commands=[_cli_command(provider_id, hardware)],
                               actions=[{"type": "provider-signin", "label": "Sign in with " + provider["name"], "provider_id": provider_id}],
                               instructions=["The command targets the app's displayed Python environment. Do not paste tokens into MultiGPU."],
                               links=[_link("Official sign-in instructions", auth_url)]))
            steps.append(_step("verify", "Save settings and check the account", "Enter the nonsecret connection settings below and check this provider.",
                               automatic=True, ready=bool(connection.get("account_verified")),
                               instructions=provider["setup_steps"][2:], actions=[{"type": "probe", "label": "Save & check"}],
                               blocking_reason=connection.get("detail") or "The provider has not confirmed a ready connection."))
        elif provider["integration"] != "development":
            steps.append(_step("runtime", "Check your available runtime", "Confirm suitable compute in the provider's own workspace.",
                               instructions=provider["setup_steps"], links=[_link("Compute and account requirements", provider["docs_url"])]))
            steps.append(_step("verify", "Confirm manual notebook access", "Confirm that you can open a Python notebook on compute you are allowed to use.",
                               instructions=["This is a manual confirmation. It does not verify quota, GPU entitlement or billing, and does not enable automatic execution."],
                               actions=[_external("Open provider", url)]))
    steps.append(_step("review", "Review the limits before training", provider["free_summary"],
                       instructions=provider["restrictions"], links=[_link("Provider documentation", provider["docs_url"])]))
    return {"provider_id": provider_id, "title": "Set up " + provider["name"],
            "description": "Follow these steps here in the app. Account access and paid plans remain with your provider.", "steps": steps}


def job_setup(job, root=None):
    from .artifacts import IntegrityError, checkpoint_assets, load_checkpoint, validate_checkpoint_payload
    from .connectors import ConnectorError, needs_payload
    provider = get_provider(job["provider_id"])
    jid, spec = job["id"], dict(job["spec"])
    folder = Path(root) if root is not None else None
    checkpoint = folder / "checkpoint.json" if folder else None
    digest, imported, problem = "initial", False, None
    if checkpoint and checkpoint.is_file():
        digest = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
        try:
            state = load_checkpoint(checkpoint)
            validate_checkpoint_payload(state, spec)
            from .datasets import fingerprint_for_spec
            expected = job.get("fingerprint") or spec.get("fingerprint") or fingerprint_for_spec(spec)
            if state.get("job_id") != jid or state.get("fingerprint") != expected:
                raise IntegrityError("Checkpoint identity differs from this run")
            companions = checkpoint_assets(state, folder)
            spec["resume_from"] = str(checkpoint)
            if companions:
                spec["weights_path"] = str(companions["assets/base_weights.pth"])
                spec["weights_sha256"] = state["payload"]["weights_sha256"]
            imported = bool(job.get("checkpoint"))
        except (OSError, ValueError, TypeError) as exc:
            problem = str(exc)
    try:
        separate = needs_payload(spec, folder)
    except (ConnectorError, OSError) as exc:
        separate, problem = False, str(exc)
    supported = provider["integration"] != "development" and provider["id"] != "local"
    downloadable = supported and job.get("step", 0) < spec["steps"] and problem is None
    actions = [{"type": "notebook", "label": "Download training notebook", "path": f"/api/jobs/{jid}/notebook", "filename": "training.ipynb"}]
    if separate:
        actions.append({"type": "payload", "label": "Download training data and code", "path": f"/api/jobs/{jid}/notebook-payload", "filename": "training-payload.zip"})
    step = _step("download", "Get the training notebook", "Download the file you will run; these setup instructions stay in the app.",
                 instructions=["Only upload these files to an authorized provider. They contain your selected data, code and recovery state."] +
                              (["This job has large inputs. Download both files; keep the payload named training-payload.zip."] if separate else ["This small job needs one file; its inputs are embedded in training.ipynb."]),
                 actions=actions if downloadable else [])
    step["can_complete"] = downloadable
    if not downloadable:
        step["blocking_reason"] = problem or ("This resource is not a notebook training backend." if not supported else "This run already reached its target.")
    help_url, upload, runtime = IMPORT_HELP.get(provider["id"], (provider["docs_url"], "Open an authorized Python notebook environment.", "Confirm your account's permitted runtime."))
    upload_instructions = [upload]
    if separate:
        upload_instructions += ["Upload training-payload.zip into the notebook's working folder before running it. If the provider extracts ZIPs, keep the extracted manifest and all files together."]
        if provider["id"] == "kaggle":
            upload_instructions += ["Use Add Input to attach the payload as a private dataset; the notebook also recognizes its extracted files under /kaggle/input."]
    steps = [step,
             _step("upload", "Open the notebook at the provider", "Use " + provider["name"] + " to import the training file.", instructions=upload_instructions,
                   links=[_link("Official import instructions", help_url)], actions=[_external("Open " + provider["name"], provider["signup_url"])]),
             _step("runtime", "Select compute and dependencies", "Choose the runtime before running training.", instructions=[runtime,
                   "Use Python 3.11 or later. The starter needs no extra packages; PyTorch recipes need torch, and image training also needs torchvision and Pillow.",
                   "If the workspace blocks file uploads, local writes or required packages, stop here and choose another authorized runtime."],
                   links=[_link("Runtime requirements", provider["docs_url"])]),
             _step("run", "Run training and watch provider progress", "Run the first code cell in training.ipynb.", instructions=[
                   "Read the actual loss, step and checkpoint messages in the notebook. Manual notebook sessions do not stream live progress back to this app.",
                   "If the provider ends the session, preserve its saved output. A later notebook can continue the same target after a verified import."]),
             _step("save-results", "Save the results on your computer", "After training completes or pauses, run the notebook's Save results cell.", instructions=[
                   "Download multigpu-results.zip using the provider's file browser. Download the result archive, not an export of the notebook.",
                   "Keep the entire checkpoint and image weights together.",
                   "Stop any billed runtime using the provider's own controls after saving the files."]),
             _step("import", "Verify and import results", "Select the downloaded result ZIP or its extracted folder, preview it, then import it.",
                   automatic=True, ready=bool(imported or (job.get("export_status") == "verified" and job.get("export_path"))),
                   instructions=["An older checkpoint requires an explicit rollback choice. Incomplete or altered results are rejected before replacing recovery data."],
                   actions=[{"type": "import", "label": "Choose downloaded results"}], blocking_reason="No verified results from this run have been imported yet.")]
    prepared = job.get("notebook_preparation") or {}
    if prepared:
        current_import = (job.get("last_import_verified") is True and isinstance(job.get("last_import_at"), str)
                          and job["last_import_at"] >= prepared.get("prepared_at", ""))
        steps[-1]["ready"] = steps[-1]["ready"] and current_import
        steps[-1]["can_complete"] = steps[-1]["ready"]
    if not steps[-1]["ready"]:
        steps[-1]["blocking_reason"] = "Verify and import the results from this prepared notebook session before continuing."
    else:
        steps[-1].pop("blocking_reason", None)
    if provider["id"] == "hex":
        steps[-2]["instructions"].append("Download before restarting the Hex kernel; files created in subdirectories are not persisted after restart.")
    if provider["id"] == "colab-enterprise":
        steps[1]["instructions"].append("Colab Enterprise requires notebook imports below 20 MB; large training inputs are delivered separately.")
    elif provider["id"] == "databricks":
        steps[1]["instructions"].append("Databricks notebook import has a documented 10 MB content limit. The app keeps the notebook small and sends large inputs separately.")
    revision = hashlib.sha256((jid + ":" + digest + ":" + str(separate)).encode()).hexdigest()
    return {"provider_id": provider["id"], "job_id": jid, "title": "Run your notebook in " + provider["name"],
            "description": "Prepare, run and recover this session without downloading a setup document.",
            "revision": revision, "payload_required": separate, "steps": steps}
