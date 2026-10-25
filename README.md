# MultiGPU

MultiGPU is a local application for training models with **your own computer and your own provider accounts**, then saving verified models on your computer. It provides a Windows desktop window, in-app provider setup, a training wizard, progress monitoring, checkpoint recovery and model export.

**Application version: 0.5.0. Official-source provider research checked: 24 October 2026.**

The goal is to make eligible free compute easier to use without changing the user's training target or silently enabling paid resources. The current implementation trains each attempt on **one resource at a time**. Ordered fallbacks and compatible checkpoints support continuation; synchronous cross-provider training and pooled GPU memory are future work.

> Free CPU, free GPU, introductory credits and a paid subscription's included credits are different offers. Published offers do not establish an account's actual entitlement, available capacity or zero-bill protection. Read the [provider audit](docs/PROVIDER_RESEARCH.md) before choosing resources.

## Contents

- [Capabilities](#capabilities)
- [Project at a glance](#project-at-a-glance)
- [Free and paid providers](#free-and-paid-providers)
- [Provider research library](#provider-research-library)
- [Installation](#installation)
- [Training workflows](#training-workflows)
- [Recipes and data](#recipes-and-data)
- [Dependencies and provider setup](#dependencies-and-provider-setup)
- [Spending and fallback rules](#spending-and-fallback-rules)
- [Progress and recovery](#progress-and-recovery)
- [Training efficiency and performance](#training-efficiency-and-performance)
- [Model export and prediction](#model-export-and-prediction)
- [Custom training scripts](#custom-training-scripts)
- [Architecture and storage](#architecture-and-storage)
- [Workspace maintenance and privacy](#workspace-maintenance-and-privacy)
- [Local API](#local-api)
- [Security and trust](#security-and-trust)
- [Development and testing](#development-and-testing)
- [Windows packaging](#windows-packaging)
- [Troubleshooting](#troubleshooting)
- [Validation and next work](#validation-and-next-work)
- [Contributing and licensing](#contributing-and-licensing)

## Capabilities

Implemented:

- Local CPU and compatible NVIDIA CUDA training with explicit device/GPU selection.
- Dependency-free tabular classification, a small PyTorch network, image transfer learning and trusted custom Python scripts.
- Automatic connector code for Kaggle, Modal Functions/Volumes and dedicated Lightning Studios.
- Assisted notebooks, optional input payloads, in-app instructions and verified result import for eligible services.
- Persisted drafts, explicit resource order and preview digests binding launch to reviewed inputs/resources.
- Metrics, worker heartbeats, five-minute summaries and redacted diagnostic logs.
- Atomic checkpoints, compatible continuation and recovery after interrupted model delivery.
- Verified exports, artifact downloads, held-out evaluation and built-in prediction examples.
- Windows tray, native file dialogs, external sign-in, notifications and browser/controller recovery.

The directory contains resources with different integration levels. It does not certify every account, GPU, plan or workload. A general model marketplace, arbitrary-model graphical trainer, hosted multi-user service, automatic tuning and a qualified cross-provider distributed cluster are not implemented.

## Project at a glance

MultiGPU is intended for individual learners and model trainers who want to run a small supported recipe or integrate their own training loop, compare compute options, and keep model artifacts locally. Provider accounts, verification, subscriptions and payment relationships belong to the user. MultiGPU supplies the local workflow and the implemented adapters.

| Area | Current scope |
|---|---|
| Release | 0.5.0; performance work and validation recorded on 25 October 2026 |
| Packaged platform | Windows 10/11 x64, per-user desktop installation |
| Core runtime | Python 3.11+; bundled Windows Python 3.12.15 |
| Interface | Local browser dashboard or native Windows WebView2 window |
| Original resource catalog | Your computer plus 13 provider/institutional entries; execution modes vary |
| Additional discovery | 142 research records and 13 bundled reports, checked 24 October 2026 |
| Built-in model tasks | Numeric binary classification and image classification |
| Extensibility | Trusted Python `train(ctx)` scripts with explicit progress, checkpoint and export hooks |
| Execution topology | One resource per attempt, compatible continuation and ordered fallbacks |
| Persistence | Local SQLite workspace, checkpoints and hash-verified model exports |
| Cloud ownership | User-created accounts; user-managed plans, credentials and provider billing controls |

The name describes the goal of using multiple available GPU resources. Selecting several providers currently creates an ordered execution plan; it does not combine their VRAM or launch data-parallel replicas. A workload still has to fit the hardware and software available to its selected attempt.

## Free and paid providers

The table is the **research classification**, not a live entitlement check. A provider can belong to several cost groups. Detailed conditions, official citations and conflicting evidence are in [docs/PROVIDER_RESEARCH.md](docs/PROVIDER_RESEARCH.md).

The [additional compute directory](docs/ADDITIONAL_PROVIDERS.md) expands the research to GPU rental clouds, regional services, training APIs, free/trial offers and research allocations, with a [filterable CSV](docs/research/additional-provider-directory-2026-10-24.csv). Version 0.4.4 includes these **142 research entries** in the app's provider directory, with cost/access filters, source links and in-app research reports. They remain **research candidates, not supported training connectors**; related products, aggregators and allocation routes are identified separately.

| Resource | Training cost category | Current app integration |
|---|---|---|
| Kaggle Notebooks | Published free CPU/GPU allowance; quota/capacity apply | Automatic CLI quota/submission/status/logs/output; immediate stop in provider console |
| Google Colab | Dynamic free CPU/GPU; separate paid products | Assisted notebook/import |
| Databricks Free Edition | Free CPU; conditional non-commercial GPU | Assisted; account/runtime restrictions apply |
| Modal | Recurring compute credits, then metered usage; GPU payment condition | Automatic Functions/Volumes; hosted notebooks retired |
| Lightning AI Studios | Free CPU; eligible starting GPU credits; paid GPU afterward | Dedicated Studio adapter with explicit shutdown authorization |
| Deepnote | Free CPU / paid GPU | Assisted notebook; API automation not implemented |
| Hex | Free CPU / paid GPU | Assisted notebook; API automation not implemented |
| JetBrains Datalore | Free CPU / paid GPU | Assisted; free-CPU API candidate, not implemented |
| Fastero | Free notebook compute; GPU/hardware unverified | Development information; outside training pool |
| GitHub Codespaces | Included CPU development allowance / paid overage; no general GPU | Repository-development helper, outside general training pool |
| Paperspace Notebooks | Paid confirmed; new-account free availability unresolved | Assisted; never assume an available free machine |
| Colab Enterprise | Metered cloud compute; eligible credits may offset charges | Assisted, user-owned billing/IAM project |
| JupyterHub | Institution/operator-provided hardware | Assisted on an authorized Hub |
| Your computer | User-owned CPU/CUDA resources and operating costs | Automatic local execution |

**Free GPU starting points:** Kaggle and consumer Colab, within their restrictions. Databricks GPU is conditional. Modal belongs in recurring credits; Lightning's GPU grant is introductory. Deepnote, Hex and Datalore should not appear under a filter meaning free GPU training.

**Paperspace:** official pages still contain free-machine claims, but current new-account access was not established. Treat it as paid/free-unverified rather than promise a free M4000. The [evidence trail](docs/research/providers-gpu-2026-10-24.md#paperspace-evidence-and-unresolved-availability) records the conflict.

**Research versus training support:** the Windows build bundles the directory data, comparison CSV and all 13 detailed research documents for in-app reading without a network connection. Official external pages still need internet access. A researched offering does not become a selectable training resource until its connector is implemented and qualified; the existing local, automatic and assisted workflows retain their own readiness and allowance checks.

## Provider research library

### What the directory counts

The expanded directory has **130 compute offerings/access platforms, 10 allocation routes, one legacy service and one paused program**. Together these are 142 research records, not 142 independent GPU vendors. The original 14 resource records are maintained separately. A vendor can expose several products, a marketplace can broker someone else's hardware, and an allocation program can grant access to an existing cloud or institution.

The research covers GPU clouds and marketplaces, general clouds, managed training and fine-tuning APIs, notebook services, regional providers, scientific infrastructure and approved research grants. It records training types and accelerators as published; NVIDIA, AMD ROCm, TPU and Gaudi offerings do not imply interchangeable dependencies or checkpoint compatibility in MultiGPU.

### Browse inside the application

1. Open the provider directory and select **Researched** to see additional discovery entries.
2. Search a provider name, hardware family or eligibility condition. Results are paginated in groups of 25.
3. Use **Cost & access** to narrow free GPU allowances, credits/trials, paid GPU, free CPU or institutional/research access.
4. Open **Research** for training mode, accelerator, access limits, parent/relationship information and official source links.
5. Open the linked reports or **Research library** to read the full bundled evidence. **Export directory CSV** downloads the comparison data.

Research entries show **Integration not implemented**. They offer no account probe, setup or training action and are excluded from training resource selection. Opening the directory does not create accounts or provision compute. Bundled report content is available offline; following an external official link requires a network connection. The reader supports tables and internal report links, escapes raw HTML and restricts external links to HTTPS.

### Cost and access categories

| Category | Meaning and checks still needed |
|---|---|
| Free GPU allowance | A published GPU allocation; inspect remaining quota, eligibility, availability and runtime restrictions |
| Conditional GPU access | An offer requiring verification, geography, approval, a particular account or workload scope |
| Introductory credit | A finite signup/trial balance; check expiry, eligible products, payment requirements and overage |
| Recurring credit | Periodically renewed credits; inspect covered compute, storage, egress and any spending limits |
| Paid GPU | Metered rental, subscription or contracted hardware; a trial may also exist |
| Free CPU | CPU notebook or development capacity; does not establish free GPU entitlement |
| Institutional/allocation access | Operator-owned resources, an approved grant or research allocation with its own usage conditions |
| Platform credit | A platform fee discount that may leave underlying GPU/cloud infrastructure fully billable |
| Legacy or paused | Historical or currently unavailable access; retained for context, not offered as a new training route |

Categories can overlap. The UI groups related tags for filtering; the individual record and report explain the actual conditions. Generic cloud trial credit alone does not establish GPU eligibility. Allocations may restrict commercial use or redistribution, and a free notebook may prohibit use as an external distributed worker.

### Detailed report index

All 13 reports below ship with the Windows build. The canonical comparison CSV and machine-readable discovery records accompany them.

| Report | Coverage |
|---|---|
| [Additional compute directory](docs/ADDITIONAL_PROVIDERS.md) | Full expansion, categories, useful free/credit leads and relationship notes |
| [Original provider audit](docs/PROVIDER_RESEARCH.md) | All 13 original providers, free/paid separation, citations and integration implications |
| [Kaggle, Colab and Paperspace](docs/research/providers-gpu-2026-10-24.md) | Notebook GPU allowances, restrictions and conflicting Paperspace evidence |
| [Modal, Lightning and local hardware](docs/research/providers-credits-2026-10-24.md) | Credit conditions, paid usage and notebook retirement |
| [Deepnote, Hex, Datalore and Fastero](docs/research/providers-notebooks-2026-10-24.md) | CPU notebooks, paid GPU and API eligibility |
| [Cloud, institutional and development resources](docs/research/providers-cloud-2026-10-24.md) | Databricks, Colab Enterprise, JupyterHub and Codespaces |
| [GPU clouds and marketplaces](docs/research/additional-gpu-clouds-2026-10-24.md) | Rental clouds, enterprise clusters and access platforms |
| [General clouds and research allocations](docs/research/additional-clouds-grants-2026-10-24.md) | Hyperscalers, academic grants and scientific allocation routes |
| [Free, promotional and credit-backed compute](docs/research/additional-free-compute-2026-10-24.md) | Free offers, trials, eligibility and unresolved availability |
| [India, managed training and scientific access](docs/research/additional-regional-managed-2026-10-24.md) | Regional clouds, training APIs and platform-versus-infrastructure credit |
| [Asia and Pacific clouds](docs/research/additional-asia-clouds-2026-10-24.md) | Regional training products, trials and geographic requirements |
| [Further regional expansion](docs/research/additional-regional-expansion-2026-10-24.md) | Europe, Central Asia, Middle East and Australia |
| [China and specialist training platforms](docs/research/additional-training-platforms-2026-10-24.md) | Container/notebook training and specialist platforms |

### Research data and update process

- [Comparison CSV](docs/research/additional-provider-directory-2026-10-24.csv): one row per research entry, with stable ID, name, parent vendor, category, cost class, training mode, accelerator, free offer, limits, interface, relationship, status, research date, official URLs and report references.
- [Application data](multigpu/data/provider-research.json): the same discovery records plus cost tags, report IDs and explicit research-only/disabled execution capabilities.
- [Research loader](multigpu/research.py): fixed report IDs and allowlisted files, copy-on-read directory data, CSV delivery and document content hashes.
- [Original catalog](multigpu/catalog.py): existing resource modes and implemented capabilities, maintained separately from discovery data.

Research uses official provider documentation, product/pricing pages, terms and official repositories. Unresolved claims remain labelled. It is a dated snapshot rather than a live price feed; offers, stock, quotas and account eligibility must be rechecked before use. Update the cited report, CSV and app JSON together, preserve stable IDs/relationship notes, and run the research consistency checks. No automatic online refresh or scraped price synchronization is implemented.

Before promoting a candidate into training selection, implement its adapter and validate authorized launch, five-minute reporting, quota/session exhaustion, expired credentials, interruption recovery, stop behavior and a usable verified local model export. Independently establish spending protection for any proposed automatic free-only mode. Discovery coverage alone does not satisfy those gates.

## Installation

### Windows release

The packaged application targets **Windows 10/11 x64**. Install the release and open **MultiGPU** from the Start menu. A local build is `dist/MultiGPU-0.5.0-Setup.exe`; `dist/release-info.json` and release acceptance evidence identify its hashes and checks when these artifacts are present.

The installer includes CPython, uv, the launcher and native host components. A system Python is not required. Optional training/provider libraries are prepared later, on request, in the app's environment.

For a first CPU demo, no GPU, provider account or optional training package is needed. CUDA recipes require a compatible NVIDIA GPU, sufficient VRAM, a working driver and the prepared CUDA dependencies. Image preparation and provider/client installation need internet access. Keep enough disk space for dependencies, input staging, checkpoints and model copies; storage requirements depend on the recipe and data. The app checks relevant operations but cannot promise a fixed minimum for every custom model.

Microsoft Edge WebView2 Evergreen Runtime is required. If missing, the launcher explains it and offers a Microsoft-signed bootstrapper with consent; network access is required. Installation is per-user without administrator elevation. Current installers are unsigned; clean-machine first-install qualification remains outstanding.

| Content | Default Windows location |
|---|---|
| Program | `%LOCALAPPDATA%\Programs\MultiGPU` |
| App Python environment | `%LOCALAPPDATA%\MultiGPU\environment` |
| Workspace | `%LOCALAPPDATA%\MultiGPU\workspace` |
| Exports | `exports/` inside the chosen workspace |

Choose another workspace in the tray for the next start. Closing the window hides it in the tray while work can continue. Use **Pause training and exit** deliberately. Remote execution can outlive the controller; inspect provider stop controls and unresolved-attempt warnings before assuming billing ended.

### Source dashboard

Python **3.11+** is required. From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m multigpu --no-browser
```

Open `http://127.0.0.1:8765`. The dashboard and CPU starter have no third-party Python dependencies. Optional `python -m pip install -e .` also installs the `multigpu` console command.

The browser dashboard can run on other suitable Python systems, but native window/dialogs/tray and release packaging target Windows. Other operating systems are not equivalent release qualifications.

### Source desktop and controller options

An unprepared source checkout needs an existing suitable Python and native preparation:

```powershell
.\packaging\prepare-native.ps1
.\start.cmd
```

The source launcher creates/reuses `.venv` from available Python; it does not automatically install a system Python. Terminal/browser development can use:

```powershell
.\start.ps1 -Port 8765 -NoBrowser -DataDir C:\MultiGPU\development-workspace
```

```text
python -m multigpu [--port PORT] [--data-dir DIRECTORY]
                   [--no-browser] [--connection-file FILE]
```

`--port` defaults to 8765; 0 requests an available loopback port. `--data-dir` defaults to `MULTIGPU_HOME`, otherwise relative `.multigpu`. `--no-browser` suppresses browser opening. `--connection-file` writes the chosen URL, process ID and workspace for a launcher. The packaged desktop chooses an available loopback port. Only one controller can own a workspace.

Installing the Python package includes static assets and discovery JSON. The full offline research library currently expects the repository's `docs/` tree or the Windows payload layout; a standalone wheel is not qualified as a complete offline-document distribution.

## Training workflows

### Local first run

1. Open **New training**, use the generated tabular sample and dependency-free recipe.
2. Name the run and inspect settings/data.
3. Choose **Your computer**, CPU/Auto and free-only execution.
4. Preview the exact plan; resolve preflight errors, then start.
5. Inspect loss, checkpoint and held-out evaluation.
6. Verify/export the model and try a prediction with its saved feature schema.

The generated sample demonstrates the workflow; its accuracy is not evidence for a real application.

### Own data and GPU

The wizard follows **Task & data → Training → Compute & limits → Review**. Choose a supported CSV/image directory, inspect it, select a compatible recipe/device and prepare dependencies.

GPU detection and CUDA readiness are separate. Explicit CPU preparation stays CPU. Image preparation follows CPU/CUDA choice; Auto can choose CUDA when NVIDIA hardware is detected. Detection does not prove compatible drivers, sufficient VRAM or success for a particular model.

Drafts persist across navigation/restart. Paid-execution and script-trust confirmations reset on restored/repeated drafts. **Repeat settings** creates an editable draft without launching work.

### Automatic providers

Follow the in-app checklist: open the provider account, prepare its client in the app environment, finish native sign-in, enter nonsecret settings, then **Save & check**. Identity, quota, requested hardware and spending admission are separate observations.

- **Kaggle:** bounded notebook submission, durable identity, quota/status/logs and output retrieval. Immediate stopping uses Kaggle's control.
- **Modal:** detached Function plus persistent artifact Volume; the retired notebook editor is not used.
- **Lightning:** dedicated Studio, requested machine and explicit permission to stop after collection.

These are implemented code paths, not live certification of all plans/hardware/billing controls.

### Assisted providers

Instructions stay inside the app. The downloads are actual training inputs: `training.ipynb` and, for some large/image jobs, `training-payload.zip` containing code/data/recovery assets.

1. Create the assisted run and review the provider guide.
2. Download the notebook and any required payload.
3. Open the provider notebook yourself, choose entitled compute and upload inputs.
4. Run the cells while watching provider runtime and billing limits.
5. Run **Save results** and download `multigpu-results.zip`.
6. Preview/verify it in the app, then import into the same job.

Manual notebooks do not provide guaranteed live telemetry to the controller. Imported history/timestamps are labelled as recorded observations, not independently observed live activity. The app cannot stop or enforce billing for manually managed runtimes.

## Recipes and data

| ID | Model/task | Runtime |
|---|---|---|
| `starter` | Numeric binary classification, logistic classifier | CPU; Python standard library |
| `pytorch` | Numeric binary classification, small 16-unit network | PyTorch CPU or selected CUDA GPU |
| `image` | Frozen ImageNet ResNet-18 backbone and trainable classification head | Torch/torchvision/Pillow; CPU or CUDA |
| `custom` | User `train(ctx)` entry point | Trusted code, user-managed dependencies/state |

New runs use recipe format version 2. Legacy version-1 jobs preserve their original data/evaluation semantics. Tabular wizard defaults are 500 steps, batch 32, learning rate 0.05, seed 42 and 30-minute sessions. Image defaults are five epochs, batch 16 and learning rate 0.001; step target is derived from batches/epochs.

The image recipe trains only the new head. Arbitrary architectures, language-model fine-tuning and large streaming jobs require custom code and suitable hardware.

### CSV inputs

```csv
age,income,label
21,35000,0
46,92000,1
```

This shows the format only: actual training requires at least **five rows per class**. Features must be finite numeric values; labels binary 0/1. Missing/text features are rejected. A complete text header is optional; choose a label name or zero-based index, defaulting to the last column.

Version 2 uses seeded stratified 80/20 validation and training-only standardization, saved with the model. Limits include a 256 MiB CSV, one million rows and approximately 512 MiB estimated in-memory data. Larger inputs need custom streaming code. The generated demo contains 512 rows/four features.

### Image inputs

```text
images/
  cats/
    cat-001.jpg
    ...
  dogs/
    dog-001.jpg
    ...
```

Provide at least two classes and five readable images per class. PNG/JPEG/BMP/WebP/TIFF are accepted, up to 50,000 images; individual images above 40 megapixels are rejected. The saved transform uses RGB, resize/crop to 224 pixels and ImageNet normalization.

Official base weights are downloaded during explicit preparation and checked against the pinned SHA-256. All inputs must fit documented host-memory/staging limits; GPU batches are transferred individually.

## Dependencies and provider setup

Use the app's preparation buttons so packages reach the controller's actual interpreter. Profiles are `cpu`, `image`, `cuda`, `kaggle`, `modal` and `lightning`. Image preparation is device-aware and includes verified base weights. Setup tasks/logs persist, support cancellation and require repair/rechecking after interruption.

The preparation profiles pin Torch 2.14.1, torchvision 0.29.1 and Pillow 12.3.0; CUDA uses `cu130` builds from the official index. See [environment.py](multigpu/environment.py) for exact package/index choices. Optional project extras also exist in [pyproject.toml](pyproject.toml).

Authentication stays in provider-native tools. Supported Windows sign-in buttons open their console/browser. The displayed commands target the app environment, not an unrelated terminal's Python. Connection forms store nonsecret settings, not pasted passwords/tokens.

## Spending and fallback rules

Users buy plans directly from providers. MultiGPU does not purchase plans or silently activate paid fallback. Keep published offer, native account identity, actual entitlement/allowance and app qualification separate.

Free-only is the default. Current automatic admission supports local resources and eligible observed Kaggle quota. Modal/Lightning free-only automation stays blocked because the app cannot independently verify zero out-of-pocket enforcement for the planned run and relevant ancillary charges. That app limitation does not remove the providers' published credit offers.

Paid automatic execution requires a new job-level choice, positive cap and appropriate provider/account settings. A saved provider limit is a user report, not independently verified billing evidence. A local timer or credit balance alone cannot establish a zero provider bill.

Multiple selected providers define primary resource and ordered fallbacks. Compatible continuation preserves the original target; it does not launch unrelated experiments or silently reduce epochs. Uncertain remote attempts reserve their account until reconciled, preventing blind duplicate submissions.

## Progress and recovery

### Observations

Worker heartbeats occur approximately every 30 seconds; five-minute summaries every 300 seconds. Summaries show progress/loss, device, allowance confidence, checkpoint and required action. Short, terminal and imported sessions also receive immediate summaries.

Built-in recipes checkpoint about every 60 seconds at cooperative points and at pause/completion. They cannot guarantee a save inside a long optimizer step. Custom scripts must call their own checkpoint hook. Controller connectivity, last worker event, last training progress and imported-history timestamps stay separate; a connected account does not prove a GPU is running.

### Checkpoint continuation

Checkpoints bind job identity, data fingerprint, recipe/version, target and saved progress/state. Built-in PyTorch/image recovery includes relevant model, optimizer, RNG and cursor information. Portable state is validated and limited to 64 MiB; image assets are referenced separately.

| Action | Behavior |
|---|---|
| Save & pause | Ask a cooperative worker to checkpoint and stop training |
| Resume on this computer | Validate compatible inputs/resources, then continue locally |
| Continue with a notebook | Review a selected eligible assisted resource and exact checkpoint/target |
| Finish evaluation and export | Complete delivery on local CPU from a full-target built-in checkpoint, without more optimizer updates |
| Recover previous checkpoint | Validate retained previous recovery state |
| Relink identical inputs | Locate moved originals through content identity |

Unobservable previous notebook sessions need explicit stopped-session confirmation. Changed checkpoint/configuration/target invalidates the reviewed digest. Older imports require rollback consent. Built-in finalization does not provide a generic recovery implementation for arbitrary custom models.

### Notebook reruns and Drive

Generated notebooks use job-scoped folders and a subprocess worker. Reruns resume the newest compatible verified checkpoint or reuse a verified completed model. Result archives contain the current export/recovery files, excluding backup exports and locks.

Colab notebooks offer optional, **disabled-by-default** recovery to a Drive folder the user explicitly mounts and selects. Checkpoints are copied into immutable, verified committed folders; earlier copies remain. Configured persistence failure pauses training while retaining a local checkpoint. That pause does not stop provider compute. Without durable recovery, provider VM deletion can remove local session files.

### Uncertain remote attempts

If launch acknowledgment is lost, the app preserves the submission identity and blocks blind retries. **Check provider state** reconciles it. **Resolve provider launch** can attach independently verified execution evidence or record the user's explicit console confirmation that an attempt stopped/was absent; user-confirmed evidence is labelled accordingly.

An absent training process does not prove a Lightning Studio stopped. Kaggle immediate stopping remains a provider-console operation. Remote recovery artifacts remain until local verification; cleanup is user-controlled.

## Training efficiency and performance

Version 0.5 measures useful training through verified model delivery. Run details expose an expandable **Measured performance** ledger: stage durations, built-in training throughput, recovered progress, observed repeated work and available peak memory. Missing GPU utilization, network-transfer or provider-stage measurements remain unknown. SDK calls that combine upload/provisioning are labelled as combined observations.

The default tabular PyTorch path reuses host tensors and preserves the original seeded sampler. Routine metrics are sampled at most once per second while exact internal progress and per-report finite-loss checks remain unchanged. Checkpoints and terminal events are immediate; charts include saved-checkpoint measurements. Incremental event ingestion and lightweight runtime polling reduce repeated parsing and transport.

Image **Advanced training settings** offer bounded prefetch and a frozen-backbone feature cache, both opt-in. Prefetch uses two threads with bounded buffers. Feature caching avoids repeated ResNet backbone passes across epochs, validates dataset/model/runtime/precision identity, and retains the original head-training target. The cache choice is bound to checkpoints; it cannot silently change on resume. FP32, synchronous loading, batch size and training targets retain their defaults.

Preview recommendations use compatible saved-run measurements, including observed overhead and dated allowance evidence. They never reorder selected resources automatically. **Use suggestion and preview again** requires a fresh preview and does not start training. Unknown timings stay unknown. Continuation advice can favour an eligible current resource when measured migration overhead exceeds estimated remaining training time.

Prepared local notebooks/payloads are reused only after specification, input, worker-code and output hashes match. This creates no new remote storage. Measured checkpoint costs and compatible delivery history inform cooperative runtime reserves; they do not establish provider billing enforcement.

See [performance details and measured results](docs/PERFORMANCE.md) for exact behavior, limits and reproducible commands:

```powershell
python -m multigpu.performance_benchmark --workspace .test-runs/performance --device cpu
```

The benchmark runs local fixtures only, never provisions providers, and never downloads pretrained weights implicitly. Recorded CPU results show faster tabular execution and a useful optional image-feature cache on small fixtures; these are not qualified CUDA or cloud speedups.

## Model export and prediction

Delivery checks paths, hashes, job/input identity and required files before saving the local copy. Downloads and built-in prediction verify files again. Remote retention and local verification are separate facts.

Typical tabular export:

```text
manifest.json
model.json
checkpoint.json
evaluation.json
input_schema.json
training.json
README.txt
predict.py
multigpu_runtime/
```

Image exports add their verified base-weight asset and preprocessing/class metadata. Exact files vary by recipe. Custom scripts choose their export files; valid custom artifacts do not automatically have a generic UI inference adapter.

From an exported model directory:

```powershell
python predict.py --features '[0.1, 0.2, 0.3, 0.4]'
python predict.py --image C:\data\example.jpg
```

Use feature names/order/count from `input_schema.json`; the numeric example matches the generated four-feature demo. Built-in tabular prediction needs only Python, including exports from the small PyTorch recipe. Image prediction uses CPU and requires Torch/torchvision/Pillow. Held-out metrics and probabilities are estimates, not guarantees on future data.

## Custom training scripts

Choose **Custom PyTorch / Python** and a trusted file implementing `train(ctx)`. A complete working example is [examples/custom_train.py](examples/custom_train.py): a dependency-free toy regressor demonstrating the hooks, not a PyTorch model implementation.

| Context member | Responsibility |
|---|---|
| `ctx.spec` | Validated settings and original target |
| `ctx.output_dir`, `ctx.device`, `ctx.gpu_index` | Output location and reported execution information |
| `ctx.step`, `ctx.loss` | Current reported progress |
| `ctx.resume_state` | Previous custom payload when resuming |
| `ctx.should_stop()` | Frequently check pause/session limits |
| `ctx.report(loss, step=None, **metrics)` | Finite loss and monotonic bounded progress |
| `ctx.checkpoint(state)` | Save a JSON-serializable resumable payload |
| `ctx.export(files)` | Map safe relative paths to bytes, text or source `Path` values |

Save model, optimizer, data cursor/sampler, RNG and other state needed for faithful resume. Checkpoint the latest reported step before returning, even if stopped before the first update. An early return without a stop or completion without required checkpoint/export is an error. Custom scripts own evaluation, dependencies, resource use and model-specific inference.

Custom Python runs with the user's permissions. Per-run trust resets on restored drafts; automatic providers also require custom-script authorization. This is not an untrusted-code sandbox.

## Architecture and storage

The frontend is plain HTML/CSS/JavaScript. Python's standard-library HTTP server and SQLite form the backend; training/provider packages are optional. The Windows application hosts the same local UI using .NET Framework/WebView2.

```mermaid
flowchart TD
    UI[Windows window or browser] --> API[Loopback controller]
    API --> PLAN[Preflight and resource planner]
    PLAN --> LOCAL[Local worker]
    PLAN --> CLOUD[Automatic adapters]
    PLAN --> NB[Assisted notebook preparation]
    LOCAL --> FILES[Checkpoints and verified exports]
    CLOUD --> FILES
    NB --> IMPORT[User execution and verified import]
    IMPORT --> FILES
    API --> STORE[SQLite workspace state]
```

### Source map

Paths below are inside [multigpu/](multigpu/).

| Module | Role |
|---|---|
| `server.py` | Routes, session/Origin guards, static files |
| `service.py` | Lifecycle, drafts, setup, reconciliation and delivery |
| `catalog.py`, `planner.py` | Directory/capabilities, recipe validation, resource and spending admission |
| `connectors.py`, `onboarding.py` | Native clients/staging/collection and in-app instructions |
| `environment.py`, `process_control.py` | Persistent dependency setup and owned Windows process trees |
| `worker.py`, `recipes.py`, `datasets.py` | Training contract, algorithms and inputs/assets |
| `notebook_runtime.py` | Isolated sessions, reruns, durable recovery and packaging |
| `artifacts.py`, `imports.py`, `prediction.py` | Integrity, result import and supported inference |
| `store.py` | SQLite persistence and workspace ownership lock |
| `research.py`, `data/provider-research.json` | Read-only discovery records, fixed report IDs, bundled documents and CSV |
| `desktop.py` | Controlled native dialogs/sign-in/open-folder actions |
| `benchmark.py` | Same-host two-GPU benchmark, not cross-provider qualification |
| `performance.py`, `performance_benchmark.py` | Measured attempt costs, preview advice and explicit local recipe benchmarks |
| `image_pipeline.py`, `staging.py` | Bounded image prefetch/features and verified prepared-asset reuse |
| `static/` | Frontend presentation and interactions |

[packaging/](packaging/) contains native window/launcher and installer tools; [tests/](tests/) contains backend/browser regressions; [docs/](docs/) contains the provider research; [examples/](examples/) contains custom integration code.

Workspaces contain `state.sqlite3`, SQLite journal files, `controller.lock`, job-specific specs/events/checkpoints/assets, cache, diagnostic logs and default `exports/`. Schema-version-two migration retains `state.before-v2.sqlite3`. Local storage is not promised to be encrypted at rest.

Credentials remain in native provider configuration. The app stores nonsecret settings and observed account/allowance evidence. Back up important exports and the workspace consistently; copying only a live main SQLite file can omit WAL state. Prefer a stopped controller or a proper SQLite backup procedure.

### Execution and delivery boundaries

A draft becomes a reviewed preview before a job can launch. The preview binds the selected resource order, validated inputs, target and spending policy. Changed settings or data require another preview. Attempts retain durable execution identity so a controller restart or lost response can trigger reconciliation rather than duplicate submission.

Worker events and checkpoints describe training progress; provider polling describes the remote attempt; artifact verification describes delivery. These observations can disagree temporarily. A completed remote process can still need collection/export, and a completed training target can still need evaluation. The UI exposes actions from the current job's `allowed_actions` instead of treating every status as universally resumable or cancellable.

The adapter layer contains account probes, staging, preparation/submission, reconciliation, polling, supported stopping/cancellation and collection. Capability flags describe which operations are available. Extending the adapter and original catalog is a separate change from adding an entry to research data.

## Workspace maintenance and privacy

### Settings and workspace selection

App settings include the default export destination and optional desktop notifications; notifications default off. Drafts, setup checklist progress, provider observations and run history persist in the selected workspace. Windows launcher preferences, window state and the WebView profile live under the desktop configuration directory, normally `%LOCALAPPDATA%\MultiGPU`.

Changing the workspace through the tray takes effect on the next start and leaves existing runs in their original workspace. It does not migrate data. Source runs can select a workspace through `--data-dir` or `MULTIGPU_HOME`. Avoid pointing independent controllers at the same directory.

### Backup, recovery and cleanup

1. Save/pause active training and check provider-side state before shutdown. Closing only the window leaves the tray/controller available.
2. Stop the controller before making a filesystem backup. Copy the complete workspace and any exports saved outside it, along with original input files needed for continuation.
3. Keep the relevant recipe/custom code and environment information. An export and a resumable training state serve different purposes; preserve both when continuation matters.
4. Restore into an unused workspace and open it with a compatible application version. Recheck dependencies and native provider authentication; the workspace is not a portable credential bundle.
5. If input paths moved, use the identical-input relinking workflow. Do not edit hashes or substitute different training data to bypass identity checks.

Archiving a run changes history visibility; it is not proof that provider compute stopped or remote files were deleted. Cache cleanup is limited to supported disposable content and is not a workspace reset. Remote recovery artifacts are not automatically deleted after export; inspect the verified local copy and manage remote cleanup yourself. Do not remove active locks or delete job directories while a controller/worker owns them.

### Where data goes

| Operation | Data handling |
|---|---|
| Browse bundled research | Reads local reports/JSON/CSV; clicking an official link opens that external site |
| Dependency preparation | Downloads selected libraries or official model weights into the app environment/cache |
| Local training | Reads selected inputs and writes local job state, checkpoints, logs and exports |
| Automatic provider training | Stages selected code/data and recovery assets for the user's connected provider account |
| Assisted training | Produces notebook/payload files that the user uploads to a chosen provider |
| Import/export | Reads result artifacts, validates identities/hashes and writes the chosen local destination |
| Provider sign-in | Uses provider-native tools/browser flows; account credentials remain under their native configuration |

Dataset names, local paths, account identities and model outputs can themselves be sensitive even when tokens are redacted. The application does not encrypt the workspace at rest. Apply your own filesystem permissions, backups and provider privacy settings to the data you use.

## Local API

This is trusted local controller infrastructure, not a public/multi-tenant service. It binds `127.0.0.1`. `/api/bootstrap` returns the session token in `X-MultiGPU-Token`; JSON mutations require it. Host, Origin and cross-site checks apply. Another process running as the same user remains inside the trust boundary.

| Method | Route/group | Purpose |
|---|---|---|
| GET | `/api/bootstrap`, `/api/hardware`, `/api/jobs`, `/api/jobs/:id` | Workspace, hardware and run observations |
| GET | `/api/runtime` | Mutable jobs, hardware, settings, connections and setup tasks without static research/catalog payloads |
| GET | `/api/research/documents/:id` | Allowlisted bundled report, source date, Markdown content and SHA-256 |
| GET | `/api/research/directory.csv` | Download the complete research comparison CSV |
| GET/POST | `/api/draft`, `/api/providers/:id/setup`, `/api/jobs/:id/setup` | Draft/checklist progress |
| POST | `/api/datasets/inspect` | Data validation before execution |
| POST | `/api/hardware/recheck` | Refresh local hardware observations |
| POST | `/api/jobs/preview`, `/api/jobs` | Preview, then create with matching digest |
| POST | `/api/providers/:id/probe` | Native account/resource evidence |
| POST | `/api/environment/setup` | Explicit preparation profile/device |
| GET | `/api/environment/tasks`, `/api/environment/tasks/:id` | Persistent setup observations |
| POST | `/api/environment/tasks/:id/cancel` | Cancel setup |
| POST | `/api/jobs/:id/pause`, `cancel`, `resume`, `reconcile` | Supported lifecycle actions |
| POST | `/api/jobs/:id/continue-guided`, `finish-delivery` | Preview/commit checkpoint-bound recovery |
| POST | `/api/jobs/:id/resolve-submission`, `relocate-inputs`, `recover` | Resolve attempts and recover verified state |
| POST | `/api/jobs/:id/import`, `export`, `predict`, `archive` | Delivery, inference and history |
| GET | `/api/jobs/:id/notebook`, `notebook-payload`, `bundle`, `artifacts` | Supported training assets/model downloads |
| GET | `/api/diagnostics/logs` | Redacted diagnostics |
| POST | `/api/desktop/select-path`, `/api/desktop/provider-signin` | Supported native file selection and provider sign-in |
| POST | `/api/settings`, `/api/cache/cleanup`, `/api/shutdown` | Settings, disposable cleanup and exit |

Short action names share `/api/jobs/:id/`. Use `allowed_actions` from the current job; state and capabilities can forbid an operation. [server.py](multigpu/server.py) and [service.py](multigpu/service.py) define the complete routes/payload checks.

Bootstrap keeps `providers` (14 original resource records), `research_providers` (142 discovery records) and `research_documents` (13 report descriptors) separate. A research record is not a valid new execution capability. Report endpoints accept fixed IDs, not filesystem paths; see [research.py](multigpu/research.py) for the ID mapping. JSON mutations accept object bodies up to 1 MiB. This API has no public compatibility/versioning guarantee for third-party clients.

New job responses may include `performance`; previews may include `performance_recommendations`, and continuation previews may include `continuation_performance`. Image specifications accept `image_loader: "sync" | "prefetch"` and `image_feature_cache: boolean`, with acceleration off by default. `GET /api/jobs/:id?observations_revision=<previous revision>` omits unchanged metrics/events/logs and returns `observations_unchanged: true`; clients must retain their previous observations. The original GET without a revision remains compatible.

Example for an intentionally isolated local controller:

```powershell
$base = 'http://127.0.0.1:8765'
$bootstrap = Invoke-WebRequest -UseBasicParsing -Uri "$base/api/bootstrap"
$headers = @{
    'X-MultiGPU-Token' = [string]($bootstrap.Headers['X-MultiGPU-Token'] | Select-Object -First 1)
}
$spec = @{
    name = 'Local API example'
    recipe = 'starter'
    providers = @('local')
    steps = 50
    device = 'cpu'
    free_only = $true
}
$plan = Invoke-RestMethod -Method Post -Uri "$base/api/jobs/preview" `
    -Headers $headers -ContentType 'application/json' -Body ($spec | ConvertTo-Json)
# Review $plan, especially preflight errors and selected resource, before creating.
$spec.preview_digest = $plan.preview_digest
$job = Invoke-RestMethod -Method Post -Uri "$base/api/jobs" `
    -Headers $headers -ContentType 'application/json' -Body ($spec | ConvertTo-Json)
$job.id
```

Creation starts a local CPU job; it demonstrates no cloud entitlement. Changed inputs/configuration require another preview.

## Security and trust

- Keep passwords, payment details and API secrets in provider-native tools, not connection forms or shared screenshots.
- Diagnostics preserve actionable errors with credential redaction. Review logs before sharing; redaction is not permission to include secrets.
- Imports validate paths, identities, finite values, size bounds and hashes. Hashes establish consistency, not the trustworthiness of arbitrary code/models.
- The native WebView permits the exact controller origin, routes approved external HTTPS navigation to the normal browser and blocks unsafe origins/schemes. Native dialogs handle file selection/downloads.
- Windows setup children join Job Objects so cancellation/controller crashes do not intentionally leave detached installers running. Custom training still needs cooperative stopping.
- Provider staging contains selected inputs. Inspect notebook/payload contents and provider privacy settings before upload.
- Do not expose the controller as a shared internet API or treat its session token as multi-user authorization.

## Development and testing

Use disposable workspaces, not installed user data. Python 3.11+ runs the core; optional recipe/provider tests need their dependencies. Cloud interface fixtures and real local training test different claims.

```powershell
python -m unittest discover -s tests -p 'test_*.py' -q
```

Version 0.5.0 regression evidence, prepared-environment conditions and exact test counts are recorded in [VALIDATION.md](VALIDATION.md). Fresh environments can skip optional-library/platform-specific cases. Provider fixtures and local performance measurements do not qualify live cloud billing or new GPU hardware.

Browser tests require Node, Playwright and Chrome. Use an isolated loopback controller on an unused port other than 8765, with a workspace **inside `.test-runs/`**; delivery/recovery tests reject other workspace locations. Start it in a separate terminal:

```powershell
python -m multigpu --port 8768 --data-dir .test-runs/browser-state --no-browser
```

Set `MULTIGPU_TEST_URL` to that test controller. Some suites need the completed local fixture from `browser-smoke.cjs`; full smoke/image coverage also needs prepared image libraries/official weights. Delivery additionally needs an absolute `MULTIGPU_TEST_PYTHON` for executing generated notebook code. Artifacts go under `.test-runs/`.

```powershell
$env:MULTIGPU_TEST_URL = 'http://127.0.0.1:8768'
$env:MULTIGPU_TEST_PYTHON = (Resolve-Path .venv\Scripts\python.exe).Path
node tests/browser-smoke.cjs
node tests/browser-contract.cjs
node tests/browser-setup.cjs
node tests/browser-journey.cjs
node tests/browser-delivery.cjs
node tests/browser-recovery.cjs
node tests/browser-provider-tools.cjs
node tests/browser-provider-observations.cjs
node tests/browser-research.cjs
node tests/browser-performance.cjs
```

For a focused check of directory consistency, disabled research capabilities, document access and API boundaries:

```powershell
python -m unittest discover -s tests -p 'test_research.py' -v
```

The research browser suite also checks search/pagination, cost distinctions, retry/stale-response handling, escaped Markdown, focus restoration, mobile overflow and exclusion from training selection. It does not authenticate new providers or launch their compute.

`python -m multigpu.benchmark --output benchmark.json` measures a synthetic same-host DDP workload when two CUDA GPUs and an NCCL-compatible environment are available. It does not qualify a multi-provider cluster or promise speedup for a real model.

## Windows packaging

See [packaging/README.md](packaging/README.md) for prerequisites and acceptance details.

```powershell
.\packaging\prepare-compiler.ps1
.\packaging\build-windows.ps1 -Version 0.5.0
.\packaging\test-payload.ps1
.\packaging\test-native-helpers.ps1
.\packaging\test-native.ps1
```

The pinned toolchain includes CPython 3.12.15, uv 0.12.22, WebView2 SDK 1.0.4258.31 and Inno Setup 6.7.3. The build verifies tool hashes/signatures, uses an explicit file allowlist and records payload/installer hashes. GPU libraries, datasets, credentials, exports and the state database are not part of the base payload.

Payload tests relocate the exact app and exclude system Python. Native tests isolate download/clipboard/navigation/recovery/second-launch checks. `test-installer.ps1` exercises install/uninstall but refuses to replace an existing registered installation.

Upgrade/uninstall are designed to retain workspace, environment, exports, WebView profile and native credentials. Environment repair keeps a backup and rolls back failures; optional dependencies must be prepared again. These behaviors still need clean-Windows and actual upgrade/uninstall qualification.

No project-level `LICENSE` file is currently included. Third-party notices remain in payload runtime/native/licenses directories and [packaging/licenses/](packaging/licenses/).

## Troubleshooting

| Symptom | Check/recovery |
|---|---|
| Tools installed but guide blocked | Recheck setup in the same environment; installation does not verify an account. Inspect task logs. |
| Account connected, training unavailable | Inspect quota, hardware, plan and free-only blockers separately. |
| Researched provider has no Set up or Train button | Its connector is not implemented. Read its evidence or choose an implemented resource; directory presence does not enable execution. |
| Credits available, automatic free-only blocked | Credit and provider billing enforcement are separate. Modal/Lightning remain blocked in this mode until independently verified protection is available. |
| Bundled research missing after a Python-only install | Run from the complete source tree or Windows package; the standalone wheel does not include the complete docs tree. |
| No free Paperspace machine | Keep availability unknown; do not choose a paid machine to satisfy free-only. Consult the recorded documentation conflict. |
| Modal notebook unavailable | Use the Functions connector if admitted or another assisted provider; recover existing output by import. |
| NVIDIA detected, CPU used | Choose a GPU-capable recipe/device and prepare CUDA in the controller environment; inspect actual reported device. |
| Client works only in another terminal | Prepare/sign in through the app or its exact displayed interpreter. |
| Kaggle launch error | Read redacted diagnostics and provider account/quota/console; reconcile uncertain attempts before retry. |
| Manual notebook has no live data | Watch its notebook, then save/download/import results. |
| Import fails | Inspect job/fingerprint/target/hash, incomplete copies, multiple exports and path restrictions; preserve originals. |
| Runtime expired | Import a saved compatible checkpoint and review continuation; configured Drive recovery must have committed successfully. |
| Target reached, export failed | Finish evaluation/export from a supported full-target checkpoint; check disk/permissions. |
| Controller/window disconnected | Reload/reconnect/restart and inspect diagnostics; provider compute can continue. |
| Second controller rejected | Reopen the existing controller or choose another workspace; do not remove an active ownership lock. |
| Optional preparation cancelled or interrupted | Open its persisted task log, repair/recheck the app environment and retry the required profile. |
| Export destination full or inaccessible | Keep the job/recovery files, choose a writable destination with space and retry supported delivery/export. |
| Window closes but training continues | Closing hides to tray. Use the tray's pause-and-exit action and verify remote stop separately. |

## Validation and next work

[VALIDATION.md](VALIDATION.md) records exact releases, hashes and checks. Version 0.4.4 passed 238 Python tests and browser checks for research browsing, cost filters, safe report rendering, mobile layouts, existing setup and local training/export. Windows payload checks cover the bundled research, CPU training/export and restart. Retained 0.4.2 evidence includes real RTX 5050 Laptop CUDA tabular/image training, exact resume, export and standalone prediction.

Version 0.5.0 adds measured stages, sampled telemetry, incremental ingestion, lightweight polling, tensorized tabular batches, optional image acceleration and explicit performance recommendations. Current CPU measurements and their limits are documented in [docs/PERFORMANCE.md](docs/PERFORMANCE.md). No fresh CUDA or live-provider speedup is inferred from them.

Local and fixture tests do not certify live provider lifecycle/billing, real Colab/Drive persistence after VM deletion, every GPU/driver or clean Windows first-install behavior. The 0.4.4 Windows build includes the expanded directory and offline research; the additional entries remain outside executable training resources until their connectors are implemented and qualified.

Next work, tracked in [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md):

1. Keep provider research current and qualify additional connectors, retaining documented uncertainty.
2. Qualify authorized live provider launch, observations, quota exhaustion, interruption, cancellation and usable local export.
3. Independently verify billing protection before enabling more automatic free-only modes.
4. Qualify real notebook recovery, package/network limits and allocated hardware.
5. Complete clean Windows/WebView2, upgrade/uninstall and installer signing.
6. Benchmark permitted parallel training before admitting it; selecting providers alone does not establish a compatible efficient cluster.

## Contributing and licensing

Use [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for the roadmap, [VALIDATION.md](VALIDATION.md) for qualification evidence, [DESIGN.md](DESIGN.md) for interface conventions and [packaging/README.md](packaging/README.md) for Windows release procedures.

Contributions should follow the current architecture, retain credential/target/preview/retention protections, add focused regressions for material logic and state what each check actually proves. Keep automated tests isolated from installed user data and billed provider resources. Report bugs with the app version, recipe, execution mode, expected/actual behavior and relevant redacted diagnostics; omit credentials and private datasets.

For a new provider, distinguish a research-only addition from an executable adapter. Keep official evidence, account eligibility, accelerator support, supported lifecycle controls and app qualification explicit. Preserve the training target, require reviewed spending choices and retain remote recovery data until local verification succeeds.

The repository currently has **no project-level license grant**. Public source availability alone does not establish permission to redistribute or reuse it under an assumed open-source license. Bundled third-party components retain their own licenses/notices in [packaging/licenses/](packaging/licenses/) and the packaged runtime/native notice directories. Provider accounts and workloads remain subject to their respective terms.
