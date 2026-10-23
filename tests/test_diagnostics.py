"""Provider diagnostics remain useful without storing credentials."""
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from multigpu import connectors as c
from multigpu.service import Service


class DiagnosticsTests(unittest.TestCase):
    @patch.object(c.subprocess, "run")
    def test_timeout_and_start_failure_retain_safe_error_evidence(self, process):
        process.side_effect = c.subprocess.TimeoutExpired("kaggle", 30,
            output=b"Upload interrupted\nAuthorization: Bearer timeout-secret",
            stderr=b"Still useful error detail\nKAGGLE_KEY=timeout-key")
        with self.assertRaises(c.ConnectorError) as raised:
            c._cli("kaggle", ["kernels", "push"], timeout=30)
        error = raised.exception
        self.assertEqual(error.code, "network")
        self.assertTrue(error.transient)
        self.assertEqual(error.diagnostic["timeout_seconds"], 30)
        self.assertIn("Upload interrupted", error.diagnostic["stdout"])
        self.assertIn("Still useful error detail", error.diagnostic["stderr"])
        self.assertNotIn("timeout-secret", json.dumps(error.diagnostic))
        self.assertNotIn("timeout-key", json.dumps(error.diagnostic))
        process.side_effect = OSError("Interpreter was not found\nAuthorization: Bearer spawn-secret")
        with self.assertRaises(c.ConnectorError) as raised:
            c._cli("kaggle", ["quota", "--csv"])
        self.assertEqual(raised.exception.code, "missing_dependency")
        self.assertIn("Interpreter was not found", raised.exception.diagnostic["error"])
        self.assertNotIn("spawn-secret", json.dumps(raised.exception.diagnostic))

    def test_prefixed_multiline_and_proxy_secrets_are_redacted(self):
        samples = ["KAGGLE_KEY=short-kaggle-key",
                   "MODAL_TOKEN_SECRET=short-modal-secret",
                   'Response 403:\n{\n  "access_token":\n    "split-secret"\n}',
                   "Proxy HTTPS://proxy-user:proxy-pass@example.com/path?token=query-secret",
                   "Proxy socks5://socks-user:socks-pass@example.com/path?token=socks-query"]
        secrets = ("short-kaggle-key", "short-modal-secret", "split-secret", "proxy-user", "proxy-pass",
                   "query-secret", "socks-user", "socks-pass", "socks-query")
        for sample in samples:
            with self.subTest(sample=sample):
                safe = c.redact_provider_output(sample)
                for secret in secrets:
                    self.assertNotIn(secret, safe)

    def test_submit_wrapper_preserves_classification_evidence_and_blocks_retry(self):
        scratch = Path(__file__).resolve().parents[1] / ".test-runs"
        scratch.mkdir(exist_ok=True)
        cases = [(1, "", "Permission kernels.get was denied", "permission"),
                 (2, "", "unrecognized arguments: --accelerator", "unsupported_cli"),
                 (0, "Kernel push error: GPU quota limit exceeded", "", "quota")]
        for returncode, stdout, stderr, classification in cases:
            with self.subTest(classification=classification), tempfile.TemporaryDirectory(dir=scratch) as directory:
                with patch("multigpu.service.hardware_info", return_value={"gpus": [], "cuda_available": False}):
                    service = Service(Path(directory))
                try:
                    job = service.create({"providers": ["colab"], "steps": 3})
                    folder = service.jobs_root / job["id"]
                    spec = folder / "spec.json"
                    response = SimpleNamespace(returncode=returncode, stdout=stdout,
                                               stderr=stderr + "\nAuthorization: Bearer push-secret")
                    with patch.object(c, "_quota", return_value={"remaining_hours": 1}), \
                         patch.object(c, "_kaggle_identity", return_value={"username": "test", "verified": True}), \
                         patch.object(c.subprocess, "run", return_value=response) as process:
                        intent = c.prepare("kaggle", {"username": "test"}, spec, folder)
                        with self.assertRaises(c.ConnectorError) as raised:
                            c.submit("kaggle", {"username": "test"}, spec, folder, intent=intent)
                        error = raised.exception
                        expected_outcome = "not_started" if classification == "unsupported_cli" else "unknown"
                        self.assertEqual(error.code, classification if expected_outcome == "not_started" else "submission_unknown")
                        self.assertEqual(error.submission_outcome, expected_outcome)
                        self.assertEqual(error.diagnostic["error_code"], classification)
                        self.assertEqual(error.diagnostic["exit_code"], returncode)
                        evidence = stdout or stderr
                        self.assertIn(evidence, error.diagnostic["stdout"] + error.diagnostic["stderr"])
                        self.assertNotIn("push-secret", json.dumps(error.diagnostic))
                        service._log_provider_error(job["id"], error, "submission", "kaggle")
                        with self.assertRaises(c.ConnectorError) as duplicate:
                            c.submit("kaggle", {"username": "test"}, spec, folder, intent=intent)
                        self.assertEqual(duplicate.exception.submission_outcome, expected_outcome)
                        self.assertEqual(process.call_count, 1)
                    journal = json.loads((folder / ("submission-" + intent["attempt_id"] + ".json")).read_text())
                    self.assertEqual(journal["submission_status"], expected_outcome)
                    for logs in (service.logs(job["id"]), service.diagnostic_logs()["logs"]):
                        self.assertIn(evidence, logs)
                        self.assertIn(classification, logs)
                        self.assertNotIn("push-secret", logs)
                finally:
                    service.shutdown()
                    service.store.close()

    def test_nested_diagnostics_are_redacted_at_the_persistence_boundary(self):
        scratch = Path(__file__).resolve().parents[1] / ".test-runs"
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as directory:
            with patch("multigpu.service.hardware_info", return_value={"gpus": [], "cuda_available": False}):
                service = Service(Path(directory))
            try:
                job = service.create({"providers": ["colab"], "steps": 3})
                error = c.ConnectorError("Permission denied", diagnostic={
                    "response": {"error": "Permission denied", "headers": {"Authorization": "Bearer nested-secret"},
                                 "access_token": "nested-access"},
                    "details": ["KAGGLE_KEY=nested-key"]})
                service._log_provider_error(job["id"], error, "submission", "kaggle")
                for logs in (service.logs(job["id"]), service.diagnostic_logs()["logs"]):
                    self.assertIn("Permission denied", logs)
                    self.assertNotIn("nested-secret", logs)
                    self.assertNotIn("nested-key", logs)
                    self.assertNotIn("nested-access", logs)
            finally:
                service.shutdown()
                service.store.close()

    @patch.object(c.subprocess, "run")
    @patch.object(c.shutil, "which", return_value="C:/wrong-environment/kaggle.exe")
    def test_cli_always_uses_app_python_and_utf8(self, which, run):
        run.return_value = SimpleNamespace(returncode=0, stdout="ok", stderr="")
        self.assertEqual(c._cli("kaggle", ["quota", "--csv"]), "ok")
        self.assertEqual(run.call_args.args[0], [c.sys.executable, "-m", "kaggle", "quota", "--csv"])
        self.assertEqual(run.call_args.kwargs["env"]["PYTHONIOENCODING"], "utf-8")
        which.assert_not_called()

    def test_redaction_keeps_error_but_removes_credentials(self):
        raw = ('Permission kernels.get was denied.\n'
               'Authorization: Bearer super-secret\n'
               '{"access_token": "another-secret"}\n'
               'Proxy https://private-user:private-password@example.com/path?credential=hidden-query\n'
               'KGAT_short-native-secret\n'
               'opaque 1234567890abcdef1234567890abcdef\n')
        safe = c.redact_provider_output(raw)
        self.assertIn("Permission kernels.get was denied", safe)
        for secret in ("super-secret", "another-secret", "private-user", "private-password", "hidden-query", "short-native-secret", "1234567890abcdef1234567890abcdef"):
            self.assertNotIn(secret, safe)

    @patch.object(c.subprocess, "run")
    def test_failure_categories_and_safe_provider_details(self, run):
        for text, code in [("No module named kaggle", "missing_dependency"),
                           ("unrecognized arguments: --timeout", "unsupported_cli"),
                           ("Cannot access kernel (Permission kernels.get was denied)", "permission"),
                           ("Please run kaggle auth login", "auth"),
                           ("Connection timed out", "network"),
                           ("accelerator capacity unavailable", "invalid_resource")]:
            with self.subTest(code=code):
                run.return_value = SimpleNamespace(returncode=1, stdout="", stderr=text + "\nAuthorization: Bearer super-secret")
                with self.assertRaises(c.ConnectorError) as raised:
                    c._cli("kaggle", ["kernels", "status", "private/notebook"])
                error = raised.exception
                self.assertEqual(error.code, code)
                self.assertIn(text, error.diagnostic["stderr"])
                self.assertNotIn("super-secret", json.dumps(error.diagnostic))
                self.assertEqual(error.diagnostic["exit_code"], 1)

    @patch.object(c.subprocess, "run")
    def test_zero_exit_push_error_is_not_reported_as_a_launched_job(self, run):
        run.return_value = SimpleNamespace(returncode=0, stdout="Kernel push error: invalid argument", stderr="")
        with self.assertRaises(c.ConnectorError):
            c._cli("kaggle", ["kernels", "push", "-p", "payload"])

    def test_job_and_app_logs_store_redacted_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("multigpu.service.hardware_info", return_value={"gpus": [], "cuda_available": False}):
                service = Service(Path(directory))
            try:
                job = service.create({"providers": ["colab"], "steps": 3})
                error = c.ConnectorError("Provider denied the request", code="permission",
                    diagnostic={"exit_code": 1, "stderr": "Permission denied\nAuthorization: Bearer super-secret"})
                service._log_provider_error(job["id"], error, "submission", "kaggle")
                for logs in (service.logs(job["id"]), service.diagnostic_logs()["logs"]):
                    self.assertIn("Permission denied", logs)
                    self.assertIn('"exit_code": 1', logs)
                    self.assertIn('"stage": "submission"', logs)
                    self.assertNotIn("super-secret", logs)
            finally:
                service.shutdown()
                service.store.close()

    @patch.object(c, "_cli", return_value="- username: None\n")
    def test_unset_native_username_is_never_an_authenticated_identity(self, cli):
        with self.assertRaises(c.ConnectorError):
            c._kaggle_identity({})


if __name__ == "__main__":
    unittest.main()
