"""
Pytest fixtures.

The new modules (cosmos_store, blob_store, iot_ingest) write to project-root
side-files when Azure isn't configured. To keep tests hermetic we redirect
those paths to a tmp dir per test and force mock mode for the LLM.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Make `src` importable when running from repo root or tests/ dir.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(autouse=True)
def _force_mock_mode(monkeypatch):
    """Mock mode keeps LLM agents deterministic."""
    monkeypatch.setenv("EDGEOPS_USE_MOCK", "true")
    # Strip any leaked Azure creds from the dev shell.
    for v in (
        "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT",
        "AZURE_SEARCH_ENDPOINT", "AZURE_SEARCH_API_KEY",
        "AZURE_STORAGE_CONNECTION_STRING",
        "COSMOS_ENDPOINT", "COSMOS_KEY",
        "TEAMS_WEBHOOK_URL",
        "EVENT_HUB_CONNECTION_STRING", "EVENT_HUB_NAME",
    ):
        monkeypatch.delenv(v, raising=False)


@pytest.fixture
def tmp_side_files(monkeypatch, tmp_path):
    """Redirect the side-file paths each module uses for its local fallback."""
    from src import blob_store, cosmos_store, iot_ingest, rag
    monkeypatch.setattr(blob_store, "LOCAL_FALLBACK_DIR", tmp_path / "_uploaded")
    monkeypatch.setattr(cosmos_store, "_LOCAL_LOG", tmp_path / "_local_cosmos.jsonl")
    monkeypatch.setattr(iot_ingest, "LOCAL_STREAM_JSONL", tmp_path / "_spresense_stream.jsonl")
    # Clear in-memory RAG so previous tests don't bleed.
    rag.clear_uploaded_chunks()
    yield tmp_path
    rag.clear_uploaded_chunks()
