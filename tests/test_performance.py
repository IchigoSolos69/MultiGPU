"""Performance changes retain identity, exact progress and recovery contracts."""
import contextlib
import io
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

from multigpu.artifacts import load_checkpoint, verify_export
from multigpu.datasets import load_dataset, fingerprint_for_spec
from multigpu.performance import recommendations
from multigpu.planner import validate_spec
from multigpu.service import Service
from multigpu.worker import TrainingContext, run
from multigpu import connectors


class PerformanceTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / ".test-runs"
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.root = Path(self.temp.name)
        with patch("multigpu.service.hardware_info", return_value={"cpu_count": 4, "gpus": [], "cuda_available": False}):
            self.service = Service(self.root / "workspace")
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.service.store.close)
        self.addCleanup(self.service.shutdown)
        self.jid = "a" * 32
        self.folder = self.service.jobs_root / self.jid
        self.folder.mkdir()
        self.events = self.folder / "events.jsonl"

    def line(self, step, **extra):
        return json.dumps({"type": "metric", "job_id": self.jid, "step": step, "loss": 1 / step,
                           "timestamp": str(step), **extra}) + "\n"

    def train(self, spec, folder):
        with contextlib.redirect_stdout(io.StringIO()):
            return run(spec, folder)

    def test_event_cursor_waits_for_complete_lines_and_does_not_parse_unchanged_tail(self):
        self.events.write_text(self.line(1) + self.line(2)[:-1], encoding="utf-8")
        self.assertEqual([e["step"] for e in self.service.read_events(self.jid)], [1])
        with patch("multigpu.service.json.loads", wraps=json.loads) as parse:
            self.service.read_events(self.jid)
            parse.assert_not_called()
        with self.events.open("a", encoding="utf-8") as stream:
            stream.write("\n" + self.line(3))
        self.assertEqual([e["step"] for e in self.service.read_events(self.jid)], [1, 2, 3])

    def test_incremental_ingestion_handles_truncation_replacement_and_absence(self):
        self.events.write_text(self.line(1) + self.line(2), encoding="utf-8")
        self.assertEqual(len(self.service.read_events(self.jid, False, True)), 2)
        self.assertEqual(self.service.read_events(self.jid, False, True), [])
        self.events.write_text(self.line(5, detail="replacement" * 50), encoding="utf-8")
        self.assertEqual([e["step"] for e in self.service.read_events(self.jid, False, True)], [5])
        self.events.unlink()
        self.assertEqual(self.service.read_events(self.jid, False, True), [])
        self.events.write_text(self.line(6), encoding="utf-8")
        self.assertEqual([e["step"] for e in self.service.read_events(self.jid, False, True)], [6])

    def test_restart_reconstructs_event_history(self):
        self.events.write_text(self.line(1), encoding="utf-8")
        self.service.read_events(self.jid)
        self.service._event_readers.clear()
        self.assertEqual(self.service.read_events(self.jid)[0]["step"], 1)

    def test_sampled_emission_keeps_exact_internal_progress_and_nonfinite_checks(self):
        ctx = TrainingContext({"id": self.jid, "recipe": "starter", "steps": 100}, self.folder)
        with contextlib.redirect_stdout(io.StringIO()):
            for step in range(99):
                ctx.report(1 / (step + 1))
            self.assertEqual(ctx.step, 99)
            with self.assertRaisesRegex(ValueError, "non-finite"):
                ctx.report(float("nan"))
            self.assertEqual(ctx.step, 99)
            ctx.emit("paused")
        events = self.service.read_events(self.jid)
        self.assertEqual(sum(e["type"] == "metric" for e in events), 1)
        self.assertEqual(events[-1]["step"], 99)
        self.assertEqual(events[-1]["type"], "paused")

    def test_worker_performance_has_unknown_gpu_and_exact_target(self):
        result = self.train({"id": self.jid, "recipe": "starter", "steps": 20}, self.folder)
        self.assertEqual(result["status"], "completed", result)
        p = result["performance"]
        self.assertEqual((p["completed_steps"], p["recovered_steps"], p["trained_samples"]), (20, 0, 640))
        self.assertIsNone(p["memory"]["gpu_peak_allocated_bytes"])
        self.assertIsNone(p["transferred_bytes"])
        self.assertIsNone(p["stages_seconds"]["upload"])
        self.assertGreater(p["stages_seconds"]["checkpointing"], 0)
        self.assertGreater(p["samples_per_second"], 0)
        self.assertEqual(load_checkpoint(self.folder / "checkpoint.json")["total_steps"], 20)
        verify_export(self.folder / "export")

    def test_measured_checkpoint_reserve_pauses_early_without_changing_target(self):
        ctx = TrainingContext({"recipe": "starter", "steps": 20, "runtime_seconds": 10}, self.folder)
        ctx._checkpoint_reserve = 4
        with patch("multigpu.worker.time.monotonic", return_value=ctx._started + 3):
            self.assertTrue(ctx.should_stop())
        self.assertEqual(ctx.spec["steps"], 20)

    def test_optional_performance_sidecar_failure_does_not_invalidate_model(self):
        from multigpu.artifacts import atomic_json
        def write(path, data):
            if path.name == "performance.json":
                raise OSError("Full optional metadata destination")
            return atomic_json(path, data)
        with patch("multigpu.worker.atomic_json", side_effect=write):
            result = self.train({"id": self.jid, "recipe": "starter", "steps": 3}, self.folder)
        self.assertEqual(result["status"], "completed", result)
        self.assertIn("persistence_warning", result["performance"])
        verify_export(self.folder / "export")

    def test_runtime_snapshot_excludes_static_research_and_catalog(self):
        snapshot = self.service.runtime_snapshot()
        self.assertNotIn("research_providers", snapshot)
        self.assertNotIn("research_documents", snapshot)
        self.assertNotIn("providers", snapshot)
        self.assertIn("connections", snapshot)
        self.assertLess(len(json.dumps(snapshot)), len(json.dumps(self.service.bootstrap())) / 5)

    def test_detail_omits_unchanged_histories_and_old_attempt_does_not_replay(self):
        with patch.object(self.service, "_start"):
            job = self.service.create({"recipe": "starter", "providers": ["local"], "steps": 10})
        folder = self.service.jobs_root / job["id"]
        job["spec"]["attempt_id"] = "current"
        self.service.save_job(job)
        events = [{"type": "metric", "job_id": job["id"], "attempt_id": "old", "step": 8, "loss": .1},
                  {"type": "metric", "job_id": job["id"], "attempt_id": "current", "step": 2, "loss": .5}]
        (folder / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
        self.assertEqual(self.service._ingest(job)["step"], 2)
        first = self.service.job(job["id"], True)
        second = self.service.job(job["id"], True, first["observations_revision"])
        self.assertTrue(second["observations_unchanged"])
        self.assertNotIn("metrics", second)
        self.assertNotIn("events", second)
        (folder / "worker.log").write_text("new log", encoding="utf-8")
        self.assertFalse(self.service.job(job["id"], True, first["observations_revision"])["observations_unchanged"])

    def test_acceleration_options_are_opt_in_validated_and_identity_bound(self):
        defaults = validate_spec({"recipe": "image"}, self.root)
        self.assertFalse(defaults.get("image_feature_cache", False))
        self.assertEqual(defaults.get("image_loader", "sync"), "sync")
        for raw in ({"image_feature_cache": True}, {"image_loader": "unsafe"}, {"recipe": "image", "image_feature_cache": "yes"}):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                validate_spec(raw, self.root)

    def test_unknown_recommendations_never_reorder_or_claim_measurements(self):
        spec = validate_spec({"providers": ["kaggle", "local"]}, self.root)
        choices = [{"provider_id": pid, "eligible": True, "resolved_resource": {"device": "cpu"}} for pid in spec["providers"]]
        result = recommendations(spec, choices, [], "fingerprint", {})
        self.assertIsNone(result["recommended_provider_id"])
        self.assertTrue(all(item["estimated_seconds"] is None for item in result["estimates"]))
        self.assertEqual(spec["providers"], ["kaggle", "local"])

    def test_recommendation_requires_same_verified_inputs_and_hardware(self):
        from multigpu.performance import Performance
        spec = validate_spec({"providers": ["local"]}, self.root)
        p = Performance(spec).snapshot(10, "cpu")
        p.update(completed_steps=10, stages_seconds={"training": 1, "data_loading": .2, "checkpointing": .1})
        job = {"id": "old", "status": "completed", "provider_id": "local", "fingerprint": "same", "spec": spec,
               "export_status": "verified", "performance": p}
        choices = [{"provider_id": "local", "eligible": True, "resolved_resource": {"device": "cpu"}}]
        result = recommendations(spec, choices, [job], "same", {})
        self.assertAlmostEqual(result["estimates"][0]["estimated_seconds"], 50.3)
        self.assertIsNone(recommendations(spec, choices, [job], "different", {})["recommended_provider_id"])
        p["hardware"]["cpu"] = "different hardware"
        self.assertIsNone(recommendations(spec, choices, [job], "same", {})["recommended_provider_id"])

    def test_allowance_estimates_keep_unknown_separate_from_zero(self):
        spec = validate_spec({"providers": ["kaggle"]}, self.root)
        choices = [{"provider_id": "kaggle", "eligible": True, "resolved_resource": {"device": "cpu"}}]
        result = recommendations(spec, choices, [], "same", {}, {"kaggle": {"quota": {"remaining_hours": 0}, "checked_at": "dated"}})
        self.assertEqual(result["estimates"][0]["remaining_allowance_seconds"], 0)
        self.assertIsNone(recommendations(spec, choices, [], "same", {})["estimates"][0]["remaining_allowance_seconds"])

    def test_gpu_recommendation_does_not_mix_identically_named_device_indices(self):
        from multigpu.performance import Performance
        spec = validate_spec({"recipe": "pytorch", "device": "cuda"}, self.root)
        p = Performance(spec).snapshot(10, "cuda:1")
        p.update(completed_steps=10, runtime={**p["runtime"], "torch": "fixture"}, stages_seconds={"training": 1})
        p["hardware"]["gpu"] = "Identical GPU"
        job = {"id": "old", "status": "completed", "provider_id": "local", "fingerprint": "same", "spec": spec,
               "export_status": "verified", "performance": p}
        choices = [{"provider_id": "local", "eligible": True, "resolved_resource": {"device": "cuda", "gpu_index": 0}}]
        hardware = {"torch_version": "fixture", "gpus": [{"name": "Identical GPU"}, {"name": "Identical GPU"}]}
        self.assertIsNone(recommendations(spec, choices, [job], "same", hardware)["recommended_provider_id"])
        p["device"] = "cuda:0"
        self.assertEqual(recommendations(spec, choices, [job], "same", hardware)["recommended_provider_id"], "local")

    def test_migration_advice_never_recommends_an_ineligible_current_resource(self):
        from multigpu.performance import continuation_advice
        job = {"provider_id": "local", "fingerprint": "same", "performance": {
            "source": "worker", "completed_steps": 95, "stages_seconds": {"training": 95}}}
        old = {"provider_id": "colab", "status": "completed", "export_status": "verified", "fingerprint": "same", "spec": {"resolved_resource": "resource"},
               "performance": {"source": "worker", "stages_seconds": {"preparation": 10}, "controller_stages_seconds": {}}}
        plan = {"selected_provider": "colab", "resolved_resource": "resource"}
        checkpoint = {"step": 95, "total_steps": 100}
        self.assertTrue(continuation_advice(job, checkpoint, plan, [old], True)["stay_recommended"])
        self.assertFalse(continuation_advice(job, checkpoint, plan, [old], False)["stay_recommended"])
        self.assertIsNone(continuation_advice(job, checkpoint, plan, [], True)["estimated_migration_overhead_seconds"])

    def test_prepared_notebook_reused_but_corruption_and_changed_inputs_invalidate(self):
        path = self.folder / "spec.json"
        path.write_text(json.dumps({"id": self.jid, "recipe": "starter", "steps": 2}), encoding="utf-8")
        first = connectors.build_notebook("colab", {}, path, self.folder)
        with patch.object(connectors, "_stage_for_provider", side_effect=AssertionError("unnecessary staging")):
            self.assertEqual(connectors.build_notebook("colab", {}, path, self.folder), first)
        first.write_text("corrupted", encoding="utf-8")
        self.assertIn("nbformat", json.loads(connectors.build_notebook("colab", {}, path, self.folder).read_text()))
        path.write_text(json.dumps({"id": self.jid, "recipe": "starter", "steps": 3}), encoding="utf-8")
        with patch.object(connectors, "_stage_for_provider", wraps=connectors._stage_for_provider) as stage:
            connectors.build_notebook("colab", {}, path, self.folder)
            stage.assert_called_once()

    def test_cache_cleanup_keeps_model_weights_and_checkpoint(self):
        cache = self.folder / "feature-cache"
        cache.mkdir()
        (cache / "features.pt").write_bytes(b"derived")
        (cache / "manifest.json").write_text("{}", encoding="utf-8")
        for name in ("checkpoint.json", "model.json", "base_weights.pth"):
            (self.folder / name).write_bytes(b"retained")
        self.service.clean_cache()
        self.assertFalse((cache / "features.pt").exists())
        self.assertTrue(all((self.folder / name).read_bytes() == b"retained" for name in ("checkpoint.json", "model.json", "base_weights.pth")))


class TorchPerformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import torch
        except ImportError:
            raise unittest.SkipTest("PyTorch is optional")
        cls.torch = torch
        cls.threads = torch.get_num_threads()
        torch.set_num_threads(1)

    @classmethod
    def tearDownClass(cls):
        cls.torch.set_num_threads(cls.threads)

    def setUp(self):
        self.repo = Path(__file__).resolve().parents[1]
        self.temp = tempfile.TemporaryDirectory(dir=self.repo / ".test-runs")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def train(self, spec, folder):
        with contextlib.redirect_stdout(io.StringIO()):
            return run(spec, folder)

    def test_cuda_benchmark_unavailable_never_falls_back_or_creates_work(self):
        from multigpu.performance_benchmark import benchmark
        with patch.object(self.torch.cuda, "is_available", return_value=False):
            with self.assertRaisesRegex(ValueError, "no CPU fallback or cloud job"):
                benchmark(self.root, device="cuda")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_tensorized_minibatches_equal_original_list_sampler_and_exact_resume(self):
        torch = self.torch
        spec = {"id": "tabular", "recipe": "pytorch", "recipe_version": 2, "steps": 40, "batch_size": 8,
                "learning_rate": .05, "seed": 42, "device": "cpu", "runtime_seconds": 60}
        data = load_dataset(spec)
        torch.manual_seed(42)
        model = torch.nn.Sequential(torch.nn.Linear(4, 16), torch.nn.Tanh(), torch.nn.Linear(16, 1))
        optimizer = torch.optim.SGD(model.parameters(), lr=.05)
        rng = random.Random(42)
        for _ in range(40):
            indices = [data["train_indices"][rng.randrange(len(data["train_indices"]))] for _ in range(8)]
            features = torch.tensor([data["features"][i] for i in indices], dtype=torch.float32)
            labels = torch.tensor([data["labels"][i] for i in indices], dtype=torch.float32)
            optimizer.zero_grad(set_to_none=True)
            loss = torch.nn.functional.binary_cross_entropy_with_logits(model(features).squeeze(-1), labels)
            loss.backward()
            optimizer.step()
        expected = {key: value.tolist() for key, value in model.state_dict().items()}
        full = self.root / "full"
        self.assertEqual(self.train(spec, full)["status"], "completed")
        self.assertEqual(load_checkpoint(full / "checkpoint.json")["payload"]["model"], expected)
        paused = self.root / "paused"
        original = TrainingContext.report
        def stop(ctx, *args, **kwargs):
            original(ctx, *args, **kwargs)
            if ctx.step == 13:
                (ctx.output_dir / "STOP").touch()
        with patch.object(TrainingContext, "report", stop):
            self.assertEqual(self.train(spec, paused)["status"], "paused")
        (paused / "STOP").unlink()
        result = self.train({**spec, "resume_from": str(paused / "checkpoint.json")}, paused)
        self.assertEqual(result["performance"]["recovered_steps"], 13)
        self.assertEqual(load_checkpoint(full / "checkpoint.json")["payload"], load_checkpoint(paused / "checkpoint.json")["payload"])

    def image_spec(self):
        from PIL import Image
        weights = self.repo / ".test-runs/models/resnet18-f37072fd.pth"
        if not weights.is_file():
            self.skipTest("Official test weights must be prepared explicitly")
        for label in range(2):
            folder = self.root / "images" / str(label)
            folder.mkdir(parents=True, exist_ok=True)
            for i in range(5):
                Image.new("RGB", (32, 32), (label * 170 + i, 70, i * 5)).save(folder / f"{i}.png")
        return {"id": "image", "recipe": "image", "recipe_version": 2, "dataset_path": str(self.root / "images"),
                "weights_path": str(weights), "epochs": 2, "batch_size": 2, "device": "cpu", "runtime_seconds": 120}

    def test_prefetch_preserves_image_model_and_no_threads_survive(self):
        import threading
        spec = self.image_spec()
        self.assertEqual(self.train(spec, self.root / "sync")["status"], "completed")
        self.assertEqual(self.train({**spec, "image_loader": "prefetch"}, self.root / "prefetch")["status"], "completed")
        self.assertEqual(load_checkpoint(self.root / "sync/checkpoint.json")["payload"], load_checkpoint(self.root / "prefetch/checkpoint.json")["payload"])
        self.assertFalse(any(t.name.startswith("image-read") for t in threading.enumerate()))

    def test_feature_cache_equivalence_exact_resume_corruption_and_mode_binding(self):
        spec = self.image_spec()
        regular = self.train(spec, self.root / "regular")
        cached_spec = {**spec, "image_feature_cache": True}
        cached = self.train(cached_spec, self.root / "cached")
        self.assertEqual(cached["status"], "completed", cached)
        self.assertEqual(regular["step"], cached["step"])
        self.assertAlmostEqual(regular["evaluation"]["loss"], cached["evaluation"]["loss"], places=5)
        self.assertNotEqual(fingerprint_for_spec(spec), fingerprint_for_spec(cached_spec))
        folder = self.root / "resume"
        original = TrainingContext.report
        def stop(ctx, *args, **kwargs):
            original(ctx, *args, **kwargs)
            if ctx.step == 2:
                (ctx.output_dir / "STOP").touch()
        with patch.object(TrainingContext, "report", stop):
            self.assertEqual(self.train(cached_spec, folder)["status"], "paused")
        (folder / "STOP").unlink()
        resumed = self.train({**cached_spec, "resume_from": str(folder / "checkpoint.json")}, folder)
        self.assertEqual(resumed["status"], "completed", resumed)
        self.assertEqual(load_checkpoint(folder / "checkpoint.json")["payload"], load_checkpoint(self.root / "cached/checkpoint.json")["payload"])
        self.assertIn('"state":"hit"', (folder / "events.jsonl").read_text())
        (folder / "feature-cache/features.pt").write_bytes(b"corrupt")
        result = self.train(cached_spec, folder)
        self.assertEqual(result["status"], "completed", result)
        self.assertGreater((folder / "feature-cache/features.pt").stat().st_size, 1000)

    def test_feature_cache_low_memory_low_disk_and_interrupted_preparation(self):
        spec = {**self.image_spec(), "image_feature_cache": True}
        with patch("multigpu.image_pipeline.available_memory", return_value=0):
            result = self.train(spec, self.root / "memory")
            self.assertEqual(result["status"], "error")
            self.assertIn("RAM", result["error"])
        with patch("multigpu.image_pipeline.shutil.disk_usage") as usage:
            usage.return_value.free = 0
            result = self.train(spec, self.root / "disk")
            self.assertEqual(result["status"], "error")
        paused = self.train({**spec, "runtime_seconds": 0}, self.root / "interrupted")
        self.assertEqual((paused["status"], paused["step"]), ("paused", 0))
        self.assertEqual(load_checkpoint(self.root / "interrupted/checkpoint.json")["total_steps"], 8)
        self.assertFalse((self.root / "interrupted/feature-cache/manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
