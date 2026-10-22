"""Measure same-host two-GPU DDP; never imply cross-provider qualification."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import socket
import statistics
import tempfile
import time

from .artifacts import atomic_json, directory


def _model(torch, features, hidden):
    return torch.nn.Sequential(torch.nn.Linear(features, hidden), torch.nn.ReLU(),
                               torch.nn.Linear(hidden, hidden), torch.nn.ReLU(), torch.nn.Linear(hidden, 2))


def _batch(torch, step, batch_size, features):
    generator = torch.Generator().manual_seed(1000 + step)
    x = torch.randn(batch_size, features, generator=generator)
    return x, (x.sum(dim=1) > 0).long()


def _measure(device, settings, *, rank=None, world_size=1):
    import torch
    import torch.distributed as dist
    torch.cuda.set_device(device)
    torch.manual_seed(42)
    model = _model(torch, settings["features"], settings["hidden"]).to(device)
    if rank is not None:
        model = torch.nn.parallel.DistributedDataParallel(model, device_ids=[device])
    optimizer = torch.optim.SGD(model.parameters(), lr=0.01)
    durations = []
    global_batch = settings["batch_size"] * 2
    for step in range(settings["warmup"] + settings["steps"]):
        x, y = _batch(torch, step, global_batch, settings["features"])
        if rank is not None:
            x, y = x.chunk(world_size)[rank], y.chunk(world_size)[rank]
            dist.barrier()
        torch.cuda.synchronize(device)
        started = time.perf_counter()
        x, y = x.to(device), y.to(device)
        optimizer.zero_grad(set_to_none=True)
        loss = torch.nn.functional.cross_entropy(model(x), y)
        loss.backward()
        optimizer.step()
        torch.cuda.synchronize(device)
        elapsed = time.perf_counter() - started
        if rank is not None:
            value = torch.tensor(elapsed, device=device)
            dist.all_reduce(value, op=dist.ReduceOp.MAX)
            elapsed = value.item()
        if step >= settings["warmup"]:
            durations.append(elapsed)
    base_model = model.module if rank is not None else model
    x, y = _batch(torch, 999999, 512, settings["features"])
    with torch.no_grad():
        validation_loss = torch.nn.functional.cross_entropy(base_model(x.to(device)), y.to(device)).item()
    windows = [durations[i::3] for i in range(3)]
    return {"device": torch.cuda.get_device_name(device), "global_batch_size": global_batch,
            "samples_per_second": global_batch * len(durations) / sum(durations),
            "median_step_seconds": statistics.median(durations), "validation_loss": validation_loss,
            "window_samples_per_second": [global_batch * len(w) / sum(w) for w in windows if w]}


def _distributed(rank, port, settings, result_path):
    import torch.distributed as dist
    dist.init_process_group("nccl", init_method=f"tcp://127.0.0.1:{port}", rank=rank, world_size=2)
    try:
        result = _measure(rank, settings, rank=rank, world_size=2)
        if rank == 0:
            atomic_json(Path(result_path), result)
    finally:
        dist.destroy_process_group()


def benchmark(output: Path | None = None, *, steps=30, warmup=5, batch_size=64, features=128, hidden=1024) -> dict:
    result = {"scope": "same_host_two_gpu_ddp", "cross_provider_qualified": False,
              "qualified": False, "minimum_speedup": 1.2, "status": "unavailable"}
    try:
        import torch
        import torch.distributed as dist
        if not torch.cuda.is_available() or torch.cuda.device_count() < 2:
            result["reason"] = "Two CUDA GPUs are required; no speedup was measured."
        elif not dist.is_nccl_available():
            result["reason"] = "This PyTorch build does not support NCCL; use a compatible Linux/WSL2 environment."
        else:
            if any(type(v) is not int or v <= 0 for v in (steps, batch_size, features, hidden)) or warmup < 0:
                raise ValueError("Benchmark sizes must be positive integers; warmup must be nonnegative")
            settings = {"steps": steps, "warmup": warmup, "batch_size": batch_size,
                        "features": features, "hidden": hidden}
            singles = [_measure(device, settings) for device in (0, 1)]
            best = max(singles, key=lambda item: item["samples_per_second"])
            with socket.socket() as handle:
                handle.bind(("127.0.0.1", 0))
                port = handle.getsockname()[1]
            with tempfile.TemporaryDirectory(prefix="multigpu-benchmark-") as temporary:
                result_path = str(Path(temporary) / "ddp.json")
                torch.multiprocessing.spawn(_distributed, args=(port, settings, result_path), nprocs=2, join=True)
                distributed = json.loads(Path(result_path).read_text(encoding="utf-8"))
            speedup = distributed["samples_per_second"] / best["samples_per_second"]
            loss_gap = abs(distributed["validation_loss"] - best["validation_loss"]) / max(best["validation_loss"], 1e-12)
            per_window = [a / b for a, b in zip(distributed["window_samples_per_second"], best["window_samples_per_second"])]
            qualified = len(per_window) == 3 and min(per_window) >= 1.2 and loss_gap <= 0.01
            result.update(status="measured", qualified=qualified, settings=settings, single_gpu=singles,
                          distributed=distributed, speedup=speedup, validation_loss_relative_gap=loss_gap,
                          window_speedups=per_window,
                          reason="All three windows gained at least 20% with comparable validation loss." if qualified else
                                 "The 20% throughput gain and 1% validation-loss tolerance were not both satisfied.",
                          limitations="Synthetic MLP, same host only. Rebenchmark the real recipe, network and job size before admission.")
    except ImportError:
        result["reason"] = "PyTorch is not installed; no speedup was measured."
    except Exception as exc:
        result.update(status="error", reason=str(exc))
    if output is not None:
        directory(Path(output).parent)
        atomic_json(Path(output), result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--steps", type=int, default=30)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=64)
    args = parser.parse_args(argv)
    result = benchmark(args.output, steps=args.steps, warmup=args.warmup, batch_size=args.batch_size)
    print(json.dumps(result, indent=2))
    return int(result["status"] == "error")


if __name__ == "__main__":
    raise SystemExit(main())
