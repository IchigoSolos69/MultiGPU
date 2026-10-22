import base64
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

from multigpu import connectors as c
from multigpu.artifacts import export_artifacts, load_checkpoint, verify_export, write_checkpoint
from multigpu.catalog import CATALOG, get_provider


class ConnectorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="connector-test-", dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.spec = self.root / "spec.json"
        self.spec.write_text(json.dumps(dict(recipe="starter", steps=3, runtime_seconds=30)), encoding="utf-8")

    def test_catalog_is_complete_and_does_not_claim_account_verification(self):
        self.assertEqual(len(CATALOG), 14)
        self.assertEqual(len({p["id"] for p in CATALOG}), 14)
        self.assertTrue(all(not p["account_verified"] for p in CATALOG))
        self.assertFalse(get_provider("kaggle")["capabilities"]["stop"])
        record = get_provider("local")
        record["capabilities"]["stop"] = False
        self.assertTrue(get_provider("local")["capabilities"]["stop"])

    @patch.object(c, "_cli", return_value="resource,used,remaining,total,refreshAt\nGPU,2,0,2,tomorrow\n")
    def test_exhausted_quota_is_not_ready(self, cli):
        result = c.probe("kaggle", {})
        self.assertFalse(result["ready"])
        self.assertTrue(result["account_verified"])
        self.assertEqual(result["quota"]["remaining_hours"], 0)

    @patch.object(c, "_cli", return_value="No quota information available\n")
    def test_unknown_quota_blocks_submission(self, cli):
        with self.assertRaisesRegex(c.ConnectorError, "no readable GPU quota"):
            c.submit("kaggle", {"username": "trainer"}, self.spec, self.root)
        self.assertEqual(cli.call_count, 1)

    @patch.object(c, "_cli", return_value="resource,used,remaining,total,refreshAt\nGPU,2,0,2,tomorrow\n")
    def test_exhausted_quota_never_pushes(self, cli):
        with self.assertRaisesRegex(c.ConnectorError, "quota is exhausted"):
            c.submit("kaggle", {"username": "trainer"}, self.spec, self.root)
        self.assertEqual(cli.call_count, 1)

    @patch.object(c, "_cli")
    def test_kaggle_push_is_bounded_and_self_contained(self, cli):
        cli.side_effect = ["resource,used,remaining,total,refreshAt\nGPU,0,0.1,1,tomorrow\n", "pushed"]
        self.spec.write_text(json.dumps(dict(recipe="starter", steps=3, runtime_seconds=999)), encoding="utf-8")
        handle = c.submit("kaggle", {"username": "trainer"}, self.spec, self.root)
        args = cli.call_args.args[1]
        self.assertEqual(args[args.index("--timeout") + 1], "270")
        stage = Path(args[args.index("-p") + 1])
        metadata = json.loads((stage / "kernel-metadata.json").read_text())
        self.assertTrue(metadata["is_private"])
        source = (stage / "train.py").read_text()
        archive = self.embedded_archive(source)
        self.assertNotIn("train.py", archive.namelist())
        self.assertEqual(json.loads(archive.read("spec.json"))["runtime_seconds"], 240)
        self.assertEqual(handle["state"], "queued")

    @patch.object(c.subprocess, "run")
    def test_cli_error_never_echoes_provider_credentials(self, run):
        run.return_value = SimpleNamespace(returncode=1, stdout="token=super-secret", stderr="Authorization: Bearer super-secret")
        with self.assertRaises(c.ConnectorError) as raised:
            c._cli("kaggle", ["quota"])
        self.assertNotIn("super-secret", str(raised.exception))
        with self.assertRaisesRegex(c.ConnectorError, "native credential"):
            c.probe("modal", {"api_key": "super-secret"})

    @patch.object(c, "_sdk")
    def test_lightning_authentication_errors_are_redacted(self, sdk):
        sdk.return_value.Studio.side_effect = RuntimeError("Authorization: secret-value")
        with self.assertRaises(c.ConnectorError) as raised:
            c._studio({"owner": "trainer", "studio_name": "training", "teamspace_name": "models"})
        self.assertNotIn("secret-value", str(raised.exception))

    def test_paid_assertions_cannot_bypass_free_only_policy(self):
        config = dict(budget_confirmed=True, entitlement_confirmed=True, native_spend_limit=5)
        for spec in ({"free_only": True, "paid_cap": 10}, {"paid_cap": 10}):
            with self.assertRaisesRegex(c.ConnectorError, "free-only execution is blocked"):
                c._paid_policy(config, spec)
        c._paid_policy(config, {"free_only": False, "paid_cap": 10})
        for limit in (11, -1, float("nan"), True, "5"):
            with self.subTest(limit=limit), self.assertRaises(c.ConnectorError):
                c._paid_policy({**config, "native_spend_limit": limit}, {"free_only": False, "paid_cap": 10})

    @patch.object(c, "_cli", return_value="[]")
    @patch.object(c, "_sdk", return_value=Mock())
    def test_paid_cap_metadata_remains_user_reported(self, sdk, cli):
        result = c.probe("modal", {"native_spend_limit": 5, "budget_confirmed": True})
        self.assertTrue(result["account_verified"])
        self.assertFalse(result["budget"]["verified"])
        self.assertEqual(result["budget"]["source"], "user_reported")

    def embedded_archive(self, source):
        encoded = re.search(r"base64\.b64decode\('([^']+)'\)", source).group(1)
        return zipfile.ZipFile(io.BytesIO(base64.b64decode(encoded)))

    def test_guided_bundle_has_one_payload_and_never_claims_launch(self):
        for _ in range(2):
            handle = c.submit("colab", {}, self.spec, self.root)
        self.assertEqual(handle["state"], "awaiting_user")
        with zipfile.ZipFile(handle["bundle_path"]) as archive:
            self.assertEqual(set(archive.namelist()), {"training.ipynb", "README.md"})
            notebook = json.loads(archive.read("training.ipynb"))
        payload = self.embedded_archive("".join(notebook["cells"][1]["source"]))
        self.assertEqual(set(payload.namelist()), {"run.py", "spec.json", "multigpu/__init__.py", "multigpu/worker.py", "multigpu/artifacts.py"})

    def test_bundled_worker_error_exits_nonzero(self):
        self.spec.write_text(json.dumps(dict(recipe="starter", steps=-1)), encoding="utf-8")
        stage, _ = c._stage(self.spec, self.root)
        result = subprocess.run([sys.executable, str(stage / "run.py")], cwd=self.root, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        state = json.loads((self.root / "output" / "connector-state.json").read_text())
        self.assertEqual(state["state"], "error")

    def test_notebook_cell_runs_real_training_and_exports_model(self):
        bundle = c.build_bundle("colab", {}, self.spec, self.root)
        with zipfile.ZipFile(bundle) as archive:
            notebook = json.loads(archive.read("training.ipynb"))
        script = self.root / "cell.py"
        script.write_text("".join(notebook["cells"][1]["source"]), encoding="utf-8")
        result = subprocess.run([sys.executable, str(script)], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((self.root / "output" / "connector-state.json").read_text())["state"], "completed")
        self.assertTrue(verify_export(self.root / "output" / "export")["files"])

    def test_rebuilding_bundle_embeds_each_selected_dataset_once(self):
        dataset = self.root / "data.csv"
        dataset.write_text("1,1\n0,0\n", encoding="utf-8")
        self.spec.write_text(json.dumps(dict(recipe="starter", steps=3, dataset_path=str(dataset))), encoding="utf-8")
        for _ in range(3):
            bundle = c.build_bundle("colab", {}, self.spec, self.root)
        with zipfile.ZipFile(bundle) as archive:
            notebook = json.loads(archive.read("training.ipynb"))
        payload = self.embedded_archive("".join(notebook["cells"][1]["source"]))
        self.assertEqual([name for name in payload.namelist() if name.startswith("assets/")], ["assets/dataset_path.csv"])

    def test_embedded_runner_rejects_zip_traversal(self):
        stage, _ = c._stage(self.spec, self.root)
        source = c._embedded(stage)
        malicious = io.BytesIO()
        with zipfile.ZipFile(malicious, "w") as archive:
            archive.writestr("../escaped.txt", "bad")
        source = re.sub(r"base64\.b64decode\('[^']+'\)", "base64.b64decode(" + repr(base64.b64encode(malicious.getvalue()).decode()) + ")", source)
        script = self.root / "malicious.py"
        script.write_text(source, encoding="utf-8")
        result = subprocess.run([sys.executable, str(script)], cwd=self.root, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "escaped.txt").exists())

    def test_provider_wrappers_are_normalized_and_verified(self):
        source = self.root / "download" / "output" / "output"
        source.mkdir(parents=True)
        write_checkpoint(source, {"step": 3})
        export_artifacts({"model.json": "{}"}, source / "export")
        (source / "events.jsonl").write_text('{"type":"completed","step":3}\n', encoding="utf-8")
        result = c._publish_outputs(self.root / "download", self.root)
        self.assertEqual(result, self.root)
        self.assertEqual(load_checkpoint(self.root / "checkpoint.json")["step"], 3)
        self.assertEqual(verify_export(self.root / "export")["files"][0]["path"], "model.json")

    def test_provider_artifact_paths_reject_windows_and_posix_escape(self):
        for path in ("../secret", "a/../../secret", "a\\..\\secret", "C:/secret", "/secret", "a//b", "a/./b"):
            with self.subTest(path=path), self.assertRaises(c.ConnectorError):
                c._safe_relative(path)

    def test_wrong_job_checkpoint_does_not_replace_last_good_state(self):
        self.spec.write_text(json.dumps({"id": "expected", "recipe": "starter"}), encoding="utf-8")
        write_checkpoint(self.root, {"job_id": "expected", "recipe": "starter", "step": 2})
        source = self.root / "other-output"
        source.mkdir()
        write_checkpoint(source, {"job_id": "other", "recipe": "starter", "step": 3})
        with self.assertRaisesRegex(c.ConnectorError, "different training run"):
            c._publish_outputs(source, self.root)
        self.assertEqual(load_checkpoint(self.root / "checkpoint.json")["step"], 2)

    @patch.object(c, "_sdk")
    def test_modal_collection_rejects_remote_path_traversal(self, sdk):
        volume = sdk.return_value.Volume.from_name.return_value
        volume.iterdir.return_value = [SimpleNamespace(type=1, path="../escaped.txt")]
        with self.assertRaisesRegex(c.ConnectorError, "unsafe artifact path"):
            c.collect("modal", {}, {"volume": "test"}, self.root)
        volume.read_file.assert_not_called()
        self.assertFalse((self.root / "escaped.txt").exists())

    def test_live_log_metrics_are_deduplicated_at_coordinator_root(self):
        line = '2026-10-22T00:00:00 {"type":"metric","step":2,"loss":0.2}\n'
        c._ingest_logs(line, self.root)
        c._ingest_logs(line, self.root)
        self.assertEqual(len((self.root / "events.jsonl").read_text().splitlines()), 1)
        self.assertTrue((self.root / "worker.log").exists())

    @patch.object(c, "_sdk")
    def test_modal_pending_sdk_timeout_is_not_training_failure(self, sdk):
        ModalTimeout = type("TimeoutError", (Exception,), {})
        call = Mock()
        call.get.side_effect = ModalTimeout()
        volume = Mock()
        volume.read_file.side_effect = FileNotFoundError()
        sdk.return_value.FunctionCall.from_id.return_value = call
        sdk.return_value.Volume.from_name.return_value = volume
        state = c.poll("modal", {}, {"call_id": "fc-test", "volume": "test"}, self.root)
        self.assertEqual(state["state"], "running")

    def test_unsupported_stop_never_claims_cancellation(self):
        result = c.stop("kaggle", {}, {"kernel": "trainer/run"}, self.root)
        self.assertFalse(result["stopped"])
        self.assertEqual(result["state"], "stop_unavailable")

    @patch.object(c, "_studio")
    def test_lightning_stop_is_cooperative_and_does_not_stop_shared_studio(self, studio):
        result = c.stop("lightning", {}, {"remote_dir": "multigpu-runs/test"}, self.root)
        self.assertFalse(result["stopped"])
        studio.return_value.run.assert_called_once()
        studio.return_value.stop.assert_not_called()

    @patch.object(c, "_studio")
    def test_lightning_requires_dedicated_studio_shutdown_consent(self, studio):
        for config in ({}, {"dedicated_studio": True}, {"stop_studio_after_run": True}):
            with self.subTest(config=config), self.assertRaisesRegex(c.ConnectorError, "dedicated Studio"):
                c._submit_lightning(config, self.root)
        studio.assert_not_called()

    @patch.object(c, "_studio")
    def test_dedicated_lightning_stops_even_if_artifact_download_fails(self, studio):
        studio.return_value.download_folder.side_effect = RuntimeError("auth-header-secret")
        handle = dict(remote_dir="multigpu-runs/test", dedicated_studio=True, stop_studio_after_run=True)
        with self.assertRaises(c.ConnectorError) as raised:
            c.collect("lightning", {}, handle, self.root)
        self.assertNotIn("auth-header-secret", str(raised.exception))
        studio.return_value.stop.assert_called_once()
        self.assertTrue(handle["studio_stopped"])


if __name__ == "__main__":
    unittest.main()
