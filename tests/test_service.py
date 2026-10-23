import contextlib
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import zipfile

from multigpu.artifacts import IntegrityError, verify_export
from multigpu.catalog import CATALOG
from multigpu.imports import import_results
from multigpu.planner import preview, validate_spec
from multigpu.server import make_server
from multigpu.service import Service
from multigpu.worker import run

TEMP_ROOT = Path(__file__).resolve().parent.parent / ".test-runs"
TEMP_ROOT.mkdir(exist_ok=True)


class PlannerTests(unittest.TestCase):
    def test_catalog_contains_every_provider_and_local(self):
        self.assertEqual(len(CATALOG), 14)
        self.assertEqual(len({p["id"] for p in CATALOG}), 14)

    def test_unknown_credits_cannot_enable_free_execution(self):
        spec = validate_spec({"providers": ["modal"]}, "exports")
        result = preview(spec, CATALOG, {"modal": {"ready": True, "config": {"budget_confirmed": True}}}, {})
        self.assertFalse(result["eligible"])
        self.assertIn("verified", result["decisions"][0]["reason"])

    def test_paid_requires_explicit_per_job_optin_and_provider_limit(self):
        spec = validate_spec({"providers": ["modal"], "free_only": False, "paid_cap": 10}, "exports")
        connection = {"modal": {"ready": True, "config": {"entitlement_confirmed": True, "budget_confirmed": True, "native_spend_limit": 20}}}
        self.assertFalse(preview(spec, CATALOG, connection, {})["eligible"])
        connection["modal"]["config"]["native_spend_limit"] = 5
        self.assertTrue(preview(spec, CATALOG, connection, {})["eligible"])

    def test_unavailable_cuda_and_untrusted_scripts_are_blocked(self):
        spec = validate_spec({"device": "cuda"}, "exports")
        self.assertFalse(preview(spec, CATALOG, {}, {"cuda_available": False})["eligible"])
        with self.assertRaises(ValueError):
            validate_spec({"steps": float("nan")}, "exports")

    def test_active_account_is_not_double_reserved(self):
        spec = validate_spec({"providers": ["kaggle"]}, "exports")
        result = preview(spec, CATALOG, {"kaggle": {"ready": True}}, {}, [{"status": "running", "provider_id": "kaggle"}])
        self.assertFalse(result["eligible"])


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=TEMP_ROOT)
        with patch("multigpu.service.hardware_info", return_value={"torch_available": False, "cuda_available": False, "gpus": []}):
            self.service = Service(Path(self.temp.name) / "state")

    def tearDown(self):
        self.service.shutdown()
        self.service.store.close()
        self.temp.cleanup()

    def wait_terminal(self, jid):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            job = self.service.job(jid)
            if job["status"] in ("completed", "paused", "error") and not self.service.threads[jid].is_alive():
                return job
            time.sleep(0.05)
        self.fail("Local training did not reach a terminal state.")

    def test_real_training_is_exported_and_persisted(self):
        job = self.service.create({"name": "Small classifier", "steps": 50})
        done = self.wait_terminal(job["id"])
        self.assertEqual(done["status"], "completed", done)
        self.assertEqual(done["step"], 50)
        self.assertTrue(done["export_path"])
        verify_export(Path(done["export_path"]))
        metrics = self.service.job(job["id"], True)["metrics"]
        self.assertGreater(metrics[0]["loss"], metrics[-1]["loss"])
        with zipfile.ZipFile(self.service.artifact_zip(job["id"])) as archive:
            self.assertIn("model.json", archive.namelist())
            self.assertIn("checkpoint.json", archive.namelist())

    def test_guided_job_bundle_does_not_start_provider(self):
        with patch("multigpu.connectors.submit") as submit:
            job = self.service.create({"providers": ["colab"], "steps": 10})
            self.assertEqual(job["status"], "awaiting_user")
            bundle = self.service.bundle(job["id"])
            self.assertTrue(bundle.is_file())
            submit.assert_not_called()

    def test_pause_resume_keeps_target_and_checkpoint(self):
        job = self.service.create({"steps": 20000, "batch_size": 256, "runtime_minutes": 0.05})
        paused = self.wait_terminal(job["id"])
        self.assertEqual(paused["status"], "paused", paused)
        self.assertTrue(paused["checkpoint"])
        self.assertEqual(paused["total_steps"], 20000)
        old_step = paused["step"]
        resumed = self.service.resume(job["id"])
        self.assertEqual(resumed["spec"]["steps"], 20000)
        again = self.wait_terminal(job["id"])
        self.assertGreater(again["step"], old_step)

    def test_connection_does_not_store_secrets(self):
        with self.assertRaises(ValueError):
            self.service.probe("modal", {"token": "must-not-store"})
        self.assertEqual(self.service.connections(), {})

    def test_second_controller_cannot_reuse_the_same_workspace(self):
        with self.assertRaisesRegex(RuntimeError, "already uses"):
            Service(self.service.root)

    def test_remote_session_limit_continues_checkpoint_on_selected_local(self):
        self.service.store.put("connection", "kaggle", {"provider_id": "kaggle", "ready": True, "config": {"username": "test"}, "quota": {"remaining_hours": 1}})
        def submit(pid, config, spec_path, job_dir, intent=None):
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
            with contextlib.redirect_stdout(io.StringIO()):
                paused = run({**spec, "runtime_seconds": 0}, job_dir)
            self.assertEqual(paused["status"], "paused")
            return {"provider": pid, "state": "completed"}
        with patch("multigpu.connectors.prepare", return_value={"provider":"kaggle","kernel":"test/fake"}), patch("multigpu.connectors.submit", side_effect=submit), patch("multigpu.connectors.poll", return_value={"state": "completed"}), patch("multigpu.connectors.collect"):
            job = self.service.create({"providers": ["kaggle", "local"], "steps": 20})
            done = self.wait_terminal(job["id"])
        self.assertEqual(done["status"], "completed", done)
        self.assertEqual(done["attempted_providers"], ["kaggle", "local"])
        self.assertEqual(done["spec"]["steps"], 20)
        verify_export(Path(done["export_path"]))

    def test_imported_pause_can_explicitly_resume_on_local_compute(self):
        job = self.service.create({"providers": ["colab"], "steps": 20})
        remote = Path(self.temp.name) / "paused-remote"
        remote.mkdir()
        (remote / "STOP").touch()
        with contextlib.redirect_stdout(io.StringIO()):
            paused = run(job["spec"], remote)
        self.assertEqual(paused["status"], "paused")
        imported = self.service.import_output(job["id"], {"path": str(remote)})
        self.assertEqual(imported["status"], "paused")
        self.service.resume(job["id"], ["local", "colab"])
        done = self.wait_terminal(job["id"])
        self.assertEqual(done["status"], "completed", done)
        self.assertEqual(done["step"], 20)

    def test_guided_output_roundtrip_and_tamper_rejected(self):
        job = self.service.create({"providers": ["paperspace"], "steps": 5})
        remote = Path(self.temp.name) / "remote"
        with contextlib.redirect_stdout(io.StringIO()):
            result = run(job["spec"], remote)
        self.assertEqual(result["status"], "completed")
        imported = self.service.import_output(job["id"], {"path": str(remote)})
        self.assertEqual(imported["status"], "completed")
        self.assertTrue(imported["export_path"])
        (remote / "export" / "model.json").write_text("tampered", encoding="utf-8")
        with self.assertRaises(IntegrityError):
            self.service.import_output(job["id"], {"path": str(remote)})

    def test_import_rejects_archive_traversal(self):
        job = self.service.create({"providers": ["colab"], "steps": 5})
        archive = Path(self.temp.name) / "bad.zip"
        with zipfile.ZipFile(archive, "w") as handle:
            handle.writestr("../escaped.txt", "bad")
        with self.assertRaises(IntegrityError):
            import_results(archive, self.service.jobs_root / job["id"], job)
        self.assertFalse((self.service.jobs_root / "escaped.txt").exists())


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=TEMP_ROOT)
        with patch("multigpu.service.hardware_info", return_value={"gpus": [], "torch_available": False}):
            self.service = Service(self.temp.name)
        self.server = make_server(self.service, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.service.shutdown()
        self.service.store.close()
        self.temp.cleanup()

    def test_static_bootstrap_and_mutation_auth(self):
        with urllib.request.urlopen(self.base + "/api/bootstrap") as response:
            self.assertEqual(len(json.load(response)["providers"]), 14)
            token = response.headers["X-MultiGPU-Token"]
        request = urllib.request.Request(self.base + "/api/jobs/preview", data=b"{}", headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request)
        self.assertEqual(error.exception.code, 403)
        request.add_header("X-MultiGPU-Token", token)
        with urllib.request.urlopen(request) as response:
            self.assertTrue(json.load(response)["eligible"])
        with urllib.request.urlopen(self.base + "/static/styles.css") as response:
            self.assertEqual(response.status, 200)

    def test_cross_origin_and_dns_rebinding_are_rejected(self):
        for headers in ({"Origin": "https://untrusted.example"}, {"Host": "attacker.example"}):
            request = urllib.request.Request(self.base + "/api/bootstrap", headers=headers)
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request)
            self.assertEqual(error.exception.code, 403)


if __name__ == "__main__":
    unittest.main()
