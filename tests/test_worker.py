import contextlib
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from multigpu.artifacts import (IntegrityError, export_artifacts, export_verified,
                               load_checkpoint, verify_export, write_checkpoint)
from multigpu.benchmark import benchmark
from multigpu.worker import TrainingContext, WorkerLock, worker_active, run
from multigpu.prediction import predict


class WorkerTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / ".test-runs"
        scratch.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.root = Path(self.temporary.name)
        self.spec = {"id": "test", "recipe": "starter", "steps": 80, "learning_rate": 0.2,
                     "batch_size": 16, "seed": 4, "device": "cpu", "runtime_seconds": 30}

    def tearDown(self):
        self.temporary.cleanup()

    def train(self, spec, path):
        with contextlib.redirect_stdout(io.StringIO()):
            return run(spec, path)

    def test_real_learning_and_verified_portable_export(self):
        job = self.root / "job"
        result = self.train(self.spec, job)
        self.assertEqual(result["status"], "completed")
        events = [json.loads(line) for line in (job / "events.jsonl").read_text().splitlines()]
        losses = [event["loss"] for event in events if event["type"] == "metric"]
        self.assertLess(events[-1]["loss"], losses[0])
        model = json.loads((job / "export" / "model.json").read_text())
        self.assertTrue(any(weight != 0 for weight in model["weights"]))
        destination = export_verified(job, self.root / "download")
        manifest = verify_export(destination)
        self.assertIn("checkpoint.json", [entry["path"] for entry in manifest["files"]])
        self.assertEqual(load_checkpoint(destination / "checkpoint.json")["step"], 80)
        self.assertEqual(events[-1]["type"], "completed")

    def test_cancellation_resume_matches_uninterrupted_training(self):
        baseline = self.root / "baseline"
        self.train(self.spec, baseline)
        interrupted = self.root / "interrupted"
        original = TrainingContext.report
        def cancel_after_17(ctx, *args, **kwargs):
            original(ctx, *args, **kwargs)
            if ctx.step == 17:
                (ctx.output_dir / "STOP").touch()
        with patch.object(TrainingContext, "report", cancel_after_17):
            paused = self.train(self.spec, interrupted)
        self.assertEqual((paused["status"], paused["step"]), ("paused", 17))
        self.assertEqual(load_checkpoint(interrupted / "checkpoint.previous.json")["step"], 0)
        (interrupted / "STOP").unlink()
        resumed = self.train({**self.spec, "resume_from": str(interrupted / "checkpoint.json")}, interrupted)
        self.assertEqual(resumed["status"], "completed")
        self.assertEqual(load_checkpoint(baseline / "checkpoint.json")["payload"],
                         load_checkpoint(interrupted / "checkpoint.json")["payload"])
        self.assertGreaterEqual(resumed["elapsed_seconds"], paused["elapsed_seconds"])

    def test_runtime_deadline_pauses_without_lowering_target(self):
        job = self.root / "deadline"
        paused = self.train({**self.spec, "runtime_seconds": 0}, job)
        self.assertEqual(paused["status"], "paused")
        self.assertEqual(paused["step"], 0)
        self.assertEqual(paused["total_steps"], 80)
        resumed = self.train({**self.spec, "resume_from": str(job / "checkpoint.json")}, job)
        self.assertEqual(resumed["step"], 80)
        self.assertEqual(resumed["status"], "completed")

    def test_checkpoint_tampering_and_config_mismatch_are_rejected(self):
        job = self.root / "job"
        self.train({**self.spec, "runtime_seconds": 0}, job)
        changed = self.train({**self.spec, "learning_rate": 0.4, "resume_from": str(job / "checkpoint.json")}, job)
        self.assertEqual(changed["status"], "error")
        self.assertIn("does not match", changed["error"])
        envelope = json.loads((job / "checkpoint.json").read_text())
        envelope["state"]["step"] = 30
        (job / "checkpoint.json").write_text(json.dumps(envelope))
        with self.assertRaises(IntegrityError):
            load_checkpoint(job / "checkpoint.json")
        failed = self.train({**self.spec, "resume_from": str(job / "checkpoint.json")}, job)
        self.assertEqual(failed["status"], "error")

    def test_failed_disk_write_preserves_last_good_checkpoint(self):
        path = write_checkpoint(self.root, {"step": 3, "payload": {"weight": 1.2}})
        with patch("multigpu.artifacts.shutil.disk_usage") as usage:
            usage.return_value.free = 0
            with self.assertRaises(OSError):
                write_checkpoint(self.root, {"step": 4})
        self.assertEqual(load_checkpoint(path)["step"], 3)

    def test_export_rejects_paths_and_corruption(self):
        for name in ("../escape", "/absolute", "C:/absolute", "x/../../escape", "a\\..\\escape", "NUL.txt"):
            with self.subTest(name=name), self.assertRaises(IntegrityError):
                export_artifacts({name: "no"}, self.root / "unsafe")
        output = export_artifacts({"model.json": "original"}, self.root / "export")
        (output / "model.json").write_text("tampered")
        with self.assertRaises(IntegrityError):
            verify_export(output)
        with self.assertRaises(IntegrityError):
            export_artifacts({"A.json": "one", "a.json": "two"}, self.root / "case-collision")

    def test_export_rejects_symlink_source(self):
        target = self.root / "target"
        target.write_text("model")
        link = self.root / "link"
        try:
            link.symlink_to(target)
        except OSError:
            self.skipTest("Symbolic link creation is unavailable in this Windows environment")
        with self.assertRaises(IntegrityError):
            export_artifacts({"model.json": link}, self.root / "export")

    def test_csv_validation_and_changed_dataset_resume(self):
        dataset = self.root / "data.csv"
        dataset.write_text("x,y,label\n1,2,1\n-1,-2,0\n")
        job = self.root / "csv"
        paused = self.train({**self.spec, "dataset_path": str(dataset), "runtime_seconds": 0}, job)
        self.assertEqual(paused["status"], "paused")
        dataset.write_text("x,y,label\n1,2,0\n-1,-2,0\n")
        failed = self.train({**self.spec, "dataset_path": str(dataset), "resume_from": str(job / "checkpoint.json")}, job)
        self.assertEqual(failed["status"], "error")
        dataset.write_text("1,nan,0\n")
        self.assertEqual(self.train({**self.spec, "dataset_path": str(dataset)}, self.root / "bad-csv")["status"], "error")

    def test_custom_hooks_and_changed_dataset_detection(self):
        script = self.root / "train.py"
        script.write_text("""import json
def train(ctx):
    state = ctx.resume_state or {'value': 0}
    while ctx.step < ctx.spec['steps'] and not ctx.should_stop():
        state['value'] += 1
        ctx.report(1 / state['value'])
        ctx.checkpoint(state)
        if ctx.step == 3:
            (ctx.output_dir / 'STOP').touch()
    ctx.checkpoint(state)
    if ctx.step == ctx.spec['steps']:
        ctx.export({'model.json': json.dumps(state)})
""")
        data = self.root / "input.dat"
        data.write_text("first dataset")
        job = self.root / "custom"
        spec = {**self.spec, "recipe": "custom", "steps": 5, "script_path": str(script), "dataset_path": str(data)}
        self.assertEqual(self.train(spec, job)["status"], "paused")
        (job / "STOP").unlink()
        resume = {**spec, "resume_from": str(job / "checkpoint.json")}
        data.write_text("different dataset")
        self.assertEqual(self.train(resume, job)["status"], "error")
        data.write_text("first dataset")
        self.assertEqual(self.train(resume, job)["status"], "completed")
        self.assertEqual(json.loads((job / "export" / "model.json").read_text())["value"], 5)

    def test_custom_cannot_relabel_stale_checkpoint_as_new_progress(self):
        script = self.root / "stale.py"
        script.write_text("def train(ctx):\n    ctx.checkpoint({'weight': 1})\n    ctx.report(0.4)\n    (ctx.output_dir / 'STOP').touch()\n")
        job = self.root / "stale"
        result = self.train({**self.spec, "recipe": "custom", "script_path": str(script)}, job)
        self.assertEqual(result["status"], "error")
        self.assertEqual(load_checkpoint(job / "checkpoint.json")["step"], 0)

    def test_five_minute_summary_events_are_separate_from_checkpoints(self):
        job = self.root / "summary"
        with patch("multigpu.worker.time.monotonic", return_value=0) as clock, contextlib.redirect_stdout(io.StringIO()):
            ctx = TrainingContext({**self.spec, "summary_interval_seconds": 300}, job)
            ctx.checkpoint({"weight": 0})
            clock.return_value = 299
            ctx.report(0.6, 1)
            before = [json.loads(line) for line in (job / "events.jsonl").read_text().splitlines()]
            self.assertFalse(any(event["type"] == "summary" for event in before))
            clock.return_value = 300
            ctx.report(0.5, 2)
        events = [json.loads(line) for line in (job / "events.jsonl").read_text().splitlines()]
        self.assertEqual(sum(event["type"] == "summary" for event in events), 1)
        self.assertEqual(sum(event["type"] == "checkpoint" for event in events), 1)

    def test_unavailable_gpu_and_benchmark_are_explicit(self):
        result = self.train({**self.spec, "device": "cuda"}, self.root / "cuda")
        self.assertEqual(result["status"], "error")
        with patch.dict("sys.modules", {"torch": None}):
            failed = self.train({**self.spec, "recipe": "pytorch"}, self.root / "torch")
            self.assertIn("not installed", failed["error"])
            measured = benchmark()
        self.assertEqual(measured["status"], "unavailable")
        self.assertFalse(measured["qualified"])
        self.assertFalse(measured["cross_provider_qualified"])
        self.assertNotIn("speedup", measured)

    def test_v2_evaluation_prediction_and_exact_resume(self):
        spec = {**self.spec, "recipe_version": 2}
        baseline = self.root / "v2-baseline"
        complete = self.train(spec, baseline)
        self.assertEqual(complete["status"], "completed")
        self.assertEqual(complete["evaluation"]["split"], "validation")
        self.assertGreater(complete["evaluation"]["accuracy"], .8)
        schema = json.loads((baseline / "export" / "input_schema.json").read_text())
        self.assertEqual(schema["standardization"]["fitted_on"], "training_split_only")
        prediction = predict(baseline / "export", {"features": dict(zip(schema["features"], [.8, -.8, .1, .2]))})
        self.assertEqual(prediction["prediction"], 1)
        interrupted = self.root / "v2-pause"
        original = TrainingContext.report
        def stop(ctx, *args, **kwargs):
            original(ctx, *args, **kwargs)
            if ctx.step == 17:
                (ctx.output_dir / "STOP").touch()
        with patch.object(TrainingContext, "report", stop):
            self.assertEqual(self.train(spec, interrupted)["status"], "paused")
        (interrupted / "STOP").unlink()
        self.assertEqual(self.train({**spec, "resume_from": str(interrupted / "checkpoint.json")}, interrupted)["status"], "completed")
        self.assertEqual(load_checkpoint(baseline / "checkpoint.json")["payload"], load_checkpoint(interrupted / "checkpoint.json")["payload"])
        self.assertEqual(json.loads((baseline / "export" / "evaluation.json").read_text()),
                         json.loads((interrupted / "export" / "evaluation.json").read_text()))

    def test_independent_heartbeat_during_no_training_reports(self):
        job = self.root / "heartbeat"
        with contextlib.redirect_stdout(io.StringIO()):
            ctx = TrainingContext({**self.spec, "heartbeat_interval_seconds": .005, "summary_interval_seconds": .01}, job)
            ctx.start_telemetry()
            time.sleep(.04)
            ctx.close()
        events = [json.loads(line) for line in (job / "events.jsonl").read_text().splitlines()]
        self.assertTrue(any(item["type"] == "heartbeat" for item in events))
        self.assertTrue(any(item["type"] == "summary" for item in events))
        self.assertTrue(all(item["step"] == 0 and item["loss"] is None for item in events))
        size = (job / "events.jsonl").stat().st_size
        time.sleep(.02)
        self.assertEqual(size, (job / "events.jsonl").stat().st_size)

    def test_worker_lock_blocks_second_owner_and_stale_identity_is_not_active(self):
        folder = self.root / "locked"
        owner = WorkerLock(folder, {**self.spec, "attempt_id": "owner"})
        try:
            self.assertTrue(worker_active(folder))
            self.assertEqual(self.train(self.spec, folder)["status"], "error")
            self.assertFalse((folder / "events.jsonl").exists())
            identity = json.loads((folder / "worker-identity.json").read_text())
            self.assertEqual(identity["attempt_id"], "owner")
        finally:
            owner.close()
        self.assertFalse(worker_active(folder))
        self.assertFalse(json.loads((folder / "worker-identity.json").read_text())["active"])


if __name__ == "__main__":
    unittest.main()
