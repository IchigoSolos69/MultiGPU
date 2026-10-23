"""Failure and recovery simulations: no provider requests or billed execution."""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from multigpu.artifacts import (IntegrityError, export_artifacts, load_checkpoint,
                               validate_checkpoint_payload, verify_export, write_checkpoint)
from multigpu.datasets import load_dataset
from multigpu.service import Service
from multigpu.worker import TrainingContext, WorkerLock, run


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / ".test-runs"
        scratch.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="lifecycle-", dir=scratch)
        self.root = Path(self.temporary.name)
        self.hardware = patch("multigpu.service.hardware_info", return_value={"torch_available": False, "cuda_available": False, "gpus": []})
        self.hardware.start()
        self.addCleanup(self.hardware.stop)
        self.service = Service(self.root / "state")

    def tearDown(self):
        self.service.shutdown()
        self.service.store.close()
        self.temporary.cleanup()

    def guided(self, **changes):
        return self.service.create({"providers": ["colab"], "steps": 8, **changes})

    def remote(self):
        job = self.guided()
        job.update(provider_id="kaggle", status="queued", execution_mode="automatic", attempted_providers=["kaggle"])
        job["spec"]["providers"] = ["kaggle"]
        self.service.save_job(job)
        self.service.store.put("connection", "kaggle", {"provider_id": "kaggle", "ready": True,
                              "config": {"username": "test"}, "quota": {"remaining_hours": 1, "source": "test", "observed_at": "test"}})
        return job

    def train(self, job, folder=None, **changes):
        folder = folder or self.service.jobs_root / job["id"]
        with contextlib.redirect_stdout(io.StringIO()):
            result = run({**job["spec"], **changes}, folder)
        self.assertNotEqual(result["status"], "error", result)
        return folder

    def test_submission_timeout_persists_intent_and_blocks_duplicate(self):
        job = self.remote()
        intent = {"provider": "kaggle", "attempt_id": "durable-attempt", "kernel": "test/existing"}
        with patch("multigpu.connectors.prepare", return_value=intent), patch("multigpu.connectors.submit", side_effect=TimeoutError("response lost")) as submit:
            self.service._run(job["id"])
            saved = self.service.job(job["id"])
            self.assertEqual(saved["status"], "interrupted")
            self.assertEqual(saved["submission_status"], "unknown")
            self.assertEqual(saved["submission_intent"], intent)
            with self.assertRaisesRegex(ValueError, "Reconcile"):
                self.service.resume(job["id"])
            submit.assert_called_once()

    def test_poll_retry_keeps_same_execution_and_never_resubmits(self):
        job = self.remote()
        def submit(pid, config, spec_path, output, intent=None):
            spec = json.loads(spec_path.read_text())
            self.train({"id": job["id"], "spec": spec}, output, runtime_seconds=0)
            return {"provider": pid, "kernel": "test/existing"}
        with patch("multigpu.connectors.prepare", return_value={"provider": "kaggle"}), \
             patch("multigpu.connectors.submit", side_effect=submit) as launch, \
             patch("multigpu.connectors.poll", side_effect=[ConnectionError("temporary"), {"state": "completed"}]) as poll, \
             patch("multigpu.connectors.collect"), patch.object(self.service.closing, "wait", return_value=False):
            self.service._run(job["id"])
        saved = self.service.job(job["id"])
        self.assertEqual(saved["status"], "paused", saved)
        self.assertEqual(saved["connection_status"], "connected")
        self.assertEqual(saved["allowance_observation"]["source"], "test")
        self.assertTrue(saved["allowance_observation"]["snapshot"])
        self.assertEqual(launch.call_count, 1)
        self.assertEqual(poll.call_count, 2)

    def test_restart_reattaches_remote_handle_without_submission(self):
        job = self.remote()
        job["spec"]["attempt_id"] = "existing-attempt"
        job.update(status="running", handle={"provider": "kaggle", "kernel": "test/existing"}, submission_status="confirmed")
        self.service.save_job(job)
        self.service.shutdown()
        self.service.store.close()
        def collect(pid, config, handle, output):
            self.train(job, output)
        with patch("multigpu.connectors.reconcile", return_value={"state": "completed", "handle": job["handle"]}) as reconnect, \
             patch("multigpu.connectors.collect", side_effect=collect), patch("multigpu.connectors.submit") as submit:
            self.service = Service(self.root / "state")
            self.service.threads[job["id"]].join(10)
            self.assertFalse(self.service.threads[job["id"]].is_alive())
        saved = self.service.job(job["id"])
        self.assertEqual(saved["status"], "completed", saved)
        self.assertEqual(saved["export_status"], "verified")
        reconnect.assert_called_once()
        submit.assert_not_called()

    def test_local_orphan_lock_blocks_resume_import_and_recovery(self):
        job = self.guided()
        path = self.train(job, runtime_seconds=0)
        state = load_checkpoint(path / "checkpoint.json")
        write_checkpoint(path, state)
        job.update(provider_id="local", status="interrupted")
        self.service.save_job(job)
        owner = WorkerLock(path, job["spec"])
        try:
            for action in (lambda: self.service.resume(job["id"]),
                           lambda: self.service.recover(job["id"], {}),
                           lambda: self.service.import_output(job["id"], {"path": str(path)})):
                with self.assertRaisesRegex(ValueError, "previous worker"):
                    action()
        finally:
            owner.close()

    def test_restart_watches_existing_local_worker_without_launch(self):
        job = self.guided()
        job["spec"]["attempt_id"] = "existing-local"
        path = self.train(job)
        job.update(provider_id="local", status="running")
        self.service.save_job(job)
        owner = WorkerLock(path, job["spec"])
        self.service.shutdown()
        self.service.store.close()
        try:
            with patch("multigpu.service.subprocess.Popen") as launch:
                self.service = Service(self.root / "state")
                self.assertEqual(self.service.job(job["id"])["status"], "running")
                owner.close()
                self.service.threads[job["id"]].join(10)
                launch.assert_not_called()
            self.assertEqual(self.service.job(job["id"])["export_status"], "verified")
        finally:
            owner.close()

    def test_collection_failure_is_recoverable_without_claiming_cleanup(self):
        job = self.remote()
        job.update(status="running", handle={"provider": "kaggle"}, submission_status="confirmed")
        self.service.save_job(job)
        with patch("multigpu.connectors.collect", side_effect=ConnectionError("download interrupted")):
            saved = self.service._finish_remote(job["id"], "completed")
        self.assertEqual(saved["status"], "interrupted")
        self.assertEqual(saved["collection_status"], "failed")
        self.assertTrue(saved["remote_terminal"])
        self.assertEqual(saved["cleanup_status"], "retained")

    def test_cleanup_failure_retains_verified_results_and_handle(self):
        job = self.remote()
        job.update(provider_id="lightning", status="running", handle={"provider": "lightning", "studio": "owned"}, submission_status="confirmed")
        self.train(job)
        self.service.save_job(job)
        def collect(pid, config, handle, output):
            handle["cleanup_status"] = "failed"
            raise RuntimeError("Studio stop was not acknowledged")
        with patch("multigpu.connectors.collect", side_effect=collect):
            saved = self.service._finish_remote(job["id"], "completed")
        self.assertEqual(saved["status"], "completed", saved)
        self.assertEqual(saved["collection_status"], "verified")
        self.assertEqual(saved["cleanup_status"], "failed")
        self.assertEqual(saved["handle"]["studio"], "owned")
        self.assertEqual(saved["export_status"], "verified")
        self.assertIn("cleanup", saved["message"])
        self.assertNotIn("resume", saved["allowed_actions"])

    def test_malformed_payload_cannot_replace_or_resume_last_good_state(self):
        job = self.guided()
        current = self.train(job, runtime_seconds=0)
        good = (current / "checkpoint.json").read_bytes()
        state = load_checkpoint(current / "checkpoint.json")
        state["payload"]["weights"].pop()
        remote = self.root / "bad-remote"
        write_checkpoint(remote, state)
        with self.assertRaisesRegex(IntegrityError, "dimensions"):
            self.service.import_output(job["id"], {"path": str(remote)})
        self.assertEqual((current / "checkpoint.json").read_bytes(), good)
        shutil.copyfile(remote / "checkpoint.json", current / "checkpoint.previous.json")
        with self.assertRaises(IntegrityError):
            self.service.recover(job["id"], {})
        shutil.copyfile(remote / "checkpoint.json", current / "checkpoint.json")
        job.update(provider_id="local", status="paused")
        self.service.save_job(job)
        with self.assertRaises(IntegrityError):
            self.service.resume(job["id"])

    def test_cpu_only_mlp_and_image_schema_validation(self):
        rng = json.loads(json.dumps(random.Random(42).getstate()))
        common = {"recipe_version": 2, "step": 0, "total_steps": 5, "loss": None, "elapsed_seconds": 0}
        payload = {"model": {"0.weight": [[0.] * 4 for _ in range(16)], "0.bias": [0.] * 16,
                             "2.weight": [[0.] * 16], "2.bias": [0.]},
                   "architecture": {"name": "binary-mlp", "features": 4, "hidden": 16},
                   "optimizer": {"state": {}, "param_groups": [{"params": [0, 1, 2, 3], "lr": .1}]},
                   "rng_state": rng, "torch_rng": [0] * 5056, "cuda_rng": [],
                   "split": {"train_indices": list(range(8)), "validation_indices": [8, 9]},
                   "standardization": {"mean": [0.] * 4, "std": [1.] * 4}}
        state = {**common, "recipe": "pytorch", "payload": payload}
        validate_checkpoint_payload(state, {"recipe": "pytorch", "recipe_version": 2, "steps": 5, "learning_rate": .1})
        malformed = copy.deepcopy(state)
        malformed["payload"]["model"]["0.weight"][0].pop()
        with self.assertRaises(IntegrityError):
            validate_checkpoint_payload(malformed)
        malformed = copy.deepcopy(state)
        malformed["payload"]["architecture"] = []
        with self.assertRaises(IntegrityError):
            validate_checkpoint_payload(malformed)
        from multigpu.datasets import RESNET18_SHA256
        image = {**common, "recipe": "image", "payload": {key: value for key, value in payload.items() if key in ("rng_state", "torch_rng", "cuda_rng", "split")}}
        image["payload"].update(head={"weight": [[0.] * 512 for _ in range(2)], "bias": [0., 0.]},
                                optimizer={"state": {}, "param_groups": [{"params": [0, 1], "lr": .001, "momentum": .9}]},
                                epoch=0, cursor=0, order=[], weights_sha256=RESNET18_SHA256, weights_asset="assets/base_weights.pth")
        validate_checkpoint_payload(image, {"recipe": "image", "recipe_version": 2, "steps": 5, "epochs": 5, "batch_size": 16, "learning_rate": .001, "momentum": .9})
        image["payload"]["head"]["weight"][0].pop()
        with self.assertRaises(IntegrityError):
            validate_checkpoint_payload(image)
        validate_checkpoint_payload({**common, "recipe": "custom", "payload": {"anything": [1, {"weights": "user-owned"}]}})

    def test_guided_partial_import_bundle_actually_continues_checkpoint(self):
        job = self.guided()
        remote = self.train(job, self.root / "remote", runtime_seconds=0)
        state = load_checkpoint(remote / "checkpoint.json")
        state["step"] = 3
        write_checkpoint(remote, state)
        self.service.import_output(job["id"], {"path": str(remote)})
        bundle = self.service.bundle(job["id"])
        with zipfile.ZipFile(bundle) as archive:
            notebook = json.loads(archive.read("training.ipynb"))
        runner = self.root / "continue.py"
        runner.write_text("".join(notebook["cells"][1]["source"]), encoding="utf-8")
        destination = self.root / "second-session"
        destination.mkdir()
        result = subprocess.run([sys.executable, str(runner)], cwd=destination, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout[-2000:])
        events = [json.loads(line) for line in (destination / "output" / "events.jsonl").read_text().splitlines()]
        self.assertEqual(next(event["step"] for event in events if event["type"] == "checkpoint"), 3)
        self.assertEqual(load_checkpoint(destination / "output" / "checkpoint.json")["step"], 8)

    def test_image_import_publishes_verified_companion_and_second_bundle(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow is optional")
        folder = self.root / "images"
        for label in ("a", "b"):
            (folder / label).mkdir(parents=True)
            for i in range(5):
                Image.new("RGB", (8, 8), (i * 20, 50 if label == "a" else 100, 0)).save(folder / label / f"{i}.png")
        asset = self.root / "base.pth"
        asset.write_bytes(b"fixture base weights; no torch load")
        digest = hashlib.sha256(asset.read_bytes()).hexdigest()
        with patch("multigpu.datasets.RESNET18_SHA256", digest):
            job = self.guided(recipe="image", dataset_path=str(folder), weights_path=str(asset), weights_sha256=digest)
            data = load_dataset(job["spec"])
            payload = {"head": {"weight": [[0.] * 512 for _ in range(2)], "bias": [0., 0.]},
                       "optimizer": {"state": {}, "param_groups": [{"params": [0, 1], "lr": .001, "momentum": .9}]},
                       "rng_state": random.Random(42).getstate(), "torch_rng": [0] * 5056, "cuda_rng": [],
                       "split": {"train_indices": data["train_indices"], "validation_indices": data["validation_indices"]},
                       "epoch": 0, "cursor": 0, "order": [], "weights_sha256": digest,
                       "weights_asset": "assets/base_weights.pth", "dataset_manifest_sha256": data["content_hash"]}
            remote = self.root / "image-output"
            write_checkpoint(remote, {"job_id": job["id"], "recipe": "image", "recipe_version": 2, "step": 0,
                                     "total_steps": job["spec"]["steps"], "fingerprint": data["fingerprint"],
                                     "elapsed_seconds": 0., "loss": None, "payload": payload})
            (remote / "assets").mkdir()
            shutil.copyfile(asset, remote / "assets" / "base_weights.pth")
            imported = self.service.import_output(job["id"], {"path": str(remote)})
            asset.unlink()
            checkpoint = Path(imported["spec"]["resume_from"])
            self.assertEqual((checkpoint.parent / "assets" / "base_weights.pth").read_bytes(), b"fixture base weights; no torch load")
            bundle = self.service.bundle(job["id"])
            self.assertTrue(bundle.is_file())
            local_before = checkpoint.read_bytes()
            (remote / "assets" / "base_weights.pth").write_bytes(b"corrupt")
            with self.assertRaises(IntegrityError):
                self.service.import_output(job["id"], {"path": str(remote)})
            self.assertEqual(checkpoint.read_bytes(), local_before)

    def test_changed_queued_data_is_rejected_before_provider_submission(self):
        data = self.root / "data.csv"
        data.write_text("x,label\n" + "".join(f"{i},{i % 2}\n" for i in range(20)))
        job = self.guided(dataset_path=str(data))
        job.update(provider_id="kaggle", status="queued", execution_mode="automatic")
        self.service.save_job(job)
        data.write_text(data.read_text().replace("0,0", "100,0", 1))
        with patch("multigpu.connectors.prepare") as prepare, patch("multigpu.connectors.submit") as submit:
            self.service._run(job["id"])
            prepare.assert_not_called()
            submit.assert_not_called()
        self.assertIn("changed after preflight", self.service.job(job["id"])["message"])

    def test_environment_busy_blocks_launch_and_resume(self):
        job = self.guided()
        job.update(status="paused")
        self.service.save_job(job)
        self.service.environment_busy = True
        with patch.object(self.service, "_start") as start:
            with self.assertRaisesRegex(ValueError, "environment preparation"):
                self.service.create({})
            with self.assertRaisesRegex(ValueError, "environment preparation"):
                self.service.resume(job["id"])
            start.assert_not_called()

    def test_pause_before_submission_does_not_launch_and_can_resume_fresh(self):
        job = self.remote()
        (self.service.jobs_root / job["id"] / "STOP").touch()
        with patch("multigpu.connectors.prepare") as prepare, patch("multigpu.connectors.submit") as submit:
            self.service._run(job["id"])
            prepare.assert_not_called()
            submit.assert_not_called()
        self.assertEqual(self.service.job(job["id"])["status"], "paused")
        with patch.object(self.service, "_start"):
            resumed = self.service.resume(job["id"], ["local"])
        self.assertEqual(resumed["status"], "queued")
        self.assertNotIn("resume_from", resumed["spec"])

    def test_completed_export_has_artifact_details_and_no_resume_action(self):
        job = self.guided()
        self.train(job)
        job.update(status="completed", phase="exporting", step=8)
        self.service.save_job(job)
        saved = self.service.export(job["id"])
        self.assertEqual(saved["phase"], "completed")
        self.assertTrue(saved["artifacts"])
        self.assertTrue(saved["export_verified_at"])
        self.assertNotIn("resume", self.service.job(job["id"])["allowed_actions"])

    def test_cache_cleanup_removes_bundle_archives_and_keeps_models(self):
        job = self.guided()
        path = self.train(job, runtime_seconds=0)
        original = (path / "checkpoint.json").read_bytes()
        (path / "model.json").write_text("model")
        (path / "notebook-bundle.zip").write_bytes(b"bundle")
        (path / "model.zip").write_bytes(b"download")
        self.assertEqual(self.service.clean_cache()["removed_bytes"], 14)
        self.assertFalse((path / "notebook-bundle.zip").exists())
        self.assertEqual((path / "checkpoint.json").read_bytes(), original)
        self.assertEqual((path / "model.json").read_text(), "model")

    def test_preflight_rejects_file_export_destination(self):
        destination = self.root / "file.txt"
        destination.write_text("existing")
        plan = self.service.plan({"output_dir": str(destination)})
        self.assertFalse(plan["eligible"])
        self.assertIn("file", " ".join(plan["preflight"]["errors"]))
        self.assertGreater(plan["preflight"]["storage"]["required_free_bytes"], 0)

    def test_preflight_checks_export_copy_space_and_selected_gpu_memory(self):
        weights = self.root / "weights.pth"
        weights.write_bytes(b"x" * 100)
        report = {"errors": [], "warnings": [], "fingerprint": "test", "dataset":
                  {"total_steps": 5, "estimated_batch_bytes": 2 * 1024 * 1024,
                   "weights": {"ready": True, "path": str(weights)}}}
        self.service.hardware.update(torch_available=True, torchvision_available=True, cuda_available=True,
                                     gpus=[{"memory_mb": 10}, {"memory_mb": 1}])
        disk = shutil.disk_usage(self.root)
        with patch("multigpu.datasets.inspect_dataset", return_value=copy.deepcopy(report)), \
             patch("multigpu.service.shutil.disk_usage", return_value=disk._replace(free=10 * 1024 * 1024 + 200)):
            plan = self.service.plan({"recipe": "image", "weights_path": str(weights), "device": "cuda", "gpu_index": 1})
        errors = " ".join(plan["preflight"]["errors"])
        self.assertIn("space", errors)
        self.assertIn("input batch", errors)
        self.assertEqual(plan["preflight"]["storage"]["required_free_bytes"], 10 * 1024 * 1024 + 300)

    def test_import_source_change_during_copy_preserves_verified_model(self):
        job = self.guided()
        remote = self.train(job, self.root / "remote-export")
        imported = self.service.import_output(job["id"], {"path": str(remote)})
        local = self.service.jobs_root / job["id"] / "export" / "model.json"
        original = local.read_bytes()
        copyfile = shutil.copyfile
        def changed_copy(source, destination, *args, **kwargs):
            source = Path(source)
            if source == remote / "export" / "model.json":
                source.write_bytes(b"changed after manifest verification")
            return copyfile(source, destination, *args, **kwargs)
        with patch("multigpu.artifacts.shutil.copyfile", side_effect=changed_copy):
            with self.assertRaisesRegex(IntegrityError, "changed during export"):
                self.service.import_output(imported["id"], {"path": str(remote)})
        self.assertEqual(local.read_bytes(), original)

    def test_summary_reports_invocation_budget_checkpoint_age_and_stop(self):
        path = self.root / "summary"
        with patch("multigpu.worker.time.monotonic", return_value=1000):
            ctx = TrainingContext({"steps": 20, "runtime_seconds": 900, "summary_interval_seconds": 300,
                                   "allowance_observation": {"confidence": "unknown"}}, path,
                                  {"step": 3, "elapsed_seconds": 4000, "payload": {}})
            ctx.phase = "training"
            ctx._checkpoint_step = 3
        (path / "STOP").touch()
        with patch("multigpu.worker.time.monotonic", return_value=1300), contextlib.redirect_stdout(io.StringIO()):
            ctx._tick()
        ctx.close()
        summary = [json.loads(line) for line in (path / "events.jsonl").read_text().splitlines() if json.loads(line)["type"] == "summary"][0]
        self.assertEqual(summary["remaining_session_seconds"], 600)
        self.assertEqual(summary["checkpoint_age_seconds"], 300)
        self.assertEqual(summary["active_gpu_count"], 0)
        self.assertEqual(summary["allowance_confidence"], "unknown")
        self.assertIn("stop", summary["required_action"])


if __name__ == "__main__":
    unittest.main()
