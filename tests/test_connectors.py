import base64
import io
import hashlib
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
        cli.side_effect = [cli.return_value, "- username: trainer\n"]
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
        quota = "resource,used,remaining,total,refreshAt\nGPU,0,0.1,1,tomorrow\n"
        cli.side_effect = [quota, "- username: trainer\n", "- username: trainer\n", quota, "pushed"]
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
        sdk.return_value.Workspace.from_context.return_value.name = "trainer"
        result = c.probe("modal", {"workspace": "trainer", "native_spend_limit": 5, "budget_confirmed": True})
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
        self.assertEqual(set(payload.namelist()), {"run.py", "spec.json", "multigpu/__init__.py", "multigpu/worker.py", "multigpu/artifacts.py", "multigpu/datasets.py", "multigpu/recipes.py", "multigpu/prediction.py"})

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
        sdk.return_value.Workspace.from_context.return_value.name = "trainer"
        volume = sdk.return_value.Volume.from_name.return_value
        volume.iterdir.return_value = [SimpleNamespace(type=1, path="../escaped.txt")]
        with self.assertRaisesRegex(c.ConnectorError, "unsafe artifact path"):
            c.collect("modal", {"workspace": "trainer"}, {"volume": "test"}, self.root)
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
        sdk.return_value.Workspace.from_context.return_value.name = "trainer"
        ModalTimeout = type("TimeoutError", (Exception,), {})
        call = Mock()
        call.get.side_effect = ModalTimeout()
        volume = Mock()
        volume.read_file.side_effect = FileNotFoundError()
        sdk.return_value.FunctionCall.from_id.return_value = call
        sdk.return_value.Volume.from_name.return_value = volume
        state = c.poll("modal", {"workspace": "trainer"}, {"call_id": "fc-test", "volume": "test"}, self.root)
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
        studio.return_value.status = "Stopped"
        studio.return_value.download_folder.side_effect = RuntimeError("auth-header-secret")
        handle = dict(remote_dir="multigpu-runs/test", dedicated_studio=True, stop_studio_after_run=True)
        with self.assertRaises(c.ConnectorError) as raised:
            c.collect("lightning", {}, handle, self.root)
        self.assertNotIn("auth-header-secret", str(raised.exception))
        studio.return_value.stop.assert_called_once()
        self.assertTrue(handle["studio_stopped"])

    def test_quota_admission_uses_one_checkpoint_reserve(self):
        for remaining in (None, -1, float("nan"), True, 149 / 3600):
            self.assertFalse(c.quota_admission({"remaining_hours": remaining})["eligible"])
        result = c.quota_admission({"remaining_hours": 150 / 3600}, 1000)
        self.assertTrue(result["eligible"])
        self.assertEqual((result["runtime_seconds"], result["timeout_seconds"]), (30, 60))

    @patch.object(c, "_cli")
    def test_prepare_is_stable_and_submission_is_idempotent(self, cli):
        quota = "resource,used,remaining,total,refreshAt\nGPU,0,1,1,tomorrow\n"
        cli.side_effect = lambda name, args, **kw: quota if args[0] == "quota" else "- username: trainer\n" if args[0] == "config" else "pushed"
        intent = c.prepare("kaggle", {"username": "trainer"}, self.spec, self.root)
        again = c.prepare("kaggle", {"username": "trainer"}, self.spec, self.root)
        self.assertEqual(intent, again)
        self.assertFalse(any(call.args[1][:2] == ["kernels", "push"] for call in cli.call_args_list))
        first = c.submit("kaggle", {"username": "trainer"}, self.spec, self.root, intent)
        second = c.submit("kaggle", {"username": "trainer"}, self.spec, self.root, intent)
        self.assertEqual(first, second)
        self.assertEqual(sum(call.args[1][:2] == ["kernels", "push"] for call in cli.call_args_list), 1)

    @patch.object(c, "_cli")
    def test_lost_acknowledgment_blocks_relaunch_and_can_reconnect(self, cli):
        quota = "resource,used,remaining,total,refreshAt\nGPU,0,1,1,tomorrow\n"
        def command(name, args, **kw):
            if args[0] == "quota": return quota
            if args[0] == "config": return "- username: trainer\n"
            if args[1] == "push": raise c.ConnectorError("Timed out", code="network", transient=True)
            if args[1] == "status": return 'trainer/run has status "running"'
            return ""
        cli.side_effect = command
        intent = c.prepare("kaggle", {"username": "trainer"}, self.spec, self.root)
        with self.assertRaises(c.ConnectorError) as first:
            c.submit("kaggle", {"username": "trainer"}, self.spec, self.root, intent)
        self.assertEqual(first.exception.code, "submission_unknown")
        with self.assertRaises(c.ConnectorError):
            c.submit("kaggle", {"username": "trainer"}, self.spec, self.root, intent)
        recovered = c.reconcile("kaggle", {"username": "trainer"}, intent, self.root)
        self.assertEqual(recovered["state"], "running")
        self.assertEqual(recovered["handle"]["kernel"], intent["kernel"])
        self.assertEqual(sum(call.args[1][:2] == ["kernels", "push"] for call in cli.call_args_list), 1)

    @patch.object(c, "_cli", return_value="- username: other-account\n")
    def test_kaggle_account_mismatch_blocks_before_push(self, cli):
        with self.assertRaises(c.ConnectorError) as raised:
            c._kaggle_identity({"username": "trainer"})
        self.assertEqual(raised.exception.code, "account_mismatch")
        self.assertEqual(cli.call_count, 1)

    @patch.object(c, "_sdk")
    def test_modal_native_workspace_mismatch_blocks_compute(self, sdk):
        sdk.return_value.Workspace.from_context.return_value.name = "other"
        with self.assertRaises(c.ConnectorError) as raised:
            c._modal_identity({"workspace": "intended"})
        self.assertEqual(raised.exception.code, "account_mismatch")
        sdk.return_value.App.assert_not_called()
        result = c.probe("modal", {"workspace": "intended"})
        self.assertFalse(result["ready"])
        self.assertEqual(result["connection_status"], "auth_required")

    @patch.object(c, "_sdk")
    def test_modal_early_pause_is_cooperative_and_cancel_is_explicit(self, sdk):
        sdk.return_value.Workspace.from_context.return_value.name = "trainer"
        handle = {"provider": "modal", "control": "control-test", "environment": "main", "identity": {"workspace": "trainer"}}
        result = c.stop("modal", {}, handle, self.root)
        self.assertEqual(result["state"], "pausing")
        self.assertFalse(result["stopped"])
        sdk.return_value.Dict.from_name.return_value.put.assert_called_once_with("pause", True)
        sdk.return_value.FunctionCall.from_id.assert_not_called()
        handle["call_id"] = "fc-test"
        result = c.cancel("modal", {}, handle, self.root)
        self.assertTrue(result["stopped"])
        sdk.return_value.FunctionCall.from_id.return_value.cancel.assert_called_once_with(terminate_containers=True)

    @patch.object(c, "poll", return_value={"state": "running"})
    @patch.object(c, "_sdk")
    def test_modal_reconcile_looks_up_control_without_creating_resources(self, sdk, poll):
        sdk.return_value.Workspace.from_context.return_value.name = "trainer"
        intent = dict(provider="modal", attempt_id="a" * 32, control="control-test", identity={"workspace": "trainer"})
        sdk.return_value.Dict.from_name.return_value.get.return_value = {**intent, "call_id": "fc-existing"}
        result = c.reconcile("modal", {}, intent, self.root)
        self.assertEqual(result["handle"]["call_id"], "fc-existing")
        self.assertNotIn("create_if_missing", sdk.return_value.Dict.from_name.call_args.kwargs)
        sdk.return_value.App.assert_not_called()

    @patch.object(c, "_sdk")
    def test_unresolved_modal_reconcile_never_spawns(self, sdk):
        sdk.return_value.Workspace.from_context.return_value.name = "trainer"
        sdk.return_value.Dict.from_name.return_value.get.return_value = None
        intent = dict(provider="modal", attempt_id="b" * 32, control="control-test", identity={"workspace": "trainer"})
        result = c.reconcile("modal", {}, intent, self.root)
        self.assertEqual(result["state"], "unknown")
        sdk.return_value.App.assert_not_called()

    @patch.object(c, "_modal_snapshot", return_value={"state": "running"})
    @patch.object(c, "_sdk")
    def test_modal_network_failure_does_not_become_training_error(self, sdk, snapshot):
        sdk.return_value.Workspace.from_context.return_value.name = "trainer"
        sdk.return_value.FunctionCall.from_id.return_value.get.side_effect = ConnectionError("private-key")
        with self.assertRaises(c.ConnectorError) as raised:
            c.poll("modal", {"workspace": "trainer"}, {"call_id": "fc-test", "volume": "test"}, self.root)
        self.assertTrue(raised.exception.transient)
        self.assertNotIn("private-key", str(raised.exception))

    @patch.object(c, "_studio")
    @patch.object(c, "_sdk")
    def test_lightning_applies_requested_machine_and_records_actual(self, sdk, studio):
        cpu, gpu = SimpleNamespace(name="CPU"), SimpleNamespace(name="T4")
        selected = studio.return_value
        selected.machine, selected.status = cpu, "Running"
        selected.owner, selected.teamspace, selected.name = SimpleNamespace(name="trainer"), SimpleNamespace(name="models"), "training"
        selected.switch_machine.side_effect = lambda machine: setattr(selected, "machine", machine)
        sdk.return_value.Machine.from_str.return_value = gpu
        intent = dict(provider="lightning", attempt_id="c" * 32, requested_machine="T4", remote_dir="multigpu-runs/test")
        selected.run.side_effect = ["", "[]", json.dumps({"pid": 42, "attempt_id": intent["attempt_id"]})]
        stage, _ = c._stage(self.spec, self.root)
        handle = c._submit_lightning(dict(user="trainer", teamspace="models", studio="training", machine="T4", dedicated_studio=True, stop_studio_after_run=True), stage, intent)
        selected.switch_machine.assert_called_once_with(gpu)
        selected.start.assert_not_called()
        self.assertEqual(handle["actual_resource"]["machine"], "T4")
        launcher = selected.run.call_args.args[0]
        self.assertIn("launch.json", launcher)
        self.assertIn("fsync", launcher)

    def test_lightning_existing_scope_is_immutable_after_settings_change(self):
        handle = dict(provider="lightning", identity=dict(owner="original", owner_type="organization", teamspace="models", studio="training"))
        bound = c._bound_config(dict(user="different", studio="different", teamspace="different", machine="CPU"), handle)
        self.assertEqual((bound["organization"], bound["studio"], bound["teamspace"]), ("original", "training", "models"))
        self.assertIsNone(bound["user"])

    @patch.object(c, "_studio")
    def test_lightning_reconcile_does_not_restart_stopped_studio(self, studio):
        selected = studio.return_value
        selected.status = "Stopped"
        selected.owner, selected.teamspace, selected.name = SimpleNamespace(name="trainer"), SimpleNamespace(name="models"), "training"
        intent = dict(provider="lightning", attempt_id="d" * 32, remote_dir="multigpu-runs/test", identity=dict(owner="trainer", owner_type="user", teamspace="models", studio="training"))
        state = c.reconcile("lightning", {}, intent, self.root)
        self.assertEqual(state["state"], "unknown")
        selected.start.assert_not_called()
        selected.run.assert_not_called()

    def test_image_folder_and_verified_weights_are_staged(self):
        dataset = self.root / "pictures"
        (dataset / "cat").mkdir(parents=True)
        (dataset / "cat" / "photo.jpg").write_bytes(b"image")
        weights = self.root / "official.pth"
        weights.write_bytes(b"prepared-weights")
        digest = hashlib.sha256(weights.read_bytes()).hexdigest()
        self.spec.write_text(json.dumps(dict(recipe="image", dataset_path=str(dataset), weights_path=str(weights), weights_sha256=digest)), encoding="utf-8")
        stage, spec = c._stage(self.spec, self.root)
        self.assertEqual((stage / spec["dataset_path"] / "cat" / "photo.jpg").read_bytes(), b"image")
        self.assertEqual((stage / spec["weights_path"]).read_bytes(), b"prepared-weights")
        self.assertIn("weights_path", c._RUNNER)
        self.assertTrue((stage / "multigpu" / "recipes.py").exists())
        self.spec.write_text(json.dumps(dict(recipe="image", weights_path=str(weights), weights_sha256="0" * 64)), encoding="utf-8")
        with self.assertRaisesRegex(c.ConnectorError, "checksum"):
            c._stage(self.spec, self.root)

    @patch.object(c, "_MAX_STAGE_BYTES", 1)
    def test_oversize_payload_is_rejected_before_upload(self):
        data = self.root / "data.csv"
        data.write_text("0,1\n", encoding="utf-8")
        self.spec.write_text(json.dumps(dict(dataset_path=str(data))), encoding="utf-8")
        with self.assertRaises(c.ConnectorError) as raised:
            c._stage(self.spec, self.root)
        self.assertEqual(raised.exception.code, "staging_limit")

    def test_checkpoint_collection_keeps_verified_weights_for_resume(self):
        output = self.root / "download"
        (output / "assets").mkdir(parents=True)
        weights = output / "assets" / "base_weights.pth"
        weights.write_bytes(b"weights")
        digest = hashlib.sha256(weights.read_bytes()).hexdigest()
        write_checkpoint(output, dict(recipe="image", payload=dict(weights_asset="assets/base_weights.pth", weights_sha256=digest)))
        c._publish_outputs(output, self.root)
        self.assertEqual((self.root / "assets" / "base_weights.pth").read_bytes(), b"weights")

    def test_kaggle_status_does_not_match_words_in_kernel_name(self):
        self.assertEqual(c._kaggle_state('trainer/error-example has status "running"'), "running")
        self.assertEqual(c._kaggle_state('trainer/run has status "complete"'), "completed")
        self.assertEqual(c._kaggle_state("unreadable status"), "unknown")

    @patch.object(c, "_studio")
    @patch.object(c, "_sdk")
    def test_lightning_setup_failure_stops_compute_without_claiming_a_launch(self, sdk, studio):
        selected = studio.return_value
        gpu = SimpleNamespace(name="T4")
        selected.machine, selected.status = gpu, "Running"
        selected.owner, selected.teamspace, selected.name = SimpleNamespace(name="trainer"), SimpleNamespace(name="models"), "training"
        sdk.return_value.Machine.from_str.return_value = gpu
        selected.run.side_effect = ["", '["torch"]']
        selected.stop.side_effect = lambda: setattr(selected, "status", "Stopped")
        stage, _ = c._stage(self.spec, self.root)
        self.spec.write_text(json.dumps(dict(recipe="pytorch")), encoding="utf-8")
        stage, _ = c._stage(self.spec, self.root)
        intent = dict(provider="lightning", attempt_id="e" * 32, requested_machine="T4", remote_dir="multigpu-runs/test")
        with self.assertRaises(c.ConnectorError) as raised:
            c._submit_lightning(dict(user="trainer", teamspace="models", studio="training", dedicated_studio=True, stop_studio_after_run=True), stage, intent)
        self.assertEqual(raised.exception.code, "preflight_failed")
        self.assertEqual(intent["cleanup_status"], "verified")
        selected.stop.assert_called_once()
        self.assertEqual(selected.run.call_count, 2)

    def test_studio_shutdown_requires_provider_readback(self):
        studio = Mock(status="Stopping")
        handle = {}
        with self.assertRaises(c.ConnectorError) as raised:
            c._stop_studio(studio, handle)
        self.assertEqual(raised.exception.code, "cleanup_pending")
        self.assertFalse(handle.get("studio_stopped", False))
        self.assertEqual(handle["cleanup_status"], "pending")


if __name__ == "__main__":
    unittest.main()
