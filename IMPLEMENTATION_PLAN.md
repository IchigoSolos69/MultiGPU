# MultiGPU user journey implementation plan

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
