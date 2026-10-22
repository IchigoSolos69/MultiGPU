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
