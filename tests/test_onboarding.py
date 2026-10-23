import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from multigpu.catalog import CATALOG
from multigpu.connectors import build_notebook, build_notebook_payload, needs_payload
from multigpu.artifacts import load_checkpoint, write_checkpoint
from multigpu.onboarding import job_setup, provider_setup
from multigpu.worker import run


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / ".test-runs"
        scratch.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="onboarding-", dir=scratch)
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def write_spec(self, spec):
        path = self.root / "spec.json"
        path.write_text(json.dumps(spec), encoding="utf-8")
        return path

    def execute(self, notebook, folder):
        folder.mkdir()
        document = json.loads(notebook.read_text())
        source = "\n".join("".join(cell["source"]) for cell in document["cells"] if cell["cell_type"] == "code")
        script = folder / "run-notebook.py"
        script.write_text(source, encoding="utf-8")
        return subprocess.run([sys.executable, str(script)], cwd=folder, capture_output=True, text=True, timeout=30)

    def test_all_providers_have_stable_renderable_guides_without_verification_claims(self):
        for provider in CATALOG:
            guide = provider_setup(provider["id"], {"ready": False}, {})
            with self.subTest(provider=provider["id"]):
                self.assertEqual(guide["provider_id"], provider["id"])
                self.assertTrue(guide["steps"])
                self.assertEqual(len({step["id"] for step in guide["steps"]}), len(guide["steps"]))
                for step in guide["steps"]:
                    self.assertIn(step["completion"], ("automatic", "acknowledgement"))
                    self.assertIsInstance(step["instructions"], list)
                    for link in step["links"]:
                        self.assertTrue(link["url"].startswith("https://"))
                if provider["id"] != "local":
                    self.assertFalse(any(step["ready"] for step in guide["steps"] if step["id"] == "verify"))

    def test_automatic_auth_requires_actual_identity_and_scoped_command(self):
        hardware = {"python": str(self.root / "managed env" / "Scripts" / "python.exe")}
        with patch("multigpu.onboarding.importlib.util.find_spec", return_value=None):
            guide = provider_setup("kaggle", {"ready": True, "account_verified": False}, hardware)
        steps = {step["id"]: step for step in guide["steps"]}
        self.assertFalse(steps["verify"]["can_complete"])
        self.assertFalse(steps["tools"]["ready"])
        self.assertIn(str(self.root), steps["signin"]["commands"][0])
        self.assertIn("kaggle", steps["signin"]["commands"][0])
        verified = provider_setup("kaggle", {"ready": True, "account_verified": True}, hardware)
        self.assertTrue(next(step for step in verified["steps"] if step["id"] == "verify")["ready"])

    def test_initial_job_guide_requires_import_and_has_no_false_target_blocker(self):
        job = {"id": "abc123", "provider_id": "colab", "status": "awaiting_user", "spec": {"recipe": "starter", "steps": 5}}
        guide = job_setup(job, self.root)
        self.assertFalse(guide["payload_required"])
        self.assertNotIn("blocking_reason", guide["steps"][0])
        self.assertFalse(guide["steps"][-1]["ready"])
        self.assertEqual(guide["steps"][0]["actions"][0]["path"], "/api/jobs/abc123/notebook")

    def test_small_direct_notebook_trains_and_creates_results_without_zip_instructions(self):
        spec = self.write_spec({"id": "test", "recipe": "starter", "recipe_version": 2, "steps": 5})
        notebook = build_notebook("colab", {}, spec, self.root)
        self.assertEqual(notebook.name, "training.ipynb")
        self.assertFalse((self.root / "notebook-bundle.zip").exists())
        self.assertFalse(list(self.root.glob("payload-*")))
        result = self.execute(notebook, self.root / "session")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout[-1000:])
        with zipfile.ZipFile(self.root / "session" / "multigpu-results.zip") as archive:
            self.assertIn("checkpoint.json", archive.namelist())
            self.assertIn("export/model.json", archive.namelist())

    def large_custom(self):
        data = self.root / "large.dat"
        with data.open("wb") as handle:
            handle.truncate(5 * 1024 * 1024)
        script = self.root / "train.py"
        script.write_text("def train(ctx):\n"
                          "    state = ctx.resume_state or {'weight': 0}\n"
                          "    ctx.checkpoint(state)\n"
                          "    while ctx.step < ctx.spec['steps'] and not ctx.should_stop():\n"
                          "        state['weight'] += 1\n"
                          "        ctx.report(1 / (state['weight'] + 1))\n"
                          "        ctx.checkpoint(state)\n"
                          "    if ctx.step == ctx.spec['steps']:\n"
                          "        ctx.export({'model.json': str(state['weight'])})\n", encoding="utf-8")
        return {"id": "test", "recipe": "custom", "recipe_version": 2, "steps": 3,
                "dataset_path": str(data), "script_path": str(script), "device": "cpu"}

    def test_notebook_then_payload_pair_executes_and_continues_saved_step(self):
        spec = self.large_custom()
        output = self.root / "checkpoint-source"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run({**spec, "runtime_seconds": 0}, output)["status"], "paused")
        state = load_checkpoint(output / "checkpoint.json")
        state["step"], state["payload"]["weight"] = 1, 1
        write_checkpoint(output, state)
        spec["resume_from"] = str(output / "checkpoint.json")
        path = self.write_spec(spec)
        self.assertTrue(needs_payload(spec))
        notebook = build_notebook("colab-enterprise", {}, path, self.root)
        os.utime(spec["dataset_path"], (1_000_000_000, 1_000_000_000))  # Different ZIP timestamps cannot invalidate the pair.
        payload = build_notebook_payload("colab-enterprise", {}, path, self.root)
        self.assertLess(notebook.stat().st_size, 20 * 1024)
        self.assertFalse(list(self.root.glob("payload-*")))
        session = self.root / "paired-session"
        session.mkdir()
        shutil.copyfile(payload, session / payload.name)
        session.rmdir if False else None
        document = json.loads(notebook.read_text())
        runner = session / "run.py"
        runner.write_text("\n".join("".join(cell["source"]) for cell in document["cells"] if cell["cell_type"] == "code"), encoding="utf-8")
        result = subprocess.run([sys.executable, str(runner)], cwd=session, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout[-1000:])
        checkpoint = load_checkpoint(session / "output" / "checkpoint.json")
        self.assertEqual(checkpoint["step"], 3)
        self.assertEqual(checkpoint["payload"]["weight"], 3)
        first = json.loads((session / "output" / "events.jsonl").read_text().splitlines()[0])
        self.assertEqual(first["step"], 1)
        self.assertTrue((session / "multigpu-results.zip").is_file())

    def test_extracted_payload_is_supported_and_corruption_stops_before_training(self):
        spec = self.large_custom()
        path = self.write_spec(spec)
        notebook = build_notebook("kaggle", {}, path, self.root)
        payload = build_notebook_payload("kaggle", {}, path, self.root)
        session = self.root / "extracted-session"
        session.mkdir()
        with zipfile.ZipFile(payload) as archive:
            archive.extractall(session / "training-payload")
        code = "".join(json.loads(notebook.read_text())["cells"][1]["source"])
        runner = session / "run.py"
        runner.write_text(code, encoding="utf-8")
        result = subprocess.run([sys.executable, str(runner)], cwd=session, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        bad = self.root / "bad-session"
        bad.mkdir()
        with zipfile.ZipFile(payload) as archive:
            archive.extractall(bad / "training-payload")
        (bad / "training-payload" / "assets" / "dataset_path.dat").write_bytes(b"corrupt")
        runner = bad / "run.py"
        runner.write_text(code, encoding="utf-8")
        result = subprocess.run([sys.executable, str(runner)], cwd=bad, capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("contents changed", result.stderr)
        self.assertFalse((bad / "output").exists())

    def test_continuation_guide_requires_new_import_after_preparation(self):
        spec = {"id": "abc123", "recipe": "starter", "recipe_version": 2, "steps": 5}
        with contextlib.redirect_stdout(io.StringIO()):
            run({**spec, "runtime_seconds": 0}, self.root)
        state = load_checkpoint(self.root / "checkpoint.json")
        job = {"id": spec["id"], "provider_id": "colab", "status": "paused", "spec": spec,
               "fingerprint": state["fingerprint"], "checkpoint": {"path": str(self.root / "checkpoint.json"), "step": 0}}
        first = job_setup(job, self.root)
        self.assertTrue(first["steps"][-1]["ready"])
        job["notebook_preparation"] = {"prepared_at": "2026-10-24T10:00:00+00:00"}
        prepared = job_setup(job, self.root)
        self.assertEqual(prepared["revision"], first["revision"])
        self.assertFalse(prepared["steps"][-1]["ready"])
        job.update(last_import_verified=True, last_import_at="2026-10-24T10:01:00+00:00")
        self.assertTrue(job_setup(job, self.root)["steps"][-1]["ready"])
        state["step"] = 1
        write_checkpoint(self.root, state)
        self.assertNotEqual(job_setup(job, self.root)["revision"], first["revision"])


if __name__ == "__main__":
    unittest.main()
