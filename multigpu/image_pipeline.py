"""Bounded image loading and explicitly opted-in frozen-backbone features."""
from concurrent.futures import ThreadPoolExecutor
from collections import deque
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import pickle
import platform

from .artifacts import _check_path, atomic_json, canonical_json


def available_memory():
    try:
        if os.name == "nt":
            import ctypes
            class Status(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [(key, ctypes.c_ulonglong) for key in
                    ("total", "available", "page_total", "page_available", "virtual_total", "virtual_available", "extended")]
            status = Status()
            status.length = ctypes.sizeof(status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return status.available
        else:
            return os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, AttributeError):
        pass
    return None


class ImageReader:
    def __init__(self, root, entries, transform, torch, mode="sync"):
        self.root, self.entries, self.transform, self.torch = Path(root), entries, transform, torch
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="image-read") if mode == "prefetch" else None

    def image(self, index):
        from PIL import Image
        item = self.entries[index]
        path = self.root / item["path"]
        # Dataset inspection already validates paths; retain the content check on every read.
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != item["sha256"]:
            raise ValueError(f"Image changed during training: {item['path']}")
        with Image.open(io.BytesIO(content)) as image:
            return self.transform(image.convert("RGB"))

    def batch(self, indices):
        return self.torch.stack([self.image(index) for index in indices])

    def batches(self, batches):
        if self.pool is None:
            for batch in batches:
                yield batch, self.batch(batch)
            return
        iterator, pending = iter(batches), deque()
        try:
            while True:
                while len(pending) < 2:
                    batch = next(iterator, None)
                    if batch is None:
                        break
                    pending.append((batch, self.pool.submit(self.batch, batch)))
                if not pending:
                    return
                batch, future = pending.popleft()
                yield batch, future.result()
        finally:
            for _, future in pending:
                future.cancel()

    def close(self):
        if self.pool:
            self.pool.shutdown(wait=True, cancel_futures=True)


def cached_features(ctx, data, model, reader, weights_hash):
    """The cache is optional and disposable; model/checkpoint identity is separate."""
    torch = reader.torch
    import torchvision
    import PIL
    key_data = {"format": 1, "dataset": data["content_hash"], "weights": weights_hash,
                "transform": data["metadata"]["input_schema"], "torch": str(torch.__version__),
                "torchvision": str(torchvision.__version__), "pillow": PIL.__version__, "dtype": "float32",
                "device": ctx.device, "gpu": torch.cuda.get_device_name(ctx.device) if str(ctx.device).startswith("cuda") else None,
                "cpu": platform.processor(), "torch_threads": torch.get_num_threads(), "cuda_runtime": torch.version.cuda,
                "cudnn_version": torch.backends.cudnn.version(), "cudnn_tf32": torch.backends.cudnn.allow_tf32,
                "matmul_tf32": torch.backends.cuda.matmul.allow_tf32,
                "batch_size": ctx.spec["batch_size"], "matmul_precision": torch.get_float32_matmul_precision(),
                "cudnn_benchmark": torch.backends.cudnn.benchmark, "cudnn_deterministic": torch.backends.cudnn.deterministic}
    key = hashlib.sha256(canonical_json(key_data)).hexdigest()
    root = ctx.output_dir / "feature-cache"
    _check_path(root.absolute())
    root.mkdir(exist_ok=True)
    target, metadata = root / "features.pt", root / "manifest.json"
    _check_path(target)
    _check_path(metadata)
    count = len(data["entries"])
    expected = count * 512 * 4
    if expected > 128 * 1024 * 1024:
        raise ValueError("Frozen-feature cache exceeds its 128 MiB memory bound; disable feature caching")
    available = available_memory()
    if available is not None and available < expected * 3 + 128 * 1024 * 1024:
        raise MemoryError("Insufficient available RAM for bounded frozen-feature caching")
    from .staging import file_hash
    try:
        saved = json.loads(metadata.read_text(encoding="utf-8"))
        if (isinstance(saved, dict) and saved.get("key") == key and target.stat().st_size <= expected + 1024 * 1024
                and file_hash(target) == saved.get("sha256")):
            features = torch.load(target, map_location="cpu", weights_only=True)
            if (isinstance(features, torch.Tensor) and tuple(features.shape) == (count, 512)
                    and features.dtype == torch.float32 and torch.isfinite(features).all().item()):
                ctx.emit("cache", cache="frozen_features", state="hit", bytes=expected)
                return features
    except (OSError, ValueError, RuntimeError, EOFError, TypeError, pickle.UnpicklingError):
        pass
    if shutil.disk_usage(root).free < expected * 2 + 10 * 1024 * 1024:
        raise OSError("Insufficient disk space for frozen features and their verified replacement")
    ctx.emit("cache", cache="frozen_features", state="building", bytes=expected)
    backbone = torch.nn.Sequential(*list(model.children())[:-1]).eval()
    features = torch.empty((count, 512), dtype=torch.float32)
    batches = [list(range(start, min(start + ctx.spec["batch_size"], count))) for start in range(0, count, ctx.spec["batch_size"])]
    with torch.no_grad():
        for batch, inputs in reader.batches(batches):
            if ctx.should_stop():
                return None
            features[batch] = backbone(inputs.to(ctx.device)).flatten(1).cpu()
    if not torch.isfinite(features).all().item():
        raise ValueError("Feature extraction produced non-finite values")
    descriptor, temporary = tempfile.mkstemp(prefix=".features-", suffix=".pt", dir=root)
    os.close(descriptor)
    try:
        torch.save(features, temporary)
        digest = file_hash(temporary)
        os.replace(temporary, target)
        atomic_json(metadata, {"key": key, "identity": key_data, "sha256": digest, "bytes": expected})
    finally:
        Path(temporary).unlink(missing_ok=True)
    ctx.emit("cache", cache="frozen_features", state="ready", bytes=expected)
    return features
