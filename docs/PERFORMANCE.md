# Training efficiency in MultiGPU 0.5

The objective is useful, recoverable training completed within the user's selected allowance, through a verified local model export. A high samples/second figure alone does not include preparation, input transfer, runtime startup, evaluation or delivery.

## Behavior and defaults

- The FP32 model, batch size, sampler, optimizer, epoch/step target and selected provider order remain unchanged by default.
- Built-in tabular PyTorch training constructs host tensors once and indexes them using the original seeded Python sampling order. Only minibatches move to the execution device.
- Loss remains checked for finite values on every report. Routine metric events are sampled at most once per second. Checkpoint, pause, error and terminal observations remain immediate; charts also include saved-checkpoint observations. Internal progress is not sampled.
- Heartbeats remain approximately 30 seconds and summaries five minutes. Terminal summaries are immediate.
- Event ingestion uses file identity, byte offsets and a verified boundary. Partial final lines wait for completion; truncation/replacement resets the bounded history. Imported history remains display-only.
- Initial bootstrap supplies static catalog/research data. Routine dashboard refreshes use `/api/runtime`; unchanged run details omit metric/event/log histories using an observation revision.

## What is measured

New worker observations use `performance.format = multigpu-performance-v1`. They include source, attempt, timestamp, recipe/version, device, runtime and hardware identity. Older jobs remain readable without these fields.

| Observation | Interpretation |
|---|---|
| Worker stage durations | Preparation, data loading, training, checkpointing, evaluation and export measured with a high-resolution wall clock |
| Controller stages | Local preparation/startup, provider staging/submission, collection and verified local export where observed |
| Submission/upload/startup | Some SDKs expose one combined operation; the app records that combined duration rather than inventing separate values |
| Samples/second | Samples processed by built-in optimizer steps divided by measured training-stage time; inspect total stages for end-to-end cost |
| Recovered steps | Progress loaded from the verified checkpoint at attempt start |
| Repeated steps | Observed lost progress when available, labelled as a lower bound; otherwise unknown |
| Peak process memory | OS-reported peak working set/RSS, including runtime/model loading |
| Peak allocated GPU memory | PyTorch allocator high-water observation when an actual CUDA runtime is available; not total device usage |
| GPU utilization / transferred bytes | Unknown when no independent observation is available; local file sizes are not presented as measured network traffic |

Worker timing is attempt-scoped. Controller timing is labelled separately and can include coordination latency. Local startup uses the worker's initialization timestamp rather than polling delay. Combined SDK stages with unknown overlap are excluded from additive ETA recommendations. External sign-up, manual notebook setup, provider queue time before observability, and optional dependency preparation are not inferred from training throughput. Preparation tasks retain their existing timestamps/logs.

Performance sidecars are optional: failure to save one does not invalidate an otherwise verified model. Performance records do not grant provider access or establish billing protection.

## Optional image acceleration

In **Training → Advanced training settings**, image jobs offer:

| Option | Default | Behavior |
|---|---|---|
| Image loading | Synchronous | Experimental bounded prefetch uses two local threads and at most two pending batches; it does not spawn Windows processes |
| Cache frozen image features | Off | Extracts verified ResNet-18 backbone features once, then trains the same classification head for the original target |

Prefetch retains the original order and per-read content hashes, and rejects buffer configurations above 128 MiB. Feature caching is restricted to the built-in frozen-backbone recipe: it is not appropriate for changing backbones or random per-epoch augmentation.

Feature cache identity binds image content, pretrained weights, preprocessing, dtype, batch size, device/hardware, Torch/torchvision/Pillow versions and relevant backend settings. Cache files have hash-checked manifests, bounded shapes, finite FP32 values and weights-only loading. Incomplete or corrupt caches are rebuilt; RAM and disk checks can reject preparation. Cancellation during extraction retains the initial resumable checkpoint. At most one feature cache is kept in each job directory, with feature tensors capped at 128 MiB.

The feature-cache choice changes the training fingerprint and is recorded in checkpoint state. It cannot silently change when resuming. Default image execution remains available. Equivalent evaluation is tested within numerical tolerance; bitwise equivalence across acceleration modes/hardware is not promised. Exact resume is tested within the same execution mode and environment.

Pinned transfers, mixed precision, compilation, TF32 overrides, automatic batch-size changes and cross-provider distributed training are not enabled by this release. They need separate hardware/workload qualification. The existing same-host two-GPU DDP benchmark remains separate.

## Preparation reuse and recovery reserve

Generated notebooks and separate training payloads can reuse their existing local artifact after validating its content hash and a key covering the current specification, selected input contents and portable worker code. Changed inputs/code or damaged artifacts trigger rebuilding. Repeated downloads therefore avoid repeated staging/compression when a valid prepared copy exists. There is one current notebook/payload per job, subject to existing size/path checks and cache cleanup controls.

This is local reuse. It does not upload a shared dataset service, create remote volumes or retain new billable provider storage. Provider account reservations and uncertain-attempt reconciliation remain unchanged. Settings cache cleanup removes generated download copies and optional feature caches only after local workers stop; original data, pretrained weights, models and checkpoints are retained. A later cached-mode resume can rebuild its disposable features.

Workers reserve time using measured checkpoint costs plus compatible historical evaluation/export costs where available. The reserve supplements the existing cooperative runtime guard. Long individual optimizer steps can still cross a deadline; neither the timer nor these estimates establish a zero provider bill. The original target is preserved when pausing.

## Recommendations require acceptance

Preview estimates use compatible completed, verified runs: matching input/configuration fingerprint, provider/resource, acceleration mode and relevant runtime/hardware observations. Missing measurements remain unknown. Estimates include observed overhead and show their saved-run evidence and date. Remaining quota is a dated observation, not a continuously verified balance.

When Auto permits it, measured local CPU execution can be compared with a selected local CUDA resource. The app does not benchmark unselected providers or provision cloud resources in the background. Accepting a suggestion changes only the reviewed resource choice/order and produces another preview; it never starts training. Paid/free eligibility is checked again at launch.

Continuation previews can advise staying on an eligible current resource when compatible historical migration overhead exceeds its estimated remaining training time. Unknown migration costs do not become claimed speedups. The user can retain the selected continuation choice.

## Reproduce local measurements

Use an explicitly selected interpreter/environment and disposable benchmark workspace:

```powershell
python -m multigpu.performance_benchmark --workspace .test-runs/performance --device cpu
python -m multigpu.performance_benchmark --workspace .test-runs/performance --device cpu `
  --images C:\data\class-folders --weights C:\models\resnet18-f37072fd.pth
python -m multigpu.performance_benchmark --workspace .test-runs/performance-cuda --device cuda `
  --images C:\data\class-folders --weights C:\models\resnet18-f37072fd.pth
```

The command does not download weights or launch providers. CUDA must actually be available; it does not silently fall back to CPU. It creates a fresh benchmark directory, records runtime import cost separately, and reports first/subsequent recipe runs. First recipe runs are **not** cold OS-cache measurements. Image variants use the same three-epoch target and include feature extraction and export. Feature-cache extraction is rebuilt in each fresh run; a resumed cache hit is separately covered by recovery tests.

## Recorded CPU results — 25 October 2026

Windows, Python 3.12.14, Torch 2.14.1+cpu, one explicitly selected Torch CPU thread. These are small deterministic fixtures, not general speedup promises.

Paired runs used the pre-change repository code and the new code in alternating order, with identical data/settings and three repeats. Timings below include worker preparation, evaluation and export; process startup/import is recorded separately in the raw evidence.

| Workload | Before median | After median | Observation |
|---|---:|---:|---|
| CPU starter, 1,000 steps | 2.367 s | 0.486 s | About 4.87× faster on this fixture |
| PyTorch tabular, 1,000 steps | 4.877 s | 1.985 s | About 2.46× faster on this fixture |
| Default image, 24 images / three epochs | 4.909 s | 4.880 s | Essentially unchanged within timing variation |

An isolated current-code comparison recorded subsequent-run medians of **3.017 s synchronous**, **2.878 s prefetch**, and **1.475 s frozen features**, with the same image target, evaluation accuracy and recorded loss. Feature extraction/export are included. Prefetch's small gain does not justify making it the default. Final 1,000-step event logs are roughly 4–5 KiB versus about 250 KiB before sampling, including the new performance summaries.

Raw local evidence: `.test-runs/performance-paired/comparison.json`, `.test-runs/performance-image-paired/comparison.json`, and `.test-runs/performance-final/benchmark-564886ac6aef4ae480b7f86c13ef18eb/report.json`. These generated files are not required in source distributions.

Fresh CUDA/ROCm/TPU/Gaudi performance is not established by these CPU measurements. No provider accounts or cloud compute were used. Live provider transfer/startup/billing, cold-machine dependency preparation and device-specific speedups remain separate qualification work.
