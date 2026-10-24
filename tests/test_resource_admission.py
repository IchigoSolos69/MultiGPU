"""Reviewed allocation and pre-auth admission; no provider account or installs."""
from pathlib import Path
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import MagicMock, patch

from multigpu.catalog import CATALOG, get_provider
from multigpu.environment import EnvironmentSetup, resolve_preparation
from multigpu.onboarding import provider_setup
from multigpu.planner import preview, validate_spec


class ResourceAdmissionTests(unittest.TestCase):
    def plan(self, raw, config=None, hardware=None, ready=True):
        pid = raw.get("providers", ["modal"])[0]
        raw = {"providers": [pid], "free_only": False, "paid_cap": 10, **raw}
        connection = {pid: {"ready": ready, "config": {"entitlement_confirmed": True, "budget_confirmed": True,
                                                       "native_spend_limit": 5, **(config or {})}}}
        return preview(validate_spec(raw, Path.cwd()), CATALOG, connection, hardware or {})

    def test_unauthenticated_modal_exposes_all_independent_blockers(self):
        result = self.plan({"recipe": "pytorch", "device": "cuda", "free_only": True}, ready=False)
        decision = result["decisions"][0]
        self.assertEqual({b["code"] for b in decision["blockers"]},
                         {"connection_not_ready", "free_cap_unverified", "gpu_not_configured"})
        self.assertFalse(result["eligible"])
        self.assertFalse(decision["preparation_eligible"])
        self.assertTrue(all(b["scope"] == "automatic" for b in decision["blockers"]))

    def test_modal_cpu_allocations_omit_configured_gpu(self):
        for settings in ({"recipe": "starter"}, {"recipe": "pytorch", "device": "cpu"},
                         {"recipe": "image", "device": "cpu"}):
            result = self.plan(settings, {"gpu": "T4"})
            self.assertTrue(result["eligible"])
            self.assertEqual(result["resolved_resource"],
                             {"provider_id": "modal", "device": "cpu", "gpu": None, "gpu_index": None})
            self.assertEqual(result["resolved_device"], "cpu")

    def test_modal_auto_resolves_gpu_only_when_configured(self):
        gpu = self.plan({"recipe": "pytorch", "device": "auto"}, {"gpu": "T4"})
        self.assertEqual(gpu["resolved_resource"]["gpu"], "T4")
        self.assertEqual(gpu["resolved_resource"]["device"], "cuda")
        cpu = self.plan({"recipe": "pytorch", "device": "auto"})
        self.assertEqual(cpu["resolved_resource"]["device"], "cpu")
        self.assertEqual(cpu["resolved_device"], "cpu")

    def test_kaggle_and_lightning_gpu_allocation_remains_visible_for_cpu_model(self):
        result = self.plan({"providers": ["kaggle"], "recipe": "starter"})
        resource = result["decisions"][0]["resolved_resource"]
        self.assertEqual((resource["device"], resource["gpu"]), ("cpu", "NvidiaTeslaT4"))
        for machine, allocation in (("T4", "T4"), ("CPU", None), ("", "unknown")):
            result = self.plan({"providers": ["lightning"], "recipe": "starter"}, {"machine": machine})
            resource = result["decisions"][0]["resolved_resource"]
            self.assertEqual((resource["device"], resource["gpu"]), ("cpu", allocation))

    def test_modal_explicit_cuda_without_gpu_never_falls_back_to_cpu(self):
        result = self.plan({"recipe": "pytorch", "device": "cuda"})
        self.assertFalse(result["eligible"])
        self.assertIn("gpu_not_configured", {b["code"] for b in result["decisions"][0]["blockers"]})
        self.assertEqual(result["decisions"][0]["resolved_resource"]["device"], "cuda")

    def test_fake_billing_evidence_does_not_enable_modal_free_only(self):
        spec = validate_spec({"providers": ["modal"], "recipe": "pytorch", "free_only": True}, Path.cwd())
        connection = {"modal": {"ready": True, "account_verified": True, "budget": {"verified": True, "max_out_of_pocket": 0},
                                "config": {"entitlement_confirmed": True, "budget_confirmed": True, "native_spend_limit": 0, "gpu": "T4"}}}
        self.assertFalse(preview(spec, CATALOG, connection, {})["eligible"])

    def test_local_gpu_detection_is_separate_from_cuda_readiness(self):
        hardware = {"gpus": [{"name": "NVIDIA fixture"}], "torch_available": False, "cuda_available": False}
        result = self.plan({"providers": ["local"], "recipe": "pytorch", "device": "auto"}, hardware=hardware)
        self.assertEqual(result["decisions"][0]["resolved_resource"]["device"], "cpu")
        self.assertTrue(any("physically detected" in warning for warning in result["warnings"]))
        result = self.plan({"providers": ["local"], "recipe": "image", "device": "cuda"}, hardware=hardware)
        self.assertEqual({b["code"] for b in result["decisions"][0]["blockers"]}, {"missing_dependency", "cuda_unavailable"})
        action = next(b["action"] for b in result["decisions"][0]["blockers"] if b["code"] == "missing_dependency")
        self.assertEqual((action["profile"], action["device"]), ("image", "cuda"))

    def test_catalog_and_first_setup_step_explain_free_only_before_auth(self):
        for pid in ("modal", "lightning"):
            provider = get_provider(pid)
            self.assertFalse(provider["capabilities"]["automatic_free_only"])
            guide = provider_setup(pid, {}, {})
            self.assertEqual(guide["steps"][0]["id"], "limits")
            self.assertFalse(guide["admission"]["automatic_free_only"])
        modal = provider_setup("modal", {}, {})
        text = " ".join(modal["steps"][0]["instructions"])
        self.assertIn("before credits", text)
        self.assertIn("after credits", text)
        self.assertTrue(get_provider("kaggle")["capabilities"]["automatic_free_only"])


class SelectedPreparationTests(unittest.TestCase):
    def test_explicit_image_cuda_does_not_depend_on_installed_torch(self):
        result = resolve_preparation("image", "cuda", {"torch_available": False, "cuda_available": False, "gpus": []})
        self.assertEqual((result["effective_profile"], result["device"]), ("cuda", "cuda"))

    def test_explicit_image_cpu_stays_cpu_on_cuda_computer(self):
        result = resolve_preparation("image", "cpu", {"cuda_available": True, "gpus": [{}]})
        self.assertEqual((result["effective_profile"], result["device"]), ("image", "cpu"))

    def test_auto_image_preparation_can_use_detected_gpu_before_torch_exists(self):
        self.assertEqual(resolve_preparation("image", "auto", {"gpus": [{}], "cuda_available": False})["effective_profile"], "cuda")
        self.assertEqual(resolve_preparation("image", "auto", {})["effective_profile"], "image")
        with self.assertRaises(ValueError):
            resolve_preparation("cpu", "cuda", {})

    def test_cuda_install_command_uses_frozen_preparation_not_later_hardware(self):
        store = MagicMock()
        store.all.return_value = []
        service = SimpleNamespace(store=store, hardware={}, lock=threading.RLock(), closing=threading.Event(),
                                  environment_busy=True, root=Path.cwd())
        setup = EnvironmentSetup(service)
        setup.tasks["test"] = {"id": "test", **resolve_preparation("image", "cuda", {}), "status": "queued"}
        process = MagicMock()
        process.__enter__.return_value = process
        process.stdout = iter([])
        process.poll.return_value = 0
        process.wait.return_value = 0
        with patch("multigpu.environment.subprocess.Popen", return_value=process) as launch, \
             patch("multigpu.process_control.OwnedProcessTree"), \
             patch("multigpu.recipes.prepare_image_weights", return_value={"weights_path": "fixture"}), \
             patch("multigpu.service.hardware_info", return_value={}), patch.dict("os.environ", {"MULTIGPU_UV": ""}):
            setup._run("test", "image")
        command = launch.call_args.args[0]
        self.assertIn("torch==2.14.1+cu130", command)
        self.assertIn("https://download.pytorch.org/whl/cu130", command)
        self.assertEqual(setup.get("test")["status"], "completed")


if __name__ == "__main__":
    unittest.main()
