"""Execution decisions and recovery evidence; no authenticated provider calls."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from multigpu import connectors as c, recipes
from multigpu.catalog import CATALOG
from multigpu.planner import preview, validate_spec
from multigpu.worker import TrainingContext, run


class ExecutionSafetyTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / ".test-runs"
        scratch.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.spec_path = self.root / "spec.json"
        self.spec_path.write_text(json.dumps({"id": "job", "recipe": "starter", "steps": 3, "runtime_seconds": 30}), encoding="utf-8")

    def prepare_kaggle(self):
        with patch.object(c, "_quota", return_value={"remaining_hours": 1}), patch.object(c, "_kaggle_identity", return_value={"username": "trainer", "verified": True}):
            return c.prepare("kaggle", {"username": "trainer"}, self.spec_path, self.root)

    def test_mixed_selection_resolves_one_executable_plan_in_both_orders(self):
        for providers in (["colab", "local"], ["local", "colab"]):
            plan = preview(validate_spec({"providers": providers}, self.root), CATALOG, {}, {})
            self.assertEqual((plan["selected_provider"], plan["execution_mode"]), ("local", "automatic"))
            self.assertEqual(plan["allowed_actions"], ["launch"])
            self.assertTrue(plan["eligible"])
            self.assertFalse(plan["preparation_eligible"])
        plan = preview(validate_spec({"providers": ["colab", "local"], "guided": True}, self.root), CATALOG, {}, {})
        self.assertEqual((plan["selected_provider"], plan["execution_mode"]), ("colab", "guided"))
        self.assertEqual(plan["allowed_actions"], ["prepare_guided"])
        self.assertTrue(plan["preparation_eligible"])
        self.assertFalse(plan["eligible"])

    def test_guided_manual_only_and_development_admission_are_distinct(self):
        for pid in ("colab", "jupyterhub"):
            plan = preview(validate_spec({"providers": [pid]}, self.root), CATALOG, {}, {})
            self.assertEqual((plan["selected_provider"], plan["execution_mode"]), (pid, "guided"))
        for pid in ("codespaces", "fastero", "local"):
            plan = preview(validate_spec({"providers": [pid], "guided": True}, self.root), CATALOG, {}, {})
            self.assertIsNone(plan["selected_provider"])
            self.assertEqual(plan["allowed_actions"], [])
        blocked = preview(validate_spec({"providers": ["modal"]}, self.root), CATALOG, {"modal": {"ready": True}}, {})
        self.assertIsNone(blocked["execution_mode"])
        self.assertEqual(blocked["decisions"][0]["blocker_code"], "free_cap_unverified")

    def test_free_only_guided_plan_blocks_known_paid_gpu_and_metered_compute(self):
        for pid in ("deepnote", "hex", "datalore"):
            free = validate_spec({"providers": [pid], "recipe": "pytorch", "device": "cuda", "guided": True}, self.root)
            blocked = preview(free, CATALOG, {}, {})
            self.assertFalse(blocked["preparation_eligible"])
            self.assertEqual(blocked["decisions"][0]["blocker_code"], "paid_gpu_required")
            paid = preview({**free, "free_only": False, "paid_cap": 5}, CATALOG, {}, {})
            self.assertTrue(paid["preparation_eligible"])
            cpu = preview({**free, "recipe": "starter", "device": "cpu"}, CATALOG, {}, {})
            self.assertTrue(cpu["preparation_eligible"])
        enterprise = preview(validate_spec({"providers": ["colab-enterprise"], "guided": True}, self.root), CATALOG, {}, {})
        self.assertFalse(enterprise["preparation_eligible"])

    def test_review_reports_device_and_actual_limit_enforcement_scope(self):
        hardware = {"torch_available": True, "cuda_available": True, "gpus": [{}, {}]}
        local = preview(validate_spec({"recipe": "pytorch", "gpu_index": 1}, self.root), CATALOG, {}, hardware)
        self.assertEqual((local["resolved_device"], local["limit_enforcement"]), ("cuda:1", "local"))
        guided = preview(validate_spec({"providers": ["colab"], "recipe": "pytorch", "device": "cuda"}, self.root), CATALOG, {}, {})
        self.assertEqual((guided["resolved_device"], guided["limit_enforcement"]), ("runtime_gpu", "manual_unverified"))
        self.assertFalse(any("when allowance" in warning for warning in guided["warnings"]))
        kaggle = preview(validate_spec({"providers": ["kaggle"]}, self.root), CATALOG, {"kaggle": {"ready": True, "quota": {"remaining_hours": 1}}}, {})
        self.assertEqual(kaggle["limit_enforcement"], "provider_quota")
        fake_cap = {"modal": {"ready": True, "budget": {"verified": True, "max_out_of_pocket": 0}}}
        self.assertFalse(preview(validate_spec({"providers": ["modal"]}, self.root), CATALOG, fake_cap, {})["eligible"])

    def test_only_matching_manual_resolution_releases_unknown_account(self):
        spec = validate_spec({"providers": ["kaggle"]}, self.root)
        connection = {"kaggle": {"ready": True, "quota": {"remaining_hours": 1}}}
        job = {"provider_id": "kaggle", "status": "interrupted", "submission_status": "unknown", "spec": {"attempt_id": "attempt"}}
        for resolution in ({}, {"outcome": "absent", "attempt_id": "different"}, {"outcome": "running", "attempt_id": "attempt"}, {"outcome": "absent", "attempt_id": "attempt"}):
            result = preview(spec, CATALOG, connection, {}, [{**job, "submission_resolution": resolution}])
            self.assertEqual(result["decisions"][0]["blocker_code"], "account_reserved")
            self.assertFalse(result["decisions"][0]["preparation_eligible"])
        for outcome in ("stopped", "absent"):
            result = preview(spec, CATALOG, connection, {}, [{**job, "submission_resolution": {"outcome": outcome, "source": "user_confirmed", "attempt_id": "attempt"}}])
            self.assertTrue(result["eligible"])
        no_attempt = {**job, "spec": {}, "submission_resolution": {"outcome": "absent", "attempt_id": None}}
        self.assertFalse(preview(spec, CATALOG, connection, {}, [no_attempt])["eligible"])

    def test_custom_v2_folder_is_accepted_but_legacy_is_not(self):
        data = self.root / "data"
        data.mkdir()
        script = self.root / "train.py"
        script.write_text("def train(ctx): pass", encoding="utf-8")
        raw = {"recipe": "custom", "dataset_path": str(data), "script_path": str(script), "trust_script": True}
        self.assertEqual(validate_spec(raw, self.root)["dataset_path"], str(data.resolve()))
        with self.assertRaises(ValueError):
            validate_spec({**raw, "recipe_version": 1}, self.root)

    def test_lightning_cleanup_reserves_account_until_matching_stopped_confirmation(self):
        spec = validate_spec({"providers": ["lightning"], "free_only": False, "paid_cap": 10}, self.root)
        connection = {"lightning": {"ready": True, "config": {"entitlement_confirmed": True, "budget_confirmed": True, "native_spend_limit": 5, "dedicated_studio": True, "stop_studio_after_run": True}}}
        job = {"provider_id": "lightning", "status": "error", "submission_status": "not_started", "cleanup_status": "failed", "spec": {"attempt_id": "attempt"}}
        for resolution in ({}, {"outcome": "absent", "source": "user_confirmed", "attempt_id": "attempt"}, {"outcome": "stopped", "source": "user_confirmed", "attempt_id": "old"}):
            self.assertFalse(preview(spec, CATALOG, connection, {}, [{**job, "submission_resolution": resolution}])["eligible"])
        job["submission_resolution"] = {"outcome": "stopped", "source": "user_confirmed", "attempt_id": "attempt"}
        self.assertTrue(preview(spec, CATALOG, connection, {}, [job])["eligible"])

    def test_fresh_quota_failure_is_not_started_and_never_claims_launch(self):
        intent = self.prepare_kaggle()
        with patch.object(c, "_quota", return_value={"remaining_hours": 0}), patch.object(c, "_kaggle_identity", return_value=intent["identity"]), patch.object(c, "_cli") as cli:
            with self.assertRaises(c.ConnectorError) as raised:
                c.submit("kaggle", {}, self.spec_path, self.root, intent)
        self.assertEqual((raised.exception.code, raised.exception.submission_outcome), ("quota", "not_started"))
        self.assertEqual(raised.exception.submission_evidence["stage"], "preflight")
        self.assertFalse(c._record_path(intent, self.root).exists())
        cli.assert_not_called()

    def test_local_payload_failure_is_proven_not_started_and_new_intent_is_fresh(self):
        intent = self.prepare_kaggle()
        with patch.object(c, "_quota", return_value={"remaining_hours": 1}), patch.object(c, "_kaggle_identity", return_value=intent["identity"]), patch.object(c, "_embedded", side_effect=OSError("disk full")), patch.object(c, "_cli") as cli:
            with self.assertRaises(c.ConnectorError) as raised:
                c.submit("kaggle", {}, self.spec_path, self.root, intent)
        self.assertEqual(raised.exception.submission_outcome, "not_started")
        self.assertEqual(json.loads(c._record_path(intent, self.root).read_text())["submission_status"], "not_started")
        cli.assert_not_called()
        self.assertNotEqual(self.prepare_kaggle()["attempt_id"], intent["attempt_id"])

    def test_client_initialization_proof_differs_from_ambiguous_provider_error(self):
        for output, status, expected in (("unrecognized arguments: --accelerator", 2, "not_started"),
                                         ("No module named kaggle", 1, "not_started"),
                                         ("Permission kernels.get denied", 1, "unknown"),
                                         ("GPU quota limit exceeded", 1, "unknown"),
                                         ("Kernel push error: unrecognized arguments: --accelerator", 1, "unknown")):
            with self.subTest(output=output), tempfile.TemporaryDirectory(dir=self.root) as folder:
                folder = Path(folder)
                spec = folder / "spec.json"
                spec.write_bytes(self.spec_path.read_bytes())
                with patch.object(c, "_quota", return_value={"remaining_hours": 1}), patch.object(c, "_kaggle_identity", return_value={"username": "trainer"}), patch.object(c.subprocess, "run", return_value=SimpleNamespace(returncode=status, stdout="", stderr=output)) as client:
                    intent = c.prepare("kaggle", {}, spec, folder)
                    with self.assertRaises(c.ConnectorError) as raised:
                        c.submit("kaggle", {}, spec, folder, intent)
                    self.assertEqual(raised.exception.submission_outcome, expected)
                    self.assertIn(output, raised.exception.diagnostic["stderr"])
                    with self.assertRaises(c.ConnectorError) as duplicate:
                        c.submit("kaggle", {}, spec, folder, intent)
                    self.assertEqual(duplicate.exception.submission_outcome, expected)
                    self.assertEqual(client.call_count, 1)

    def test_timeout_after_push_boundary_stays_unknown(self):
        intent = self.prepare_kaggle()
        with patch.object(c, "_quota", return_value={"remaining_hours": 1}), patch.object(c, "_kaggle_identity", return_value=intent["identity"]), patch.object(c.subprocess, "run", side_effect=subprocess.TimeoutExpired("kaggle", 120, output=b"upload begun")):
            with self.assertRaises(c.ConnectorError) as raised:
                c.submit("kaggle", {}, self.spec_path, self.root, intent)
        error = raised.exception
        self.assertEqual((error.code, error.submission_outcome), ("submission_unknown", "unknown"))
        self.assertIn("upload begun", error.diagnostic["stdout"])

    def test_failed_client_spawn_is_proven_not_started_through_submit(self):
        intent = self.prepare_kaggle()
        with patch.object(c, "_quota", return_value={"remaining_hours": 1}), patch.object(c, "_kaggle_identity", return_value=intent["identity"]), patch.object(c.subprocess, "run", side_effect=OSError("No interpreter")):
            with self.assertRaises(c.ConnectorError) as raised:
                c.submit("kaggle", {}, self.spec_path, self.root, intent)
        self.assertEqual(raised.exception.submission_outcome, "not_started")
        self.assertEqual(raised.exception.submission_evidence["stage"], "client_spawn")
        self.assertEqual(json.loads(c._record_path(intent, self.root).read_text())["submission_status"], "not_started")

    def test_confirmed_acknowledgment_survives_redundant_journal_failure(self):
        intent = self.prepare_kaggle()
        original = c._json_write
        def write(path, value):
            if Path(path) == c._record_path(intent, self.root) and value.get("submission_status") == "confirmed":
                raise OSError("disk full")
            return original(path, value)
        with patch.object(c, "_quota", return_value={"remaining_hours": 1}), patch.object(c, "_kaggle_identity", return_value=intent["identity"]), patch.object(c, "_cli", return_value="pushed"), patch.object(c, "_json_write", side_effect=write):
            handle = c.submit("kaggle", {}, self.spec_path, self.root, intent)
        self.assertEqual(handle["submission_status"], "confirmed")
        self.assertEqual(handle["kernel"], intent["kernel"])
        self.assertIn("journal_warning", handle)

    def test_corrupt_existing_journal_is_never_pristine(self):
        intent = self.prepare_kaggle()
        c._record_path(intent, self.root).write_text("{partial", encoding="utf-8")
        for action in (lambda: c.submit("kaggle", {}, self.spec_path, self.root, intent), lambda: self.prepare_kaggle()):
            with self.assertRaises(c.ConnectorError) as raised:
                action()
            self.assertEqual(raised.exception.submission_outcome, "unknown")
        c._json_write(c._record_path(intent, self.root), {"submission_status": "confirmed"})
        with self.assertRaises(c.ConnectorError) as raised:
            c.submit("kaggle", {}, self.spec_path, self.root, intent)
        self.assertEqual(raised.exception.submission_outcome, "unknown")

    def save_intent(self, provider, **fields):
        attempt = "a" * 32
        intent = {"provider": provider, "attempt_id": attempt, "job_id": "job", "spec_sha256": hashlib.sha256(self.spec_path.read_bytes()).hexdigest(), **fields}
        c._json_write(c._record_path(intent, self.root), {**intent, "submission_status": "unknown"})
        return intent

    def test_attach_kaggle_only_exact_saved_kernel_and_account(self):
        intent = self.save_intent("kaggle", identity={"username": "trainer"}, kernel="trainer/multigpu-" + "a" * 20)
        with patch.object(c, "_kaggle_identity", return_value=intent["identity"]) as identity, patch.object(c, "poll", return_value={"state": "running"}) as poll, patch.object(c, "submit") as submit:
            for entered in (intent["kernel"], "https://www.kaggle.com/code/" + intent["kernel"]):
                result = c.attach("kaggle", {}, entered, intent, self.root)
                self.assertEqual(result["handle"]["kernel"], intent["kernel"])
                self.assertEqual(result["submission_status"], "confirmed")
            for entered in ("trainer/other-model", "https://evil.example/code/" + intent["kernel"]):
                with self.assertRaises(c.ConnectorError):
                    c.attach("kaggle", {}, entered, intent, self.root)
            self.assertEqual(poll.call_count, 2)
            submit.assert_not_called()
            self.assertEqual(identity.call_args.args[0]["username"], "trainer")
        changed = {**intent, "identity": {"username": "other"}}
        with self.assertRaises(c.ConnectorError):
            c.attach("kaggle", {}, intent["kernel"], changed, self.root)

    def test_attach_modal_requires_matching_provider_control_record(self):
        intent = self.save_intent("modal", identity={"workspace": "trainer"}, control="control", volume="volume", environment="main", app_name="app")
        modal = Mock()
        modal.Workspace.from_context.return_value.name = "trainer"
        control = modal.Dict.from_name.return_value
        with patch.object(c, "_sdk", return_value=modal), patch.object(c, "poll", return_value={"state": "running"}) as poll:
            control.get.return_value = {**intent, "attempt_id": "b" * 32, "call_id": "fc-exact"}
            with self.assertRaises(c.ConnectorError):
                c.attach("modal", {}, "fc-exact", intent, self.root)
            poll.assert_not_called()
            control.get.return_value = {**intent, "call_id": "fc-exact"}
            result = c.attach("modal", {}, "fc-exact", intent, self.root)
            self.assertEqual(result["handle"]["call_id"], "fc-exact")
            self.assertNotIn("create_if_missing", modal.Dict.from_name.call_args.kwargs)
            control.put.assert_not_called()
            modal.App.assert_not_called()
            with self.assertRaises(c.ConnectorError):
                c.attach("modal", {}, "fc-unrelated", intent, self.root)

    def test_attach_lightning_reads_exact_launch_record_without_starting_studio(self):
        identity = {"owner": "trainer", "owner_type": "user", "user": "trainer", "teamspace": "models", "studio": "dedicated"}
        intent = self.save_intent("lightning", identity=identity, remote_dir="multigpu-runs/" + "a" * 32)
        studio = Mock(status="Running", name="dedicated")
        studio.owner, studio.teamspace = SimpleNamespace(name="trainer"), SimpleNamespace(name="models")
        studio.name = "dedicated"
        studio.run.return_value = json.dumps({"attempt_id": intent["attempt_id"], "pid": 42})
        with patch.object(c, "_studio", return_value=studio), patch.object(c, "poll", return_value={"state": "paused"}):
            result = c.attach("lightning", {}, "42", intent, self.root)
            self.assertEqual(result["handle"]["pid"], 42)
            self.assertIn("launch.json", studio.run.call_args.args[0])
            for method in (studio.start, studio.switch_machine, studio.upload_file, studio.stop):
                method.assert_not_called()
            with self.assertRaises(c.ConnectorError):
                c.attach("lightning", {}, "43", intent, self.root)
            studio.status = "Stopped"
            studio.run.reset_mock()
            with self.assertRaises(c.ConnectorError):
                c.attach("lightning", {}, "42", intent, self.root)
            studio.run.assert_not_called()

    def fake_torch(self, devices=2):
        torch = Mock()
        torch.cuda.is_available.return_value = True
        torch.cuda.device_count.return_value = devices
        torch.cuda.get_rng_state.return_value.tolist.return_value = [1, 2]
        torch.get_rng_state.return_value.tolist.return_value = [3, 4]
        return torch

    def test_selected_local_gpu_and_remote_host_index_are_resolved_separately(self):
        for provider, requested, expected in (("local", 1, 1), ("kaggle", 1, 0), ("colab", 255, 0)):
            torch = self.fake_torch()
            ctx = SimpleNamespace(spec={"device": "cuda", "gpu_index": requested, "execution_provider": provider, "seed": 42})
            with patch.dict(sys.modules, {"torch": torch}):
                recipes._torch(ctx)
            self.assertEqual((ctx.device, ctx.gpu_index), (f"cuda:{expected}", expected))
            torch.cuda.set_device.assert_called_once_with(expected)
        torch = self.fake_torch(devices=1)
        with patch.dict(sys.modules, {"torch": torch}), self.assertRaisesRegex(RuntimeError, "index is unavailable"):
            recipes._torch(SimpleNamespace(spec={"device": "cuda", "gpu_index": 1, "seed": 42}))
        torch.cuda.set_device.assert_not_called()

    def test_mlp_allocation_and_checkpoint_rng_use_selected_gpu(self):
        torch = self.fake_torch()
        from unittest.mock import MagicMock
        torch.tensor.return_value = MagicMock()
        model = torch.nn.Sequential.return_value
        model.to.return_value = model
        model.state_dict.return_value = {}
        optimizer = torch.optim.SGD.return_value
        optimizer.state_dict.return_value = {"state": {}, "param_groups": []}
        torch.nn.functional.binary_cross_entropy_with_logits.return_value.detach.return_value.item.return_value = .25
        ctx = SimpleNamespace(spec={"recipe_version": 1, "device": "cuda", "gpu_index": 1, "seed": 42, "steps": 2, "batch_size": 1, "learning_rate": .05}, step=1,
                              resume_state={"model": {}, "optimizer": {"state": {}, "param_groups": []}, "rng_state": random.Random(42).getstate(), "torch_rng": [3, 4], "cuda_rng": [1, 2]}, checkpoint=Mock(), should_stop=lambda: False, maybe_checkpoint=Mock(), performance=SimpleNamespace(samples=0))
        def report(loss):
            ctx.step += 1
        ctx.report = report
        with patch.dict(sys.modules, {"torch": torch}), patch.object(recipes, "_finish_tabular"):
            recipes.train_pytorch(ctx, {"features": [[1.]], "labels": [1.], "train_indices": [0]})
        model.to.assert_called_once_with("cuda:1")
        self.assertFalse(any("device" in call.kwargs for call in torch.tensor.call_args_list))
        self.assertEqual([call.args[0] for call in torch.tensor.return_value.__getitem__.return_value.to.call_args_list], ["cuda:1", "cuda:1"])
        self.assertEqual(torch.cuda.set_rng_state.call_args.kwargs["device"], "cuda:1")
        self.assertTrue(all(call.kwargs["device"] == "cuda:1" for call in torch.cuda.get_rng_state.call_args_list))

    def test_telemetry_reports_resolved_device_and_active_gpu(self):
        ctx = TrainingContext({"recipe": "pytorch", "steps": 2}, self.root / "output")
        self.addCleanup(ctx.close)
        ctx.device, ctx.gpu_index = "cuda:1", 1
        ctx._last_summary -= 301
        with contextlib.redirect_stdout(io.StringIO()):
            ctx._tick()
        event = json.loads((ctx.output_dir / "events.jsonl").read_text().splitlines()[-1])
        self.assertEqual((event["device"], event["gpu_index"], event["active_gpu_count"]), ("cuda:1", 1, 1))

    def test_custom_explicit_cuda_receives_resolved_context_device(self):
        script = self.root / "custom.py"
        script.write_text("def train(ctx):\n ctx.report(.1)\n ctx.checkpoint({'device': ctx.device})\n ctx.export({'model.txt': ctx.device})\n", encoding="utf-8")
        torch = self.fake_torch()
        with patch.dict(sys.modules, {"torch": torch}), contextlib.redirect_stdout(io.StringIO()):
            result = run({"id": "custom", "recipe": "custom", "script_path": str(script), "steps": 1, "device": "cuda", "gpu_index": 1}, self.root / "custom-output")
        self.assertEqual((result["status"], result["device"]), ("completed", "cuda:1"))

    def test_staged_remote_inputs_do_not_reuse_laptop_gpu_index(self):
        spec = json.loads(self.spec_path.read_text())
        spec.update(gpu_index=1, execution_provider="local")
        self.spec_path.write_text(json.dumps(spec), encoding="utf-8")
        stage, staged = c._stage_for_provider("colab", self.spec_path, self.root)
        self.assertEqual((staged["execution_provider"], staged["gpu_index"]), ("colab", 0))
        self.assertEqual(json.loads((stage / "spec.json").read_text())["gpu_index"], 0)

    def test_cancelled_weights_download_keeps_no_partial_asset(self):
        cancelled = [False]
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        def read(size):
            cancelled[0] = True
            return b"partial asset"
        response.read.side_effect = read
        cache = self.root / "weights"
        with patch.object(recipes.urllib.request, "urlopen", return_value=response), self.assertRaises(InterruptedError):
            recipes.prepare_image_weights(cache, allow_download=True, cancelled=lambda: cancelled[0])
        response.read.assert_called_once()
        self.assertEqual(list(cache.iterdir()), [])

    def test_cancel_after_download_before_commit_keeps_no_target(self):
        cancelled = [False]
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        chunks = iter((b"asset bytes", b""))
        def read(size):
            chunk = next(chunks)
            if not chunk:
                cancelled[0] = True
            return chunk
        response.read.side_effect = read
        cache = self.root / "weights"
        with patch.object(recipes.urllib.request, "urlopen", return_value=response), self.assertRaises(InterruptedError):
            recipes.prepare_image_weights(cache, allow_download=True, cancelled=lambda: cancelled[0])
        self.assertEqual(list(cache.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
