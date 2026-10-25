# MultiGPU user journey implementation plan

## Version 0.4.4: bundled provider research — completed 25 October 2026

- Added 142 researched compute offerings/access routes alongside the existing 14 resources, with separate cost and integration labels, search, filters and pagination.
- Bundled 13 detailed official-source reports and a CSV comparison for reading inside the app without network access.
- Corrected Paperspace's free-compute wording; kept credits, conditional GPU access, institutional allocations and paid compute distinct.
- Kept unimplemented research entries outside account probes and training selection. Live provider qualification remains required before admitting new workers.
- Verified backend/API boundaries, safe report rendering, desktop/mobile browsing, keyboard recovery, existing local training/export and the Windows payload. See [VALIDATION.md](VALIDATION.md) for evidence and installer identity.

## Official-source research and documentation: 24 October 2026

The completed [provider audit](docs/PROVIDER_RESEARCH.md) separates free GPU, conditional GPU, recurring/introductory credits, free CPU, paid compute and existing hardware. The expanded README documents the current implementation and operating limits. This documentation update does not reship the 0.4.3 binary.

Follow-up implementation must synchronize directory tags/filters and admission with that audit: treat Paperspace free availability as unverified; retain free CPU services without implying free GPU; separate Modal recurring credits from Lightning starting credits; preserve Databricks conditions and Codespaces workload restrictions; keep unknown hardware, allowance and billing evidence unknown. Qualify accounts and costs before enabling a new automatic mode.

## Version 0.4.3: Modal compute after notebook retirement

- Keep Modal's Functions/Volumes connector and stable provider ID; label it Modal.
- Remove the retired hosted-notebook launch, payload and guided-fallback routes.
- Explain the shutdown before setup; preserve billing admission and saved result import/recovery.
- Verify stale saved jobs, direct download endpoints, free-only rejection and paid compute eligibility; rebuild the Windows installer with accurate provider guidance.

## Version 0.4.2: training-to-delivery fixes

Delivered on 24 October 2026: Windows installer, 224 passing Python tests, eight browser suites, exact-payload/native acceptance and real RTX 5050 CUDA qualification. See VALIDATION.md for evidence and the remaining live-provider and clean-install gates.

Implementation follows the real local/Modal/Colab walkthrough on 23–24 October:

- Job-scoped notebook payloads and subprocesses; reruns resume verified progress
  or reuse completed exports. Result archives exclude backup exports and locks.
- Optional explicitly mounted Drive recovery; hash-verified committed checkpoint
  copies, preserved history, and worker pause on configured storage failure.
- Preview-bound assisted continuation and finishing built-in evaluation/export
  from full-target checkpoints, without more optimizer updates.
- Independent early admission blockers, exact frozen Modal allocation, separate
  physical-GPU/CUDA readiness, and device-aware environment preparation.
- Validated imported history and provenance, terminal/import summaries, readable
  import previews, cleared errors and completed-session delivery surfaces.
- Saved-draft presence, secondary repeat export, bounded Modal progress/history
  collection, and retained credential, target and cleanup protections.

Qualification is recorded in VALIDATION.md. Local tests and generated notebook
execution do not certify live Modal billing or a Colab-managed GPU runtime.

## Version 0.4.1: consistent provider setup

Fix the contradictory Kaggle screens reported on 23 October 2026:

- Refresh readiness once for each completed matching installation, including
  completion already consumed by dashboard polling. Installation alone cannot
  verify an account or bypass a missing tool check.
- Apply account results to the current provider object. Retain the newest
  timestamped observation across delayed dashboard responses; accept newer
  failed checks and keep account verification separate from training allowance.
- Update read-only identity, allowance and check time while preserving focused
  fields and edits made during a check. Use the observed native Kaggle username
  when no username is configured. Preserve an explicitly entered zero cap.
- Allow one account check per provider at a time, including after closing and
  reopening setup. Show rejected checks without inventing new verification.
- Refresh overview/directory status when setup closes and on explicit refresh,
  including when the activating button retains focus.

Release gates: response-order browser regressions, existing setup/journey
checks, the 21 affected Python tests, and a rebuilt Windows payload smoke test.
Record outcomes in VALIDATION.md. Preserve the installed workspace; live cloud
training, quota exhaustion and billing qualification remain separate work.

## Version 0.4: complete-journey reliability

Implemented after the end-to-end review, for beginners and experienced trainers equally:

- One reviewed resource/mode/device resolution controls creation; invalid preflight blocks every preparation route, including alternate notebook downloads.
- Submission evidence separates proven not-started failures from uncertain launches. Existing executions can be attached with account/attempt proof; explicit console confirmations retain their user-confirmed provenance. Unstopped Lightning Studios remain reserved.
- Account verification, allowance, job eligibility and live qualification remain distinct. Known paid GPU and metered notebook modes cannot masquerade as free-only execution.
- Built-in recipes and custom scripts have visible entry points, optional custom input files/folders, exact review and immediately invalidated input/trust checks.
- Native sign-in uses fixed provider commands; persistent setup tasks support cancellation. Windows Job Objects own installer children before they execute. Interrupted setup requires rechecking/repair.
- Initial reconnect finishes initialization once. Focused fields, log selections and recovery snapshots survive polling while status/actions/export advance. Assisted sessions explain their import-based observations.
- Completed model delivery uses frozen input metadata; resume accepts content-verified relocated inputs. Checkpoint import validates, hashes and commits the same captured bytes. Prediction is capability-gated and names saved features.
- Attention/history/actions are consistent; logs retain redacted delivery failures; native tray notifications, second-launch restoration, crash recovery, version checks and retained-environment repair are implemented.

Use existing local HTTP/SQLite contracts and additive metadata. User data, checkpoints, target, paid opt-in and remote recovery retention stay protected. Qualification remains a release gate: local/fixture tests do not certify live provider entitlements, billing or CUDA. Current evidence is recorded in VALIDATION.md.

Approved direction: Windows first, with both tabular and image classification,
guided setup, reliable recovery and verified local model delivery.

## Milestones

1. **User intent and navigation:** persisted four-step drafts, revision-bound
   previews, server-checked preview digests, route-safe asynchronous responses,
   stable keyboard focus during polling, accessible mobile navigation.
2. **Execution reliability:** persist attempt identity before submission,
   reconcile uncertain launches and restarts, retry monitoring without duplicate
   execution, reserve compute, distinguish cooperative pause from cancellation
   and provider-side stop controls.
3. **Provider readiness:** verify actual account/workspace/hardware, show dated
   quota and billing evidence, explicit primary resource and ordered fallbacks,
   correct all thirteen provider capability distinctions. Unverified zero-spend
   enforcement never enables automatic credit-backed free-only jobs.
4. **Beginner training:** inspect CSVs and class-folder images before launch;
   version new recipes with seeded stratified 80/20 validation, minimum five
   examples per class, evaluation and inference examples. Image training uses a
   fixed pretrained ResNet-18 backbone and trainable head; defaults five epochs,
   batch sixteen, SGD learning rate .001 and momentum .9.
5. **Monitoring and delivery:** independent 30-second heartbeat and five-minute
   summary, repeatable guided notebook sessions, import preview and explicit
   rollback, previous-checkpoint recovery, alternative export destinations,
   verified prediction, run history/archive and user-controlled cleanup.
6. **Windows delivery:** application-owned Python 3.12 environment, native path
   selection, observable optional dependency setup, background tray launcher,
   Inno Setup installer, retained workspace/exports during upgrade/uninstall.

## Contracts and compatibility

Extend the existing loopback HTTP controller and SQLite JSON store. Drafts never
persist paid/trusted-code confirmations. New HTTP job creation requires a current
preview digest. Keep execution mode, phase, monitoring connection, submission,
collection and export status separate. Persist provider intent before mutating a
provider. Unknown remote state reserves its account and blocks duplicate resume.

Back up SQLite before version-two metadata migration. Preserve existing job IDs,
unknown fields, outputs and version-one recipe/checkpoint behavior. New recipe
semantics are explicitly versioned. Keep native provider authentication outside
the app. No provider credentials are collected by the dashboard.

## Acceptance

Use isolated test workspaces and mocked providers for automatic tests. Cover
delayed paid/free previews, reversed run reads, restart/draft retention, uncertain
submissions, Wi-Fi loss, account mismatch, quota boundaries, early pause, two
guided sessions, stale/older/incompatible imports, corrupt current checkpoint,
full export drive, alternate export, reloaded predictions, keyboard and mobile
completion. Test both training recipes on CPU and qualify CUDA independently.

Each live provider/mode must demonstrate launch, reporting, quota/session expiry,
interruption recovery, stopping and usable local model export before its status
is changed to qualified. Automated tests must never launch billed jobs.

## Release gates

Windows 10/11 x64 is the first packaged target. Pin dependency combinations and
installer inputs. Preserve user data by default. Cross-provider synchronous
training, LLM fine-tuning, other packaged operating systems and automatic remote
artifact deletion are outside this release. Live account billing, actual CUDA
compatibility and installer behavior require separate qualification; simulated
results must never be presented as live certification.

## Implementation status — 23 October 2026

The six milestones are implemented in the local application. Verification is
recorded separately from capability availability:

| Area | Current evidence |
|---|---|
| Four-step setup, drafts, resource order, focus, async guards | Real browser acceptance and deterministic response-order tests |
| Tabular and image classification | Real CPU training, exact resume, validation, reloaded and standalone inference |
| Provider execution/reconciliation/stopping | Mocked native-interface regressions; live account qualification remains required |
| Imports, rollback, checkpoint recovery, export retry | Local worker/artifact regressions, including continued guided sessions |
| Windows distribution | Compiled installer, relocated bundled-runtime acceptance, isolated install/uninstall preservation |
| CUDA, clean Windows VM, signed distribution | Not qualified by the current test environment |

The release does not enable unverified credit-backed zero-spend automation or
cross-provider synchronous training. Installer acceptance uses isolated folders
on the current Windows host; it does not replace a separate clean-machine VM
or real-provider billing qualification.

## Version 0.3: Windows window and in-app setup

- Open the workspace in a dedicated WebView2 Windows window, retaining the tray,
  guarded shutdown, workspace preservation and provider-native authentication.
- Keep provider account pages in the user's normal browser; restrict the app
  window to its own controller origin.
- Replace instruction-archive downloads with contextual provider and training
  setup guides. Save navigation/acknowledgements and derive verified readiness
  from current observations.
- Provide actual notebook/training payload downloads only when required by the
  provider. Explain upload, runtime selection, execution and result verification
  inside the app. Preserve validated checkpoint continuation.
- Verify persisted guides, stale responses, false readiness prevention, notebook
  delivery, dedicated-window startup, downloads and installer behavior.
## Version 0.5 — useful training per allowance

Implemented measured worker/controller stages, optional performance observations, per-second metric sampling with exact internal progress, incremental event cursors, lightweight runtime snapshots and unchanged-history suppression. Tabular PyTorch batches reuse host tensors with the original sampler. Image prefetch and verified frozen-backbone feature caching remain opt-in, with mode-bound recovery and bounded buffers/cache files. Prepared local training files are reused only after input/code/output verification; no new remote storage is created.

Preview recommendations use comparable verified historical runs and require acceptance plus another preview. Continuation advice preserves the reviewed choice, and measured checkpoint/delivery costs inform cooperative runtime reserves. CPU baseline/variant measurements and unavailable CUDA/cloud observations are documented in docs/PERFORMANCE.md. Mixed precision, compilation, automatic batch changes, provider entitlement expansion and cross-provider distributed execution remain deferred. Validation evidence is in VALIDATION.md.
