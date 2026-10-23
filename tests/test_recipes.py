import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from multigpu.artifacts import load_checkpoint, verify_export
from multigpu.datasets import RESNET18_SHA256, inspect_dataset, weights_info
from multigpu.prediction import predict
from multigpu.recipes import prepare_image_weights
from multigpu.worker import TrainingContext, run


class RecipeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import torch
            cls.torch = torch
            cls.original_threads = torch.get_num_threads()
            torch.set_num_threads(1)
        except ImportError:
            cls.torch = None

    @classmethod
    def tearDownClass(cls):
        if cls.torch:
            cls.torch.set_num_threads(cls.original_threads)

    def setUp(self):
        self.repo = Path(__file__).resolve().parents[1]
        scratch = self.repo / ".test-runs"
        scratch.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def train(self, spec, output):
        with contextlib.redirect_stdout(io.StringIO()):
            return run(spec, output)

    def test_real_pytorch_cpu_and_portable_stdlib_prediction_match(self):
        if self.torch is None:
            self.skipTest("PyTorch is optional")
        spec = {"id": "mlp", "recipe": "pytorch", "recipe_version": 2, "steps": 12, "batch_size": 8,
                "learning_rate": .05, "seed": 42, "device": "cpu", "runtime_seconds": 60}
        output = self.root / "mlp"
        result = self.train(spec, output)
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["evaluation"]["split"], "validation")
        exported = output / "export"
        model = json.loads((exported / "model.json").read_text())
        net = self.torch.nn.Sequential(self.torch.nn.Linear(4, 16), self.torch.nn.Tanh(), self.torch.nn.Linear(16, 1))
        net.load_state_dict({key: self.torch.tensor(value) for key, value in model["model"].items()})
        raw = [.8, -.8, .2, .1]
        normalization = model["standardization"]
        row = [(value - mean) / std for value, mean, std in zip(raw, normalization["mean"], normalization["std"])]
        with self.torch.no_grad():
            expected = self.torch.sigmoid(net(self.torch.tensor([row])))[0, 0].item()
        predicted = predict(exported, {"features": raw})
        self.assertAlmostEqual(predicted["probability"], expected, places=6)
        process = subprocess.run([sys.executable, str(exported / "predict.py"), "--features", json.dumps(raw)],
                                 cwd=exported, capture_output=True, text=True, timeout=30)
        self.assertEqual(process.returncode, 0, process.stderr or process.stdout)
        self.assertAlmostEqual(json.loads(process.stdout)["probability"], expected, places=6)

    def test_image_weights_preparation_never_downloads_implicitly(self):
        with patch("multigpu.recipes.urllib.request.urlopen") as network:
            with self.assertRaises(ValueError):
                prepare_image_weights(self.root / "models")
            network.assert_not_called()
        bad = self.root / "bad.pth"
        bad.write_bytes(b"corrupt asset")
        self.assertFalse(weights_info({"weights_path": str(bad)})["ready"])

    def test_real_pretrained_image_cpu_exact_resume_and_export_inference(self):
        if self.torch is None:
            self.skipTest("PyTorch is optional")
        try:
            from PIL import Image
            from torchvision.models import resnet18
        except (ImportError, RuntimeError):
            self.skipTest("Compatible torchvision and Pillow are optional")
        cached = self.repo / ".test-runs" / "models" / "resnet18-f37072fd.pth"
        if not cached.is_file():
            self.skipTest("Prepare official test weights explicitly; unit tests do not download assets")
        self.assertEqual(weights_info({"weights_path": str(cached)})["sha256"], RESNET18_SHA256)
        images = self.root / "images"
        for label, name in enumerate(("blue", "red")):
            (images / name).mkdir(parents=True)
            for index in range(5):
                Image.new("RGB", (32, 32), (label * 180 + index * 3, index * 7, (1 - label) * 180)).save(images / name / f"{index}.png")
        spec = {"id": "image", "recipe": "image", "recipe_version": 2, "dataset_path": str(images),
                "weights_path": str(cached), "weights_sha256": RESNET18_SHA256,
                "epochs": 1, "batch_size": 2, "learning_rate": .001, "momentum": .9,
                "seed": 42, "device": "cpu", "runtime_seconds": 90}
        report = inspect_dataset(spec)
        self.assertEqual(report["errors"], [])
        spec["steps"] = report["dataset"]["total_steps"]
        baseline = self.root / "image-baseline"
        completed = self.train(spec, baseline)
        self.assertEqual(completed["status"], "completed", completed)
        self.assertEqual(completed["evaluation"]["samples"], 2)
        self.assertEqual(completed["step"], 4)
        interrupted = self.root / "image-interrupted"
        original = TrainingContext.report
        def stop_after_two(ctx, *args, **kwargs):
            original(ctx, *args, **kwargs)
            if ctx.step == 2:
                (ctx.output_dir / "STOP").touch()
        with patch.object(TrainingContext, "report", stop_after_two):
            paused = self.train(spec, interrupted)
        self.assertEqual((paused["status"], paused["step"]), ("paused", 2), paused)
        self.assertTrue((interrupted / "assets" / "base_weights.pth").is_file())
        (interrupted / "STOP").unlink()
        resumed = self.train({**spec, "resume_from": str(interrupted / "checkpoint.json")}, interrupted)
        self.assertEqual(resumed["status"], "completed", resumed)
        self.assertEqual(load_checkpoint(baseline / "checkpoint.json")["payload"],
                         load_checkpoint(interrupted / "checkpoint.json")["payload"])
        self.assertEqual(completed["evaluation"], resumed["evaluation"])
        exported = baseline / "export"
        verify_export(exported)
        prediction = predict(exported, {"image_path": str(images / "red" / "0.png")})
        self.assertIn(prediction["prediction"], ["blue", "red"])
        self.assertAlmostEqual(sum(prediction["probabilities"]), 1, places=5)
        process = subprocess.run([sys.executable, str(exported / "predict.py"), "--image", str(images / "red" / "0.png")],
                                 cwd=exported, capture_output=True, text=True, timeout=30)
        self.assertEqual(process.returncode, 0, process.stderr or process.stdout)
        self.assertEqual(json.loads(process.stdout)["prediction"], prediction["prediction"])


if __name__ == "__main__":
    unittest.main()
