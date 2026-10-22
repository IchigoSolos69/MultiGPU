# MultiGPU

A local training workspace for your own compute accounts. Browse all 13 requested providers, train locally, submit supported provider jobs, prepare portable notebooks, and verify model exports on your computer.

## Run

Python **3.11 or newer** is the only requirement for the dashboard and CPU starter. No Node installation, frontend build, hosted account, or cloud credential is required.

```powershell
python -m multigpu
```

Open **http://127.0.0.1:8765**. On Windows, double-click `start.cmd` or run `./start.ps1`. The launcher uses a local virtual environment, the Python launcher, or the bundled Codex Python runtime when available. You can set `MULTIGPU_PYTHON` to a full interpreter path. To install a console entry point:

```powershell
python -m pip install -e .
multigpu
```

Options: `--port 8765`, `--data-dir .multigpu`, `--no-browser`. State, jobs and default model exports live in `.multigpu/`. Set a different model folder in Settings. One controller may use a data directory at a time.

## First training run

1. Choose **New training → Starter classifier → Your computer**.
2. Keep free-only selected and preview the plan.
3. Start training. The runner learns real binary classification weights using SGD, records loss and saves resumable checkpoints.
4. Completed model files are verified with SHA-256 and copied to the selected export folder. Download a ZIP from the run page if desired.

The starter uses explicitly generated demonstration data unless you provide a CSV. A CSV contains numeric feature columns followed by a final binary `0`/`1` label; an optional header is accepted. Generated-data loss is not evidence of quality on your own task.

The **PyTorch classifier** uses CUDA when a compatible PyTorch build is available, otherwise CPU. Install the build for your hardware using [PyTorch's installation instructions](https://pytorch.org/get-started/locally/), then restart MultiGPU. PyTorch is optional and is not automatically downloaded. The included classification recipes require the model and dataset to fit the selected device; use your own streaming training script for larger datasets.

## Provider support

Provider facts are dated **21 October 2026**; quota and entitlements are checked separately. An implemented adapter is not a live account certification.

| Provider | Application support |
|---|---|
| Local CPU / CUDA | Real starter and optional PyTorch training; pause, resume, logs and verified export |
| Kaggle | Native CLI quota, bounded submission, logs/status and artifact download; immediate remote stop is unavailable |
| Modal | Optional SDK integration through detached Functions and persistent Volumes; paid execution only after explicit opt-in and reported native billing controls |
| Lightning | Optional SDK execution in an existing dedicated Studio; explicit startup/shutdown consent and reported provider limit required |
| Paperspace, Colab | Self-contained assisted notebook bundles and verified result import |
| Deepnote, Hex, Datalore, Fastero | Assisted CPU notebook workflows; eligible GPU resources remain provider/plan dependent |
| Databricks Free Edition | Assisted native notebook workflow, subject to account eligibility, networking and non-commercial restrictions |
| JupyterHub | Assisted notebook workflow on resources allocated by your host or institution |
| Colab Enterprise | Assisted cloud notebook workflow on the user's own billing/IAM account |
| Codespaces | Development information only; excluded from the generic training pool |

### Native authentication

Install optional tools into the **same Python environment** as the controller and log in directly with the provider:

```powershell
python -m pip install -e ".[kaggle]"
kaggle auth login
kaggle quota

python -m pip install -e ".[modal]"
modal setup

python -m pip install -e ".[lightning]"
```

Follow Lightning's native SDK authentication instructions for your account. The dashboard stores connection names and non-secret settings in SQLite. It rejects passwords and API tokens; native credential stores and environment variables remain under your control. SDK/CLI versions and provider permissions must be validated against your account.

Free-only automatic execution is enabled for local compute and eligible Kaggle quota. **Modal/Lightning credit-backed free-only automation is blocked** because this implementation cannot independently verify a zero out-of-pocket limit covering all charges. Use their guided notebook bundles, or explicitly enable paid execution, enter your provider-configured limit, and confirm entitlement and billing controls. A user-reported limit is not independent verification. Provider charges, electricity, storage and transfer remain yours.

Lightning automation requires a dedicated Studio with permission to stop it after result collection. If the controller goes offline, a Studio can remain running; native provider limits and the provider console remain essential. Modal Volumes are retained remotely until you remove them directly. The app never purchases plans, tops up credits, or deletes remote recovery artifacts.

## Assisted notebooks and result import

Preview a provider, select **Prepare notebook**, extract `training.ipynb`, and run it in your own account. The notebook embeds the worker, selected data, script and checkpoint. Review its contents before uploading private data. Set compute and provider billing controls yourself. PyTorch/custom dependencies must already be available or installed in that notebook.

Progress is visible in the provider notebook. The local dashboard cannot promise live telemetry for manually launched notebooks. Download the generated `output/` folder (or ZIP) and enter its local path on the run page. Import checks hashes, checkpoint identity and training target before accepting results. A paused imported run can explicitly resume on your computer; migration never lowers its target. Standalone custom scripts must bring their own required modules/data beyond the selected input files.

## Checkpoints, limits and parallel work

- Training summaries are emitted every **300 seconds**, separately from **60-second** checkpoint cadence. The dashboard refreshes every five seconds; remote updates depend on provider logs and durable output commits.
- Each worker invocation has a session limit and reserves time for a final checkpoint. On expiry, the coordinator can continue a committed checkpoint on another selected eligible provider, or pause. Manual pause never automatically starts another resource.
- Model, optimizer, RNG and progress state are preserved by the included recipes. Compatibility fingerprints reject a changed dataset, script or training configuration.
- Incomplete or corrupted checkpoints are rejected. The previous good checkpoint and prior exports are retained. Export failure preserves the completed training state and can be retried after fixing storage.
- Multiple independent jobs can run simultaneously. A single cloud account is not double-reserved by the scheduler. Selecting several providers supplies execution/failover options; it does not automatically combine their VRAM or invent experiments.
- `python -m multigpu.benchmark --output .multigpu/benchmark.json` measures a **same-host two-CUDA-GPU DDP prototype** when available. It reports unavailable rather than inventing performance. This synthetic benchmark does not qualify arbitrary recipes or cross-provider networking for production execution. Cross-provider same-model training remains unqualified and is not automatically enabled.

## Custom Python / PyTorch

Select a trusted `.py` file implementing `train(ctx)` and confirm that it may run with your user permissions. [examples/custom_train.py](examples/custom_train.py) is a runnable dependency-free example.

```python
def train(ctx):
    state = ctx.resume_state or {"weight": 0.0}
    for step in range(ctx.step + 1, ctx.spec["steps"] + 1):
        if ctx.should_stop():
            break
        # Update model and optimizer, then report the matching state.
        loss = update(state)
        ctx.report(loss, step)
        ctx.checkpoint(state)
    ctx.checkpoint(state)
    if ctx.step == ctx.spec["steps"]:
        ctx.export({"model.json": serialize_model(state)})
```

Custom scripts must save full resumable state for the **current reported step**, check `ctx.should_stop()`, and export usable model files on completion. They are trusted programs, not sandboxed plugins. No generic wrapper can guarantee stopping or resuming arbitrary unmodified training code.

## Validation

```powershell
python -m unittest discover -s tests -v
node --check multigpu/static/app.js
```

Tests cover real learning and exact resume, deadline/pause behavior, hashes and traversal, disk exhaustion, guided notebook execution/import, missing SDKs, quota and spending gates, provider mocks, local HTTP authentication/origin checks, and export persistence. GPU and live account integration tests require your own hardware and credentials; no billed jobs are launched by the test suite.

The backend binds exclusively to loopback. Mutations require a session token and same-origin requests; provider secrets are not accepted by its API. Keep this trusted local application off public interfaces.
