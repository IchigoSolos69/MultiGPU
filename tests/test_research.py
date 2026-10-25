"""Published research must stay separate from provider execution authority."""
import csv
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from multigpu.catalog import CATALOG, get_provider
from multigpu.research import DOCUMENTS, research_document, research_documents, research_providers, research_directory_csv
from multigpu.server import make_server
from multigpu.service import Service


TEMP_ROOT = Path(__file__).resolve().parent.parent / ".test-runs"
TEMP_ROOT.mkdir(exist_ok=True)


class ResearchDirectoryTests(unittest.TestCase):
    def test_complete_directory_matches_cited_csv_and_fixed_documents(self):
        records = research_providers()
        self.assertEqual(len(records), 142)
        self.assertEqual(len({row["id"] for row in records}), 142)
        original = list(csv.DictReader(io.StringIO(research_directory_csv().decode("utf-8-sig"))))
        self.assertEqual([row["id"] for row in records], [row["id"] for row in original])
        for row, source in zip(records, original):
            for field in ("name", "cost_class", "training_mode", "accelerator", "limits", "interface", "free_offer"):
                self.assertEqual(row[field], source[field], (row["id"], field))
            self.assertTrue(row["sources"])
            self.assertTrue(all(url.startswith("https://") for url in row["sources"]))
            self.assertTrue(set(row["report_ids"]) <= DOCUMENTS.keys())
        self.assertEqual(len(research_documents()), 13)
        for document in research_documents():
            loaded = research_document(document["id"])
            self.assertEqual(loaded["path"], document["path"])
            self.assertTrue(loaded["content"].startswith("#"))
            self.assertEqual(len(loaded["sha256"]), 64)

    def test_every_research_record_has_no_execution_capability(self):
        ids = {row["id"] for row in CATALOG}
        self.assertEqual(len(ids), 14)
        for row in research_providers():
            self.assertTrue(row["research_only"])
            self.assertEqual(row["integration"], "research")
            self.assertTrue(all(value is False for value in row["capabilities"].values()))
            self.assertNotIn(row["id"], ids)
            with self.assertRaises(KeyError):
                get_provider(row["id"])

    def test_credit_and_eligibility_tags_do_not_claim_general_free_gpu(self):
        records = {row["id"]: row for row in research_providers()}
        for identifier in ("google_cloud", "oracle", "aws", "azure", "ibm", "coiled", "featurize", "siliconflow", "gpu_mart"):
            self.assertNotIn("free_gpu", records[identifier]["cost_tags"])
        self.assertIn("platform_credit", records["coiled"]["cost_tags"])
        self.assertNotIn("introductory_credit", records["coiled"]["cost_tags"])
        self.assertIn("paused", records["nvidia-academic-grants-paused"]["cost_tags"])
        self.assertIn("legacy", records["aws_studio_lab"]["cost_tags"])
        self.assertNotIn("free_gpu", get_provider("paperspace")["cost_tags"])
        self.assertIn("unverified", get_provider("paperspace")["free_summary"])
        self.assertIn("recurring_credit", get_provider("modal")["cost_tags"])
        self.assertFalse(get_provider("modal")["capabilities"]["automatic_free_only"])
        self.assertTrue(get_provider("kaggle")["capabilities"]["submit"])

    def test_discovery_results_are_independent(self):
        records = research_providers()
        records[0]["capabilities"]["submit"] = True
        records[0]["sources"].clear()
        original = research_providers()[0]
        self.assertFalse(original["capabilities"]["submit"])
        self.assertTrue(original["sources"])


class ResearchAPITests(unittest.TestCase):
    def test_runtime_endpoint_has_no_static_research_and_keeps_session_guard(self):
        with self.get("/api/runtime") as response:
            data = json.load(response)
            self.assertTrue(response.headers["X-MultiGPU-Token"])
            self.assertIn("jobs", data)
            self.assertNotIn("research_providers", data)
            self.assertNotIn("providers", data)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=TEMP_ROOT)
        with patch("multigpu.service.hardware_info", return_value={"cuda_available": False, "gpus": [], "torch_available": False}):
            self.service = Service(Path(self.temp.name) / "state")
        self.server = make_server(self.service, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()
        self.service.shutdown()
        self.service.store.close()
        self.temp.cleanup()

    def get(self, path):
        return urllib.request.urlopen(self.base + path, timeout=5)

    def test_bootstrap_includes_discovery_without_adding_training_resources(self):
        with self.get("/api/bootstrap") as response:
            boot = json.load(response)
        self.assertEqual(len(boot["providers"]), 14)
        self.assertEqual(len(boot["research_providers"]), 142)
        self.assertEqual(len(boot["research_documents"]), 13)
        with patch("multigpu.connectors.probe") as probe, patch("multigpu.connectors.submit") as submit:
            with self.assertRaisesRegex(ValueError, "Unknown provider"):
                self.service.probe("runpod", {})
            with self.assertRaisesRegex(ValueError, "Unknown provider"):
                self.service.plan({"providers": ["runpod"], "steps": 10})
            probe.assert_not_called()
            submit.assert_not_called()

    def test_document_endpoint_is_json_and_uses_only_fixed_ids(self):
        with self.get("/api/research/documents/additional-providers") as response:
            self.assertEqual(response.headers["Content-Type"], "application/json")
            self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
            doc = json.load(response)
        self.assertEqual(doc["path"], "ADDITIONAL_PROVIDERS.md")
        self.assertIn("142 distinct research entries", doc["content"])
        for target in ("unknown", "%2e%2e%2fREADME.md", "..%5cREADME.md", "providers-gpu/../../README.md"):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.get("/api/research/documents/" + target)
            self.assertEqual(error.exception.code, 404)

    def test_markdown_html_is_data_not_an_html_response(self):
        fixture = Path(self.temp.name) / "docs"
        fixture.mkdir()
        malicious = '<script>window.researchExecuted=true</script>\n# Literal evidence'
        (fixture / "ADDITIONAL_PROVIDERS.md").write_text(malicious, encoding="utf-8")
        with patch("multigpu.research.DOCS_ROOT", fixture):
            with self.get("/api/research/documents/additional-providers") as response:
                self.assertEqual(response.headers["Content-Type"], "application/json")
                self.assertEqual(json.load(response)["content"], malicious)

    def test_csv_download_is_bundled_and_matches_catalog(self):
        with self.get("/api/research/directory.csv") as response:
            self.assertIn("attachment", response.headers["Content-Disposition"])
            self.assertEqual(response.headers["Content-Type"], "text/csv; charset=utf-8")
            rows = list(csv.DictReader(io.StringIO(response.read().decode("utf-8-sig"))))
        self.assertEqual(len(rows), 142)
        self.assertEqual(rows[0]["id"], research_providers()[0]["id"])


if __name__ == "__main__":
    unittest.main()
