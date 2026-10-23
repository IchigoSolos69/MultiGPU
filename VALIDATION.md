# MultiGPU 0.3.1 validation

Verified on 23 October 2026 using isolated test workspaces on the current Windows
host. No cloud training jobs were launched. The implementation milestones and
qualification gates are in [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md).

| Check | Result | Evidence |
|---|---|---|
| Full Python regression suite | 140 tests passed, no skips | [.test-runs/v031-python-tests.log](.test-runs/v031-python-tests.log) |
| Real CPU recipes | Starter, PyTorch tabular and pretrained ResNet-18 training, exact resume, held-out evaluation and portable prediction passed | Recipe and worker tests in the full suite |
| Browser smoke | Real tabular/image training, export/prediction, pause/resume, saved setup, ordered resources, history, keyboard and mobile passed | [Browser results](.test-runs/browser/final-browser-results.txt) |
| Browser contract tests | Summary, verified file list, class-zero confidence, explicit rollback, guided continuation controls and cache confirmation passed | Browser results above |
| In-app setup | Provider/job guides, saved steps, explicit notebook download, no automatic instruction ZIP, manual vs observed readiness and responsive layouts passed | Browser results above |
| JavaScript syntax | Application and all three browser test scripts passed | Browser results above |
| Relocated Windows payload | Bundled Python, application-owned environment, training/export and persisted restart passed with system Python excluded | [Packaging acceptance](dist/acceptance-results.json) |
| Provider recovery and error logs | Managed CLI selection, structured recovery, retained redacted provider errors, nested credentials, timeout output, and blocked duplicate submissions passed | [Recovery browser results](.test-runs/browser/recovery-results.txt), diagnostics tests in the full suite |
| Actual Windows installer | Version 0.3.0 install/uninstall passed with data retained; not repeated for this diagnostic patch | [Prior Windows qualification](dist/acceptance-results-0.3.0.json) |
| Packaged native tray | Version 0.3.0 readiness and authenticated shutdown passed; native behavior retained | Prior Windows qualification above |
| Dedicated Windows window | Version 0.3.0 actual MultiGPU.exe, downloads, clipboard, links, origin guards and hide/restore passed; not repeated for this patch | Prior Windows qualification above |

The Python regressions include uncertain provider submissions, monitoring retry
without resubmission, restart reconciliation, account reservation, queued dataset
changes, checkpoint corruption, incomplete imports, image companion assets,
continued guided notebooks, storage failures and alternate export destinations.
Provider calls in these tests are mocks.

The setup regressions also verify that acknowledgement never changes account
readiness, stale checkpoint guides cannot advance, new sessions require new
verified imports, and notebook/payload pairs execute after separate downloads.
Corrupt payloads stop before training. Instructions remain in the app; downloaded
files contain runnable training code and selected inputs.

## Release artifact

- Installer: [MultiGPU-0.3.1-Setup.exe](dist/MultiGPU-0.3.1-Setup.exe)
- Size: 31,087,501 bytes
- SHA-256: `696fde1e9440abd4bdfcb4160b9882c6b1ac2435a136698214f9b22aa2a8501c`
- Bundled runtime: Python 3.12.15; uv 0.12.22
- Native window: Microsoft WebView2 SDK 1.0.4258.31; tested with existing Runtime 155.0.4283.62
- Exact payload and installer identity: [release-info.json](dist/release-info.json)

The packaged application and launcher hashes match the tested, frozen source.
Optional training dependencies are prepared explicitly after installation.

## Qualification still required

- Live provider accounts: launch, actual allowance exhaustion, stop behavior,
  recovery, usable model delivery and enforceable billing controls.
- Actual CUDA hardware and a clean Windows 10/11 virtual machine.
- First-time WebView2 Runtime installation on a machine without it. The app
  explains the Microsoft prerequisite and requests consent before setup; current
  tests reuse the existing Runtime.
- Installer signing; the current installer is unsigned.

Modal and Lightning credit-backed free-only automation remains blocked until
zero-spend enforcement is verified. Provider quotas and available hardware are
account observations, never guarantees. Assisted notebooks require the user to
launch their notebook and bring results back for verification. Custom checkpoint
semantics are defined by the user's script; portable checkpoint files are bounded
to 64 MB. Cross-provider synchronous training remains outside this release.
