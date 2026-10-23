# MultiGPU

A local training workspace that uses your own compute accounts and saves verified models on your computer. The Windows-first journey supports **tabular binary classification**, **image classification**, and trusted custom Python training.

## Start

On Windows, double-click `start.cmd` for the tray application, or run `./start.ps1` for a terminal session. The launcher prepares an application-owned environment; it does not install packages into system Python. Closing the browser leaves the tray controller running.

For development, Python 3.11+ runs the dependency-free dashboard and CPU starter:

```powershell
python -m multigpu --no-browser
```

The command-line default is http://127.0.0.1:8765 and `.multigpu/` for state. The packaged tray chooses an available loopback port, uses `%LOCALAPPDATA%/MultiGPU/workspace`, and lets you choose an existing workspace for the next start. One controller may own a workspace at a time. Existing data receives a SQLite backup before its version-two metadata migration.

Build and test the Windows installer using [packaging instructions](packaging/README.md). Optional training packages are not bundled into the base installer. Installer signing, a clean Windows VM, live provider accounts and actual CUDA hardware require separate release qualification.

## From data to a usable model

1. **Task & data:** try the generated tabular sample, choose a CSV, or choose an image class-folder directory. Inspect it before sending anything to a provider.
2. **Training:** name the run and review recipe defaults. Advanced settings include target, batch size, learning rate, seed and device. Dependency setup reports its progress in the app.
3. **Compute & limits:** choose a primary resource and order explicit fallbacks. Free-only is selected by default. Paid usage requires a new per-run choice.
4. **Review:** preview the exact data, resource order and training target. A launch requires the matching preview digest; changed inputs require another preview.
5. **Follow and recover:** monitor actual progress, independent heartbeats, checkpoints and required actions. Interrupted cloud monitoring reconnects to saved provider identities before another attempt can start.
6. **Evaluate and export:** inspect held-out metrics, verify the local model, try a prediction and download a portable inference example.

Drafts survive navigation and restart. Paid and trusted-script execution confirmations are not restored. Repeating a run creates an editable draft and never launches compute by itself.

### Tabular data

Use numeric feature columns and a binary `0`/`1` label. An optional header and selectable label column are supported. New recipes require at least five rows in each class and use a seeded stratified 80/20 split. Feature standardization is fitted only on training rows and saved with the model. The dependency-free classifier needs no extra libraries; the small PyTorch classifier supports CPU or qualified CUDA.

```csv
height,weight,label
165,62,0
151,45,1
178,78,0
```

This excerpt illustrates the format; include at least five examples of each label. Missing, nonnumeric and nonfinite values are rejected with an actionable error. Generated sample data is explicitly a demonstration, not evidence of performance on your task.

### Image data

Choose a folder with at least two class subfolders and five readable images per class:

```text
dataset/
  cats/photo-1.jpg
  cats/photo-2.jpg
  ...
  dogs/photo-1.jpg
  dogs/photo-2.jpg
  ...
```

The built-in recipe freezes an official pretrained ResNet-18 backbone and trains its classification head. Defaults are five epochs, batch size 16, learning rate .001 and SGD momentum .9. Fixed RGB preprocessing, class order, seeded validation split, data cursor and RNG state travel with the model/checkpoint. Official base weights are downloaded and SHA-256 checked during explicit environment preparation, before paid compute is allocated.

Prepare the image environment in the dashboard. CPU libraries are the default; CUDA preparation is a separate choice. All training data must fit the recipe's documented host-memory and staging limits; GPU batches are transferred individually. Large or specialized models use custom scripts.

## Providers and spending

All 13 requested providers plus the local computer remain in the directory. Provider facts, authenticated identity, observed quota, actual hardware and application support are distinct. Documentation references are dated; missing observations remain unknown.

| Resource | Implemented mode |
|---|---|
| Local CPU / CUDA | Built-in and trusted custom recipes, checkpoint/resume and verified exports; CUDA qualification depends on your installation |
| Kaggle | Native CLI quota, bounded submission, logs/status and artifact retrieval; stopping immediately requires Kaggle's own control |
| Modal | Native Functions/Volumes adapter with durable identity and cooperative pause; paid opt-in required while zero-spend controls remain unverified |
| Lightning | Existing dedicated Studio adapter with machine/account checks, recovery identity and explicit start/stop authorization |
| Paperspace, Colab | Assisted notebooks and verified result import |
| Deepnote, Hex, Datalore, Fastero | Capability-dependent notebook/development information; free CPU and paid GPU access remain separate |
| Databricks Free Edition | Conditional AI Runtime eligibility; assisted execution subject to workspace restrictions and non-commercial use |
| JupyterHub | Assisted notebook execution on hardware allocated by your institution or host |
| Colab Enterprise | Assisted notebook execution on the user's billing/IAM project |
| Codespaces | Development information; excluded from the general training pool |

An implemented adapter is **not** live account certification. Provider credentials stay in their native authentication tools. The dashboard rejects passwords/tokens and stores only nonsecret connection settings.

Free-only automatic execution is admitted for local compute and eligible Kaggle quota. Modal/Lightning credit-backed free-only automation remains blocked because the application cannot independently verify zero out-of-pocket enforcement covering compute, storage and transfer. A reported native spending limit is a user declaration. The application does not purchase plans, add credits or activate paid fallbacks.

Native authentication and dependency installation must target the application's displayed Python environment. Use **Save & check** to inspect identity, actual compute and billing evidence. Read provider terms and configure any provider-side billing controls directly with that provider.

## Interruption, notebooks and recovery

- Each provider attempt has a persisted identity before submission. Unconfirmed submissions block duplicate retries; use reconciliation and the provider console to resolve uncertainty.
- Lost connectivity retains the last known execution state and account reservation while monitoring retries. Restart reconnects existing executions rather than silently launching replacements.
- Pause is capability-specific. Cooperative pause waits for a durable checkpoint; cancellation may lose uncommitted progress; Kaggle requires its native Stop control. A disconnected controller never proves remote compute stopped.
- Heartbeats are emitted every 30 seconds, summaries every 300 seconds and checkpoints every 60 seconds. Controller fetch time, worker reachability and actual metric age are displayed separately.
- Assisted notebooks do not provide guaranteed local live telemetry. Check progress in the provider notebook, download its output folder/ZIP, then preview and import it locally.
- Paused guided runs keep notebook preparation and import controls. A continuation bundle contains verified recovery state and preserves the original target.
- Imports verify hashes, job identity, recipe/configuration and progress. Older state requires explicit rollback. Recovery can restore the previous verified checkpoint.
- A finished run can choose another export destination after a storage failure. Downloaded ZIPs contain only manifest-listed verified artifacts. Remote recovery files remain until you clean them up directly.

Selecting multiple resources supplies ordered execution/fallback options. It does not combine unrelated free GPUs into one distributed cluster, pool their VRAM, or create experiments automatically. The optional same-host benchmark is a qualification prototype, not a claim of cross-provider speedup.

## Custom Python

[examples/custom_train.py](examples/custom_train.py) supplies a working starter. A trusted script defines `train(ctx)`, restores `ctx.resume_state`, calls `ctx.report(loss, step)`, polls `ctx.should_stop()`, checkpoints complete model/optimizer/RNG state for the reported step, and exports named usable model files through `ctx.export(files)`.

Custom scripts run with your user permissions and define their own dependencies and evaluation. Cooperative stopping and complete resumable state are part of their contract. Existing version-one jobs retain their original sampling and fingerprints; new recipe behavior is explicitly versioned.

## Verification

```powershell
python -m unittest discover -s tests -v
node --check multigpu/static/app.js
```

Use an isolated controller for browser acceptance; the test refuses the normal user port:

```powershell
python -m multigpu --port 8768 --data-dir .test-runs/browser-state --no-browser
$env:MULTIGPU_TEST_URL = 'http://127.0.0.1:8768'
node tests/browser-smoke.cjs
```

Playwright is a development-only dependency. Tests use mocked providers and must not launch cloud jobs. Optional CPU recipe tests use PyTorch/torchvision/Pillow and a separately prepared official model asset. Test artifacts remain under `.test-runs/`.

The HTTP controller binds only to loopback. Mutations require a session token and origin checks. Keep the trusted local controller off public interfaces. See [the implementation plan](IMPLEMENTATION_PLAN.md) for delivery requirements and qualification gates.
