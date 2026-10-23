# MultiGPU 0.2.0 validation

Verified on 23 October 2026 using isolated test workspaces on the current Windows
host. No cloud training jobs were launched. The implementation milestones and
qualification gates are in [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md).

| Check | Result | Evidence |
|---|---|---|
| Full Python regression suite | 116 tests passed, no skips | [.test-runs/final-tests.log](.test-runs/final-tests.log) |
| Real CPU recipes | Starter, PyTorch tabular and pretrained ResNet-18 training, exact resume, held-out evaluation and portable prediction passed | Recipe and worker tests in the full suite |
| Browser smoke | Real tabular/image training, export/prediction, pause/resume, saved setup, ordered resources, history, keyboard and mobile passed | [Browser results](.test-runs/browser/final-browser-results.txt) |
| Browser contract tests | Summary, verified file list, class-zero confidence, explicit rollback, guided continuation controls and cache confirmation passed | Browser results above |
| JavaScript syntax | Application and both browser test scripts passed | Browser results above |
| Relocated Windows payload | Bundled Python, application-owned environment, training/export and persisted restart passed with system Python excluded | [Packaging acceptance](dist/acceptance-results.json) |
| Actual Windows installer | Installation and uninstall passed; workspace, exports and environment retained | Packaging acceptance above |
| Packaged native tray | Readiness and authenticated controller shutdown passed | Packaging acceptance above |

The Python regressions include uncertain provider submissions, monitoring retry
without resubmission, restart reconciliation, account reservation, queued dataset
changes, checkpoint corruption, incomplete imports, image companion assets,
continued guided notebooks, storage failures and alternate export destinations.
Provider calls in these tests are mocks.

## Release artifact

- Installer: [MultiGPU-0.2.0-Setup.exe](dist/MultiGPU-0.2.0-Setup.exe)
- Size: 29,199,480 bytes
- SHA-256: `bc95330c62f2b6fa98d0591feec328823ad90e15b1c2eecc8f62860de6c478b7`
- Bundled runtime: Python 3.12.15; uv 0.12.22
- Exact payload and installer identity: [release-info.json](dist/release-info.json)

The packaged application and launcher hashes match the tested, frozen source.
Optional training dependencies are prepared explicitly after installation.

## Qualification still required

- Live provider accounts: launch, actual allowance exhaustion, stop behavior,
  recovery, usable model delivery and enforceable billing controls.
- Actual CUDA hardware and a clean Windows 10/11 virtual machine.
- Installer signing; the current installer is unsigned.

Modal and Lightning credit-backed free-only automation remains blocked until
zero-spend enforcement is verified. Provider quotas and available hardware are
account observations, never guarantees. Assisted notebooks require the user to
launch their notebook and bring results back for verification. Custom checkpoint
semantics are defined by the user's script; portable checkpoint files are bounded
to 64 MB. Cross-provider synchronous training remains outside this release.
