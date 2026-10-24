import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from types import ModuleType
import unittest
from unittest.mock import MagicMock, patch
import zipfile

from multigpu import connectors
from multigpu.artifacts import load_checkpoint, verify_export, write_checkpoint
from multigpu.notebook_runtime import (RecoveryPersistenceError, job_key, mirror_checkpoint,
                                      package_results, publish_progress, _session_lock)


class NotebookRuntimeTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / ".test-runs"
        scratch.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="notebook-runtime-", dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.spec = {"id": "job-a", "recipe": "starter", "recipe_version": 2, "steps": 4,
                     "runtime_seconds": 30, "learning_rate": .05}

    def notebook(self, spec=None):
        spec = spec or self.spec
        folder = self.root / ("preparation-" + spec["id"])
        folder.mkdir(exist_ok=True)
        path = folder / "spec.json"
        path.write_text(json.dumps(spec))
        return json.loads(connectors.build_notebook("colab", {}, path, folder).read_text())

    def execute(self, notebook, session, recovery=None):
        session.mkdir(exist_ok=True)
        code = "\n".join("".join(c["source"]) for c in notebook["cells"] if c["cell_type"] == "code")
        if recovery is not None:
            code = code.replace("RECOVERY_DIR = None", "RECOVERY_DIR = " + repr(str(recovery)), 1)
        script = session / "notebook.py"
        script.write_text("import os\nfrom pathlib import Path\n(Path.cwd()/'notebook-pid').write_text(str(os.getpid()))\n" + code)
        return subprocess.run([sys.executable, str(script)], cwd=session, capture_output=True, text=True, timeout=40)

    def output(self, session, spec=None):
        return session / "multigpu-runs" / job_key(spec or self.spec) / "output"

    def custom(self):
        script = self.root / "custom.py"
        script.write_text("import os\n"
                          "def train(ctx):\n"
                          " state = ctx.resume_state or {'weight': 0}\n"
                          " ctx.checkpoint(state)\n"
                          " while ctx.step < ctx.spec['steps'] and not ctx.should_stop():\n"
                          "  state['weight'] += 1\n"
                          "  state['worker_pid'] = os.getpid()\n"
                          "  ctx.report(1/(state['weight']+1))\n"
                          "  ctx.checkpoint(state)\n"
                          "  if state['weight'] == 1:\n"
                          "   (ctx.output_dir/'STOP').touch()\n"
                          "   break\n"
                          " if ctx.step == ctx.spec['steps']:\n"
                          "  ctx.export({'model.json': str(state['weight'])})\n")
        return {**self.spec, "recipe": "custom", "script_path": str(script), "device": "cpu"}

    def test_rerun_reuses_completed_export_and_packages_only_current_model(self):
        notebook = self.notebook()
        session = self.root / "session"
        first = self.execute(notebook, session)
        self.assertEqual(first.returncode, 0, first.stderr)
        output = self.output(session)
        before = (output / "events.jsonl").read_bytes()
        export_digest = hashlib.sha256((output / "export" / "manifest.json").read_bytes()).hexdigest()
        second = self.execute(notebook, session)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("Reusing this job's verified completed model", second.stdout)
        self.assertEqual((output / "events.jsonl").read_bytes(), before)
        self.assertEqual(hashlib.sha256((output / "export" / "manifest.json").read_bytes()).hexdigest(), export_digest)
        self.assertFalse(list(output.glob("export.previous-*")), "Rerunning retrained and made a second export")
        backup = output / "export.previous-old"
        shutil.copytree(output / "export", backup)
        (output / "export" / "unlisted.bin").write_bytes(b"not in verified manifest")
        result = package_results(output)
        with zipfile.ZipFile(result) as archive:
            names = archive.namelist()
            self.assertIn("export/model.json", names)
            self.assertIn("events.jsonl", names)
            self.assertIn("notebook-session.json", names)
            self.assertFalse(any("previous-" in n or n.endswith(".lock") or n.endswith("unlisted.bin") for n in names))
            download = self.root / "download"
            archive.extractall(download)
        imported = self.root / "imported"
        imported.mkdir()
        (imported / "spec.json").write_text(json.dumps(self.spec))
        connectors._publish_outputs(download, imported)
        self.assertEqual(load_checkpoint(imported / "checkpoint.json")["step"], 4)
        self.assertTrue(verify_export(imported / "export")["files"])
        self.assertIn("files.download", "".join(notebook["cells"][-1]["source"]))

    def test_packaging_uses_the_notebook_session_lock(self):
        session = self.root / "locked"
        self.assertEqual(self.execute(self.notebook(), session).returncode, 0)
        output = self.output(session)
        result = output.parent / "multigpu-results.zip"
        original = result.read_bytes()
        with _session_lock(output.parent):
            with self.assertRaisesRegex(RuntimeError, "already running"):
                package_results(output)
        self.assertEqual(result.read_bytes(), original)
        self.assertEqual(package_results(output), result)

    def test_optional_colab_setup_is_disabled_and_training_preserves_its_choice(self):
        notebook = self.notebook()
        setup = next("".join(c["source"]) for c in notebook["cells"] if "ENABLE_DRIVE_RECOVERY =" in "".join(c["source"]))
        namespace = {}
        exec(setup, namespace)
        self.assertIsNone(namespace["RECOVERY_DIR"])
        mounted = self.root / "mounted"
        mounted.mkdir()
        drive = SimpleNamespace(mount=MagicMock())
        google, colab = ModuleType("google"), ModuleType("google.colab")
        colab.drive = drive
        with patch.dict(sys.modules, {"google": google, "google.colab": colab}):
            enabled = setup.replace("ENABLE_DRIVE_RECOVERY = False", "ENABLE_DRIVE_RECOVERY = True").replace("DRIVE_RECOVERY_FOLDER = ''", "DRIVE_RECOVERY_FOLDER = " + repr(str(mounted)))
            exec(enabled, namespace)
        drive.mount.assert_called_once_with("/content/drive")
        self.assertEqual(namespace["RECOVERY_DIR"], str(mounted.resolve()))
        training = next("".join(c["source"]) for c in notebook["cells"] if "launch_notebook(base" in "".join(c["source"]))
        exec(training.splitlines()[0], namespace)
        self.assertEqual(namespace["RECOVERY_DIR"], str(mounted.resolve()))

    def test_two_notebooks_share_session_without_resetting_each_other(self):
        a = self.notebook()
        bspec = {**self.spec, "id": "job-b", "steps": 7}
        b = self.notebook(bspec)
        session = self.root / "shared"
        self.assertEqual(self.execute(a, session).returncode, 0)
        before = (self.output(session) / "checkpoint.json").read_bytes()
        result = self.execute(b, session)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.output(session) / "checkpoint.json").read_bytes(), before)
        self.assertEqual(load_checkpoint(self.output(session, bspec) / "checkpoint.json")["step"], 7)

    def test_rerun_resumes_checkpoint_in_a_subprocess(self):
        spec = self.custom()
        notebook = self.notebook(spec)
        session = self.root / "resume"
        first = self.execute(notebook, session)
        self.assertEqual(first.returncode, 0, first.stderr)
        output = self.output(session, spec)
        saved = load_checkpoint(output / "checkpoint.json")
        self.assertEqual(saved["step"], 1)
        self.assertNotEqual(saved["payload"]["worker_pid"], int((session / "notebook-pid").read_text()))
        result = self.execute(notebook, session)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(load_checkpoint(output / "checkpoint.json")["payload"]["weight"], 4)
        events = [json.loads(line) for line in (output / "events.jsonl").read_text().splitlines()]
        paused = next(i for i, event in enumerate(events) if event["type"] == "paused")
        self.assertEqual(next(event["step"] for event in events[paused + 1:] if event["type"] == "checkpoint"), 1)
        self.assertEqual(verify_export(output / "export")["metadata"]["step"], 4)

    def test_optional_mounted_recovery_restores_across_sessions_and_keeps_old_copies(self):
        spec = self.custom()
        notebook = self.notebook(spec)
        recovery = self.root / "mounted-drive"
        recovery.mkdir()
        first = self.execute(notebook, self.root / "session-1", recovery)
        self.assertEqual(first.returncode, 0, first.stderr)
        copies = list((recovery / job_key(spec) / "checkpoints").iterdir())
        self.assertGreaterEqual(len(copies), 2)
        result = self.execute(notebook, self.root / "session-2", recovery)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = self.output(self.root / "session-2", spec)
        self.assertEqual(load_checkpoint(output / "checkpoint.json")["step"], 4)
        self.assertTrue(all(copy.exists() for copy in copies))

    def test_incomplete_recovery_copy_cannot_replace_an_older_verified_checkpoint(self):
        spec = self.custom()
        notebook = self.notebook(spec)
        recovery = self.root / "mounted-drive"
        recovery.mkdir()
        self.assertEqual(self.execute(notebook, self.root / "session-1", recovery).returncode, 0)
        copies = sorted((recovery / job_key(spec) / "checkpoints").iterdir())
        damaged = copies[-1] / "checkpoint.json"
        damaged.write_bytes(b"interrupted recovery copy")
        result = self.execute(notebook, self.root / "session-2", recovery)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = self.output(self.root / "session-2", spec)
        first = json.loads((output / "events.jsonl").read_text().splitlines()[0])
        self.assertEqual(first["step"], 0)
        self.assertEqual(load_checkpoint(output / "checkpoint.json")["step"], 1)
        self.assertEqual(damaged.read_bytes(), b"interrupted recovery copy")
        self.assertTrue(copies[0].is_dir())

    def test_missing_requested_recovery_preserves_local_checkpoint(self):
        notebook = self.notebook()
        session = self.root / "safe"
        self.assertEqual(self.execute(notebook, session).returncode, 0)
        output = self.output(session)
        original = (output / "checkpoint.json").read_bytes()
        self.assertIsNone(mirror_checkpoint(output, self.spec))
        with self.assertRaises(RecoveryPersistenceError):
            mirror_checkpoint(output, {**self.spec, "recovery_dir": str(self.root / "not-mounted")})
        self.assertEqual((output / "checkpoint.json").read_bytes(), original)

    def test_corrupt_current_checkpoint_falls_back_without_discarding_it(self):
        spec = self.custom()
        notebook = self.notebook(spec)
        session = self.root / "corrupt"
        self.assertEqual(self.execute(notebook, session).returncode, 0)
        output = self.output(session, spec)
        (output / "checkpoint.json").write_bytes(b"incomplete write")
        result = self.execute(notebook, session)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(load_checkpoint(output / "checkpoint.json")["step"], 1)
        self.assertEqual(next(output.glob("checkpoint.rejected-*.json")).read_bytes(), b"incomplete write")

    def test_foreign_checkpoint_is_preserved_and_pinned_seed_is_checked_before_reuse(self):
        spec = self.custom()
        notebook = self.notebook(spec)
        session = self.root / "foreign"
        self.assertEqual(self.execute(notebook, session).returncode, 0)
        output = self.output(session, spec)
        state = load_checkpoint(output / "checkpoint.json")
        state["job_id"] = "someone-else"
        write_checkpoint(output, state)
        original = (output / "checkpoint.json").read_bytes()
        result = self.execute(notebook, session)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("another job", result.stderr)
        self.assertEqual((output / "checkpoint.json").read_bytes(), original)
        completed_session = self.root / "pinned"
        self.assertEqual(self.execute(self.notebook(), completed_session).returncode, 0)
        checkpoint = self.output(completed_session) / "checkpoint.json"
        pinned = self.notebook({**self.spec, "resume_from": str(checkpoint), "resume_sha256": "0" * 64})
        result = self.execute(pinned, completed_session)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("recovery checkpoint changed", result.stderr)

    def test_compact_modal_progress_fetches_only_unseen_history(self):
        output = self.root / "remote"
        output.mkdir()
        events = [{"type": "metric", "step": i, "loss": 1/(i+1)} for i in range(1200)]
        (output / "events.jsonl").write_text("\n".join(map(json.dumps, events)) + "\n")
        cursor = {}
        publish_progress(output, cursor)
        local = self.root / "local"
        local.mkdir()
        reads = []
        def read(name):
            reads.append(name)
            return [output.joinpath(name).read_bytes()]
        volume = SimpleNamespace(read_file=read)
        modal = SimpleNamespace(Volume=SimpleNamespace(from_name=lambda *a, **kw: volume))
        handle = {"volume": "isolated-fixture"}
        connectors._modal_snapshot(modal, handle, local)
        self.assertEqual(len((local / "events.jsonl").read_text().splitlines()), 1200)
        reads.clear()
        connectors._modal_snapshot(modal, handle, local)
        self.assertNotIn("events.jsonl", reads)
        self.assertFalse(any(name.startswith("history/") for name in reads))
        with (output / "events.jsonl").open("a") as stream:
            stream.write(json.dumps({"type": "summary", "step": 1200}) + "\n")
        publish_progress(output, cursor)
        reads.clear()
        connectors._modal_snapshot(modal, handle, local)
        self.assertEqual([n for n in reads if n.startswith("history/")], ["history/00000003.jsonl"])

    def test_modal_freezes_cpu_allocation_instead_of_mutable_config(self):
        config = {"workspace": "test", "entitlement_confirmed": True, "budget_confirmed": True,
                  "native_spend_limit": 1, "gpu": "A100"}
        spec = {**self.spec, "free_only": False, "paid_cap": 1,
                "resolved_resource": {"provider_id": "modal", "device": "cpu", "gpu": None, "gpu_index": None}}
        path = self.root / "spec.json"
        path.write_text(json.dumps(spec))
        with patch.object(connectors, "_modal_identity", return_value={"workspace": "test"}):
            intent = connectors.prepare("modal", config, path, self.root)
        self.assertIsNone(intent["requested_gpu"])
        self.assertEqual(json.loads(Path(intent["stage_path"]).joinpath("spec.json").read_text())["device"], "cpu")
        app = MagicMock()
        app.app_id = "fixture-app"
        app.function.return_value = lambda fn: SimpleNamespace(spawn=lambda: SimpleNamespace(object_id="fixture-call"))
        sdk = MagicMock()
        sdk.App.return_value = app
        sdk.Image.debian_slim.return_value.add_local_dir.return_value = sdk.Image.debian_slim.return_value
        sdk.Dict.from_name.return_value.put.return_value = True
        with patch.object(connectors, "_sdk", return_value=sdk):
            connectors._submit_modal({**config, "gpu": "H100"}, spec, Path(intent["stage_path"]), intent)
        self.assertNotIn("gpu", app.function.call_args.kwargs)
        gpu_spec = {**spec, "recipe": "pytorch", "resolved_resource": {"provider_id": "modal", "device": "cuda", "gpu": "T4", "gpu_index": None}}
        path.write_text(json.dumps(gpu_spec))
        with patch.object(connectors, "_modal_identity", return_value={"workspace": "test"}):
            gpu_intent = connectors.prepare("modal", config, path, self.root)
        with patch.object(connectors, "_sdk", return_value=sdk):
            connectors._submit_modal({**config, "gpu": "H100"}, gpu_spec, Path(gpu_intent["stage_path"]), gpu_intent)
        self.assertEqual(app.function.call_args.kwargs["gpu"], "T4")
        with patch.object(connectors, "_sdk") as sdk_call:
            with self.assertRaises(connectors.ConnectorError):
                connectors._submit_modal(config, spec, Path(intent["stage_path"]), {**intent, "requested_gpu": "A100"})
            sdk_call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
