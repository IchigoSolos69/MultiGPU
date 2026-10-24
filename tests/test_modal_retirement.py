"""Modal's hosted notebook retirement must not disable Functions or saved results."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from multigpu import connectors
from multigpu.catalog import CATALOG, get_provider
from multigpu.onboarding import IMPORT_HELP, job_setup, provider_setup
from multigpu.planner import preview, validate_spec
from multigpu.service import Service
from multigpu.worker import run


class ModalRetirementTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / ".test-runs"
        scratch.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="modal-retirement-", dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def plan(self, *, guided=False, free_only=True):
        spec = validate_spec({"providers": ["modal"], "guided": guided, "free_only": free_only, "paid_cap": 10}, self.root)
        connections = {"modal": {"ready": True, "config": {"entitlement_confirmed": True,
                        "budget_confirmed": True, "native_spend_limit": 5}}}
        return preview(spec, CATALOG, connections, {})

    def service(self):
        with patch("multigpu.service.hardware_info", return_value={"torch_available": False, "cuda_available": False, "gpus": []}):
            service = Service(self.root / "state")
        self.addCleanup(service.store.close)
        self.addCleanup(service.shutdown)
        return service

    def legacy_job(self, service):
        job = service.create({"providers": ["colab"], "steps": 3})
        job["provider_id"] = "modal"
        job["spec"]["providers"] = ["modal", "colab"]
        service.save_job(job)
        (service.jobs_root / job["id"] / "spec.json").write_text(json.dumps(job["spec"]), encoding="utf-8")
        return job

    def test_catalog_and_setup_preserve_functions_without_a_hosted_notebook_route(self):
        provider = get_provider("modal")
        self.assertEqual(provider["name"], "Modal")
        self.assertEqual(provider["docs_url"], "https://modal.com/docs/guide/functions")
        self.assertTrue(provider["capabilities"]["submit"])
        self.assertFalse(provider["capabilities"]["notebook_bundle"])
        self.assertNotIn("modal", IMPORT_HELP)
        guide = provider_setup("modal", {}, {})
        self.assertFalse(guide["admission"]["manual_alternative"])
        limits = guide["steps"][0]
        text = " ".join(limits["instructions"])
        self.assertIn("23 October 2026", text)
        self.assertIn("Modal Functions", text)
        self.assertNotIn("You can prepare a manual notebook", text)
        self.assertIn("https://modal.com/changelog/sunsetting-modal-notebooks", [link["url"] for link in limits["links"]])

    def test_paid_functions_remain_eligible_and_free_only_guard_remains_blocked(self):
        self.assertTrue(self.plan(free_only=False)["eligible"])
        free = self.plan()
        self.assertFalse(free["eligible"])
        self.assertEqual(free["decisions"][0]["blocker_code"], "free_cap_unverified")
        self.assertNotIn("prepare_guided", [entry.get("action", {}).get("type") for entry in free["decisions"][0]["blockers"]])
        with self.assertRaises(connectors.ConnectorError) as denied:
            connectors._paid_policy({}, {"free_only": True})
        self.assertIn("local compute or another assisted notebook provider", str(denied.exception))
        self.assertNotIn("Follow the in-app notebook guide", str(denied.exception))
        for paid in (False, True):
            guided = self.plan(guided=True, free_only=not paid)
            self.assertFalse(guided["preparation_eligible"])
            self.assertIsNone(guided["selected_provider"])
            self.assertEqual(guided["decisions"][0]["blocker_code"], "notebook_retired")

    def test_direct_notebook_builders_reject_modal_before_reading_or_staging(self):
        with patch.object(connectors, "_stage_for_provider") as stage:
            for build in (connectors.build_notebook, connectors.build_notebook_payload, connectors.build_bundle):
                with self.subTest(builder=build.__name__), self.assertRaises(connectors.ConnectorError) as caught:
                    build("modal", {}, self.root / "missing-spec.json", self.root)
                self.assertEqual(caught.exception.code, "notebook_retired")
            stage.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_saved_modal_session_has_import_only_guide_and_no_new_downloads(self):
        service = self.service()
        job = self.legacy_job(service)
        guide = job_setup(job, service.jobs_root / job["id"])
        self.assertEqual([step["id"] for step in guide["steps"]], ["retired", "import"])
        self.assertFalse(guide["steps"][0]["can_complete"])
        self.assertEqual([action["type"] for step in guide["steps"] for action in step["actions"]], ["import"])
        self.assertIn("import", service.job(job["id"])["allowed_actions"])
        self.assertNotIn("bundle", service.job(job["id"])["allowed_actions"])
        for download in (service.notebook, service.notebook_payload, service.bundle):
            with self.subTest(download=download.__name__), self.assertRaisesRegex(ValueError, "retired"):
                download(job["id"])

    def test_saved_modal_checkpoint_can_be_imported_and_continue_in_selected_colab(self):
        service = self.service()
        job = self.legacy_job(service)
        remote = self.root / "existing-results"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run({**job["spec"], "runtime_seconds": 0}, remote)["status"], "paused")
        reviewed = service.import_output(job["id"], {"path": str(remote), "preview": True})
        imported = service.import_output(job["id"], {"path": str(remote), "preview_digest": reviewed["preview_digest"]})
        self.assertTrue(imported["checkpoint"]["verified"])
        self.assertEqual(imported["guided_providers"], ["colab"])
        continuation = service.continue_guided(job["id"], {"preview": True})
        self.assertEqual(continuation["provider_id"], "colab")
        with patch.object(service, "_start") as start:
            continued = service.continue_guided(job["id"], {"preview_digest": continuation["preview_digest"], "confirmed_stopped": True})
            self.assertEqual(continued["provider_id"], "colab")
            start.assert_not_called()

    def test_saved_modal_finished_model_can_still_be_imported_and_exported(self):
        service = self.service()
        job = self.legacy_job(service)
        remote = self.root / "finished-results"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run(job["spec"], remote)["status"], "completed")
        imported = service.import_output(job["id"], {"path": str(remote)})
        self.assertEqual(imported["export_status"], "verified")
        self.assertTrue(Path(imported["export_path"], "manifest.json").is_file())
        self.assertTrue(service.artifact_zip(job["id"]).is_file())


if __name__ == "__main__":
    unittest.main()
