# MultiGPU 0.4.4 validation

Verified on **25 October 2026**. The Windows build adds 142 research entries alongside the 14 existing resources, with cost/access filters, pagination, provider evidence and an offline reader for 13 reports. Research-only entries remain outside provider authentication and training selection. Source research dates remain 24 October 2026; this release does not establish live provider entitlements.

| Fresh check | Result | Evidence |
|---|---|---|
| Full Python regression | **238 passed, no skips** | [Log](.test-runs/v044-python-tests.log) |
| Research data and API boundaries | Complete 142-row CSV/data agreement; 13 fixed document IDs; research entries cannot probe or launch; path traversal rejected; Markdown served as data; free GPU distinguished from credits and unverified access | tests/test_research.py in full suite |
| Research browser journey | Search, pagination, cost filters, safe Markdown/tables/links, failed-read retry, ignored stale responses, keyboard focus restoration and research exclusion from training passed at 1440px and 390px | [Proof](.test-runs/browser/research-proof.json), [mobile close-control bounds](.test-runs/browser/research-mobile-header-bounds.json) |
| Existing browser workflows | Provider tools and observations passed; real local training/pause/resume/export/predict, saved drafts and assisted handoff checks passed | [Training journey](.test-runs/v044-browser-smoke.log); provider-tools/provider-observations suites |
| Final Windows payload | Bundled Python 3.12.15; 142 discovery entries, all 13 reports and CSV readable locally; research execution disabled; real CPU training/export and persisted restart passed with system Python excluded | [Payload acceptance](<build/smoke install 330c375c474a411f8a416a21d5395275/acceptance-result.json>) |
| Windows native host | Downloads, clipboard, notebook handoff, navigation guards, browser/controller recovery, second launch and exit passed in an isolated configuration. Accepted native binaries match the final payload; final research JavaScript is covered by the browser checks and source hash comparison | [Native acceptance](<build/native acceptance 018fc2fd63364efa821717223165cacb/native reports/native-acceptance-result.json>) |
| Build integrity | **3,474 manifest files** verified; no unlisted files; packaged sources/research match the workspace; installer, checksum and manifest identities agree | [Source audit](.test-runs/v044-source-audit.json) |

Installer: [MultiGPU-0.4.4-Setup.exe](dist/MultiGPU-0.4.4-Setup.exe), **31,642,666 bytes**. SHA-256: `88c7e6b6e3b7dff322115b1900ed45a5cddd33635a65f53c7f6467afe278ec43`. Manifest SHA-256: `a3808a4302a83c8430eb8a0a99a1ff61547e335297d4d7c2c5bf94362ba1cca2`. [Release record](dist/acceptance-results-0.4.4.json).

No provider accounts or paid compute were used. The existing registered installation and user workspace were preserved; installer upgrade/uninstall replay was not run against that installation. Live cloud lifecycle/billing, new CUDA/ROCm/TPU/Gaudi qualification, clean-machine WebView2 installation and installer signing remain unverified. The additional directory entries are published research, not newly implemented training adapters.

## Retained 0.4.3 qualification

Verified on 24 October 2026. Modal retired its hosted notebook editor on 23 October; its Functions API still supports GPU workloads. This patch retains the Functions/Volumes connector and removes obsolete Modal notebook preparation, downloads and fallback instructions. Existing checkpoint/model import and recovery remain available. Automatic free-only admission remains blocked until independent zero-spend enforcement is verified.

Official sources: [shutdown notice](https://modal.com/changelog/sunsetting-modal-notebooks), [notebook availability](https://modal.com/docs/guide/notebooks), [Functions API](https://modal.com/docs/guide/functions).

| Fresh check | Result | Evidence |
|---|---|---|
| Full Python regression | **230 passed, no skips** | [Log](.test-runs/v043-python-tests.log) |
| Modal retirement | Catalog/setup, unchanged paid Functions eligibility, unchanged free-only rejection, direct notebook/payload/bundle rejection before staging, legacy checkpoint continuation in Colab and completed-model import/export passed | tests/test_modal_retirement.py in full suite |
| Real notebook/browser delivery | Local execution of actual generated Colab notebook, safe rerun, verified import and prediction passed; Modal's retired notebook actions are absent, old results remain importable, explicit Colab alternative is offered | [Proof](.test-runs/browser-v043/delivery-proof.json) |
| Setup and journey browsers | Both affected suites passed, including saved navigation, account verification, recovery and focused inputs | [Setup](.test-runs/v043-browser-setup.log), [journey](.test-runs/v043-browser-journey.log) |
| Exact Windows payload | Bundled Python 3.12.15, file hashes, real CPU training/export and persisted restart passed with system Python excluded | [Payload acceptance](<build/smoke install b97161ae8b664c82b1621584978e722e/acceptance-result.json>) |
| Source/version integrity | 3,458 payload files verified, 28 packaged sources match; native executable version 0.4.3.0; JavaScript and UTF-8 checks passed | [Source audit](.test-runs/v043-source-audit.json) |

Installer: [MultiGPU-0.4.3-Setup.exe](dist/MultiGPU-0.4.3-Setup.exe), **31,553,488 bytes**. SHA-256: `eba5ec98fb59199605f2bd798ae81bf82aeec442424552406f78d9a9c0bfc384`. Manifest SHA-256: `bb836ff278068fd733921ae9a4be1bb3ee9afbd9ff28069a5e3b23e6fdf4276d`. [Release record](dist/acceptance-results-0.4.3.json).

No live Modal job or billing test was performed. The installed app/workspace was preserved. Native-host behavior and real CUDA qualification below are retained 0.4.2 evidence, not fresh 0.4.3 certification. Installer signing, clean Windows/WebView2, upgrade/uninstall replay and real Colab/Drive persistence remain unqualified.

## Retained 0.4.2 qualification

Verified on 24 October 2026 in isolated workspaces on this Windows host. The installed application, its environment and saved workspace were preserved. No cloud provider jobs were launched.

| Check | Result | Evidence |
|---|---|---|
| Full Python regression | **224 passed, no skips** | [.test-runs/v042-python-tests.log](.test-runs/v042-python-tests.log) |
| Real local browser journey | CPU training, pause/resume, image training/prediction, verified export and persisted drafts passed | [Smoke log](.test-runs/v042-browser-smoke.log) |
| Notebook delivery | Actual downloaded Colab notebook executed locally, reran without additional training, packaged one current export, imported 12 loss observations and verified checkpoint/model files, then predicted in the app | [Delivery proof](.test-runs/browser-v042/delivery-proof.json) |
| Recovery APIs and worker | Full-target finalization without optimizer updates, immutable reviewed checkpoints, stale/double commits, stopped guards, selected assisted fallback and malformed/oversize history passed | [Recovery log](.test-runs/v042-recovery-regressions.log), full regression above |
| Recovery UI | Preview/cancel, explicit notebook-stopped confirmation, stale-review rejection and fresh retry, preserved target, CPU delivery wording and 1440/390 layouts passed with fixture responses | [Recovery proof](.test-runs/browser-v042/recovery-proof.json) |
| Existing UI contracts | Contract, setup, journey, provider-tools and provider-observations suites passed, including rollback consent, focus retention, safe account checks and diagnostics | [Contract](.test-runs/v042-browser-contract.log), [setup](.test-runs/v042-browser-setup.log), [journey](.test-runs/v042-browser-journey.log), [tools](.test-runs/v042-browser-provider-tools.log), [observations](.test-runs/v042-browser-provider-observations.log) |
| Recovery storage and Modal snapshots | Disabled-by-default explicit Drive mount, verified immutable copies, corrupted-newest fallback, persistence-failure pause, packaging/rerun locking, incremental snapshots and frozen allocations passed local/SDK-fixture tests | Full Python regression above |
| Actual CUDA | RTX 5050 Laptop, driver 617.14, Torch 2.14.1+cu130, torchvision 0.29.1+cu130: tabular (12 steps) and image (4 steps) training, checkpoint/resume, verified exports and standalone prediction passed | [GPU proof](.test-runs/cuda-audit-ba2977adbc8241a49b8326615041118a/proof.json) |
| Exact payload | Bundled Python 3.12.15, real CPU training/export and persisted restart passed with system Python excluded | [Payload acceptance](<build/smoke install 97a024a84897410d900b173a4bc8c0a2/acceptance-result.json>) |
| Windows host | Exact packaged native window, HTTP/blob/notebook downloads, clipboard restoration, navigation/origin guards, browser/controller recovery, second launch and exit with controller unavailable passed | [Native acceptance](<build/native acceptance 71833681192d47978feb8a543eafa75c/native reports/native-acceptance-result.json>) |
| Windows helpers | Instance separation, controller identity, notification bounds, environment repair/decline/rollback and unresolved-attempt exit guard passed | [Helper acceptance](<build/native helper acceptance aa43245b9250454e9a13540a94c143de/acceptance-result.json>) |
| Release integrity | All **3,458 payload files** verified; **28 packaged sources** match this workspace; native executable 0.4.2.0; static UTF-8 and JavaScript syntax checks passed | [Source audit](.test-runs/v042-source-audit.json) |

The CUDA audit used a separate test environment. Resumed tabular and image model, optimizer and RNG states exactly matched uninterrupted baselines. Tabular portable prediction matched a GPU forward pass within 1e-6; image export prediction used the portable CPU adapter. These results qualify this tested driver/package/hardware combination, not every user's installation. The disposable audit environment was removed after qualification to recover disk space; proof, logs, checkpoints and exported models were retained.

The notebook round trip ran generated code in a local Python process. Drive tests use explicit local folders and fixture mount behavior; they do not certify a real Colab GPU or survival after Google deletes a runtime. Modal tests use SDK fixtures, not a live billing account. Successful notebook imports describe recorded provenance and never claim an independently observed provider session.

## Release artifact

- Installer: [MultiGPU-0.4.2-Setup.exe](dist/MultiGPU-0.4.2-Setup.exe), **31,553,693 bytes**.
- SHA-256: `801a3ff6ba1b8aca5e0458af170c55456523a709a1c29d6ae1311a5f78dd3173`.
- Payload manifest SHA-256: `4e23b4a2f7518e8cf05b6566d4a86d5cdc2cc559a3fcdad18e3f895c6f093d74`.
- [Release acceptance record](dist/acceptance-results-0.4.2.json).

## Qualification still required

- Live provider launch, allowance exhaustion, stopping, interruption recovery, model delivery and billing controls.
- Actual Colab/Drive persistence across provider runtime deletion and other GPU/driver installations.
- Clean Windows 10/11 and first-time WebView2 installation.
- Actual installer upgrade/uninstall replay: the existing registered installation was preserved. Relocated exact-payload and native-host tests passed on this host.
- Installer signing; this installer remains unsigned.

Automatic Modal/Lightning free-only execution remains blocked while independent zero-spend enforcement is unverified. Assisted notebooks require manual execution/import. Original training targets, recovery copies and user-controlled remote cleanup remain protected. Cross-provider synchronous training remains unqualified.

Prior release evidence is retained in [0.4.0 acceptance](dist/acceptance-results-0.4.0.json) and [0.4.1 acceptance](dist/acceptance-results-0.4.1.json).
