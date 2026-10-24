"""Bundled, read-only provider evidence; discovery never grants execution access."""
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
from pathlib import Path


CHECKED = "2026-10-24"
DOCS_ROOT = Path(__file__).resolve().parent.parent / "docs"
DIRECTORY_CSV = "research/additional-provider-directory-2026-10-24.csv"
DOCUMENTS = {
    "additional-providers": ("ADDITIONAL_PROVIDERS.md", "Additional training compute directory"),
    "provider-audit": ("PROVIDER_RESEARCH.md", "Original provider audit"),
    "providers-gpu": ("research/providers-gpu-2026-10-24.md", "Kaggle, Colab and Paperspace evidence"),
    "providers-credits": ("research/providers-credits-2026-10-24.md", "Modal, Lightning and local hardware"),
    "providers-notebooks": ("research/providers-notebooks-2026-10-24.md", "Deepnote, Hex, Datalore and Fastero"),
    "providers-cloud": ("research/providers-cloud-2026-10-24.md", "Cloud, institutional and development resources"),
    "additional-gpu-clouds": ("research/additional-gpu-clouds-2026-10-24.md", "GPU clouds, marketplaces and enterprise clusters"),
    "additional-clouds-grants": ("research/additional-clouds-grants-2026-10-24.md", "General clouds and research allocations"),
    "additional-free-compute": ("research/additional-free-compute-2026-10-24.md", "Free, promotional and credit-backed compute"),
    "additional-regional-managed": ("research/additional-regional-managed-2026-10-24.md", "India clouds, training APIs and scientific access"),
    "additional-asia-clouds": ("research/additional-asia-clouds-2026-10-24.md", "Asia and Pacific training clouds"),
    "additional-regional-expansion": ("research/additional-regional-expansion-2026-10-24.md", "Europe, Central Asia, Middle East and Australia"),
    "additional-training-platforms": ("research/additional-training-platforms-2026-10-24.md", "China and specialist training platforms"),
}


@lru_cache(maxsize=1)
def _records():
    source = Path(__file__).parent / "data" / "provider-research.json"
    records = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(records, list) or len({row["id"] for row in records}) != len(records):
        raise ValueError("The bundled research directory is invalid.")
    return records


def research_providers():
    """Return independent discovery records, separate from the executable catalog."""
    return deepcopy(_records())


def research_documents():
    return [{"id": key, "title": title, "path": path, "source_checked": CHECKED}
            for key, (path, title) in DOCUMENTS.items()]


def research_document(document_id):
    """IDs select fixed bundled files; never interpret a request as a filesystem path."""
    relative, title = DOCUMENTS[document_id]
    content = (DOCS_ROOT / relative).read_text(encoding="utf-8-sig")
    return {"id": document_id, "title": title, "path": relative, "source_checked": CHECKED,
            "content": content, "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest()}


def research_directory_csv():
    return (DOCS_ROOT / DIRECTORY_CSV).read_bytes()
