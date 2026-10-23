"""Persisted instructions never substitute for actual provider readiness."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from multigpu.server import make_server
from multigpu.service import Service


class SetupJourneyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.hardware = patch("multigpu.service.hardware_info", return_value={"torch_available": False, "cuda_available": False, "gpus": []})
        self.hardware.start()
        self.addCleanup(self.hardware.stop)
        self.service = Service(self.root / "state")

    def tearDown(self):
        self.service.shutdown()
        self.service.store.close()
        self.temp.cleanup()

    def guide(self, ready=False):
        return {"provider_id": "kaggle", "title": "Kaggle setup", "steps": [
            {"id": "account", "title": "Your account", "completion": "acknowledgement", "can_complete": True},
            {"id": "verify", "title": "Check account", "completion": "automatic", "ready": ready,
             "blocking_reason": "Check the native account before continuing."}]}

    def move(self, raw=None, ready=False):
        return self.service._setup_progress("provider_setup", "kaggle", self.guide(ready), raw)

    def test_acknowledgements_survive_restart_but_cannot_verify_credentials(self):
        guide = self.move({"action": "advance", "step_id": "account", "acknowledged": True})
        self.assertEqual(guide["progress"]["current_step"], "verify")
        self.assertEqual(guide["progress"]["completed_steps"], ["account"])
        self.service.shutdown()
        self.service.store.close()
        self.service = Service(self.root / "state")
        self.assertEqual(self.move()["progress"], guide["progress"])
        with self.assertRaisesRegex(ValueError, "native account"):
            self.move({"action": "advance", "step_id": "verify", "acknowledged": True})
        self.assertEqual(self.service.connections(), {})

    def test_automatic_steps_follow_current_readiness_not_saved_confirmations(self):
        self.move({"action": "goto", "step_id": "verify"})
        guide = self.move({"action": "advance", "step_id": "verify"}, ready=True)
        self.assertIn("verify", guide["progress"]["completed_steps"])
        self.assertNotIn("verify", self.move(ready=False)["progress"]["completed_steps"])
        self.assertFalse(self.move(ready=False)["completed"])

    def test_stale_invalid_and_unacknowledged_actions_do_not_write_progress(self):
        for raw in ({"action": "advance", "step_id": "account"},
                    {"action": "advance", "step_id": "verify", "acknowledged": True},
                    {"action": "goto", "step_id": "not-a-step"},
                    {"action": "advance", "step_id": "account", "acknowledged": True, "ready": True}):
            with self.assertRaises(ValueError):
                self.move(raw)
            self.assertIsNone(self.service.store.get("provider_setup", "kaggle"))

    def test_provider_and_job_progress_are_independent(self):
        self.move({"action": "advance", "step_id": "account", "acknowledged": True})
        first = self.service._setup_progress("job_setup", "one", self.guide(), None)
        second = self.service._setup_progress("job_setup", "two", self.guide(), None)
        self.assertEqual(first["progress"]["current_step"], "account")
        self.assertEqual(second["progress"]["completed_steps"], [])

    def test_new_checkpoint_resets_instructions_and_rejects_stale_confirmation(self):
        def guide(revision):
            return {**self.guide(), "revision": revision}
        self.service._setup_progress("job_setup", "run", guide("first"),
            {"action": "advance", "step_id": "account", "acknowledged": True, "guide_revision": "first"})
        changed = self.service._setup_progress("job_setup", "run", guide("continued"), None)
        self.assertEqual(changed["progress"]["completed_steps"], [])
        self.assertEqual(changed["progress"]["current_step"], "account")
        with self.assertRaisesRegex(ValueError, "training files changed"):
            self.service._setup_progress("job_setup", "run", guide("continued"),
                {"action": "advance", "step_id": "account", "acknowledged": True, "guide_revision": "first"})

    def test_setup_http_mutations_are_token_guarded(self):
        server = make_server(self.service, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urllib.request.urlopen(base + "/api/bootstrap") as response:
                token = response.headers["X-MultiGPU-Token"]
            with patch.object(self.service, "provider_setup", side_effect=lambda *_: copy.deepcopy(self.guide())):
                with urllib.request.urlopen(base + "/api/providers/kaggle/setup") as response:
                    self.assertEqual(json.load(response)["provider_id"], "kaggle")
                request = urllib.request.Request(base + "/api/providers/kaggle/setup", data=b'{"action":"goto","step_id":"account"}', headers={"Content-Type": "application/json"})
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(request)
                self.assertEqual(error.exception.code, 403)
                request.add_header("X-MultiGPU-Token", token)
                with urllib.request.urlopen(request) as response:
                    self.assertEqual(response.status, 200)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_direct_notebook_http_is_training_file_and_tracks_current_session(self):
        job = self.service.create({"providers": ["colab"], "steps": 4})
        server = make_server(self.service, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urllib.request.urlopen(base + f"/api/jobs/{job['id']}/notebook") as response:
                self.assertEqual(response.headers["Content-Type"], "application/x-ipynb+json")
                self.assertIn('filename="training.ipynb"', response.headers["Content-Disposition"])
                document = json.load(response)
            self.assertEqual(document["nbformat"], 4)
            self.assertTrue(any(c["cell_type"] == "code" for c in document["cells"]))
            self.assertTrue(self.service.job(job["id"])["notebook_preparation"]["prepared_at"])
            with urllib.request.urlopen(base + f"/api/jobs/{job['id']}/setup") as response:
                guide = json.load(response)
            self.assertFalse(guide["payload_required"])
            self.assertFalse(next(s for s in guide["steps"] if s["id"] == "import")["ready"])
            self.assertFalse((self.service.jobs_root / job["id"] / "notebook-bundle.zip").exists())
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
