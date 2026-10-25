"""Explicit local benchmarks; never provisions a provider or downloads weights."""
import argparse
import contextlib
import io
import json
from pathlib import Path
import statistics
import time
import uuid

from .artifacts import atomic_json, directory
from .worker import run


def benchmark(root, *, device="cpu", repeats=3, threads=1, images=None, weights=None):
    if device not in {"cpu", "cuda"} or type(repeats) is not int or not 1 <= repeats <= 10 or not 1 <= threads <= 32:
        raise ValueError("Choose cpu/cuda, 1–10 repeats and 1–32 CPU threads")
    imported = time.perf_counter()
    import torch
    import_seconds = time.perf_counter() - imported
    if device == "cuda" and not torch.cuda.is_available():
        raise ValueError("CUDA was requested but is unavailable; no CPU fallback or cloud job was launched")
    if bool(images) != bool(weights):
        raise ValueError("Image benchmarking requires both --images and explicitly prepared --weights")
    root = directory(Path(root).resolve() / ("benchmark-" + uuid.uuid4().hex))
    previous = torch.get_num_threads()
    torch.set_num_threads(threads)
    results = []
    variants = [("starter", {})] if device == "cpu" else []
    variants.append(("pytorch", {}))
    if images:
        variants.extend(("image", options) for options in ({}, {"image_loader": "prefetch"}, {"image_feature_cache": True}))
    try:
        for variant, options in variants:
            for repeat in range(repeats):
                name = variant + ("-prefetch" if options.get("image_loader") else "-features" if options.get("image_feature_cache") else "")
                spec = {"id": name, "recipe": variant, "recipe_version": 2, "steps": 1000, "batch_size": 16,
                        "seed": 42, "device": device, "runtime_seconds": 600, **options}
                if variant == "image":
                    spec.update(dataset_path=str(Path(images).resolve()), weights_path=str(Path(weights).resolve()), epochs=3, batch_size=4)
                started = time.perf_counter()
                with contextlib.redirect_stdout(io.StringIO()):
                    result = run(spec, root / f"{name}-{repeat}")
                elapsed = time.perf_counter() - started
                if result["status"] != "completed":
                    raise RuntimeError("Benchmark did not complete: " + json.dumps(result))
                results.append({"variant": name, "repeat": repeat, "cache_condition": "first_recipe_run" if repeat == 0 else "subsequent_recipe_run",
                                "seconds": elapsed, "steps": result["step"], "loss": result["loss"], "evaluation": result["evaluation"],
                                "performance": result["performance"], "events_bytes": (root / f"{name}-{repeat}" / "events.jsonl").stat().st_size})
        medians = {name: statistics.median(r["seconds"] for r in results if r["variant"] == name and (repeats == 1 or r["repeat"] > 0))
                   for name in {r["variant"] for r in results}}
        report = {"scope": "explicit_local_recipe_benchmark", "device": device, "threads": threads, "torch": str(torch.__version__),
                  "runtime_import_seconds": import_seconds, "results": results, "subsequent_median_seconds": medians,
                  "cloud_jobs_launched": False, "workspace": str(root),
                  "limitations": "First recipe run is not a cold OS-cache measurement. Process import time is recorded separately. Feature-cache runs include extraction; no user training targets are changed."}
        atomic_json(root / "report.json", report)
        return report
    finally:
        torch.set_num_threads(previous)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--device", choices=("cpu", "cuda"), required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--images", type=Path)
    parser.add_argument("--weights", type=Path)
    args = parser.parse_args(argv)
    try:
        report = benchmark(args.workspace, device=args.device, repeats=args.repeats, threads=args.threads, images=args.images, weights=args.weights)
    except ImportError:
        parser.error("Prepare the selected CPU/CUDA training environment first; no packages were installed automatically")
    except (ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    print(json.dumps({"report": str(Path(report["workspace"]) / "report.json"), "medians": report["subsequent_median_seconds"]}))


if __name__ == "__main__":
    main()
