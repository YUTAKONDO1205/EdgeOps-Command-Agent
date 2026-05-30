"""Tests for src/ai_search.py — Azure SDK is mocked so we never hit a
real Search service. The goal is to verify the doc shape we'd upload and
the search-result mapping back to ``rag.RetrievalResult``."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from src import ai_search


def test_is_configured_false_without_env():
    assert ai_search.is_configured() is False


def test_is_configured_true_with_env(monkeypatch):
    monkeypatch.setenv("AZURE_SEARCH_ENDPOINT", "https://example.search.windows.net")
    monkeypatch.setenv("AZURE_SEARCH_API_KEY", "xxx")
    assert ai_search.is_configured() is True


def test_upload_docs_no_op_when_not_configured(tmp_side_files):
    n = ai_search.upload_docs([
        ai_search.IndexedDoc(id="1", section_title="t", body="b",
                             keywords=["k"], source_name="s.pdf"),
    ])
    assert n == 0


def test_upload_docs_sends_expected_payload(monkeypatch):
    monkeypatch.setenv("AZURE_SEARCH_ENDPOINT", "https://example.search.windows.net")
    monkeypatch.setenv("AZURE_SEARCH_API_KEY", "xxx")

    captured = {}

    fake_search_client = MagicMock()
    fake_search_client.upload_documents.return_value = [
        MagicMock(succeeded=True), MagicMock(succeeded=True),
    ]

    def fake_search_client_factory(endpoint, index_name, credential):
        return fake_search_client

    fake_index_client = MagicMock()
    fake_index_client.get_index.return_value = object()  # index exists -> skip create

    with patch("azure.search.documents.SearchClient", fake_search_client_factory), \
         patch("azure.search.documents.indexes.SearchIndexClient", lambda **kw: fake_index_client):
        ai_search._CLIENT_CACHE.clear()
        n = ai_search.upload_docs([
            ai_search.IndexedDoc(id="a", section_title="ch1", body="body a",
                                 keywords=["振動"], source_name="m.pdf",
                                 page_start=1, page_end=2),
            ai_search.IndexedDoc(id="b", section_title="ch2", body="body b",
                                 keywords=["温度"], source_name="m.pdf"),
        ])
    assert n == 2
    actions = fake_search_client.upload_documents.call_args[0][0]
    assert {a["id"] for a in actions} == {"a", "b"}
    assert actions[0]["section_title"] == "ch1"
    assert actions[0]["page_start"] == 1
    assert "ingested_at" in actions[0]


def test_search_maps_results_correctly(monkeypatch):
    monkeypatch.setenv("AZURE_SEARCH_ENDPOINT", "https://example.search.windows.net")
    monkeypatch.setenv("AZURE_SEARCH_API_KEY", "xxx")

    fake_client = MagicMock()
    fake_client.search.return_value = [
        {"section_title": "α", "body": "body α", "@search.score": 9.5},
        {"section_title": "β", "body": "body β", "@search.score": 4.2},
    ]
    with patch("azure.search.documents.SearchClient",
               lambda endpoint, index_name, credential: fake_client):
        ai_search._CLIENT_CACHE.clear()
        hits = ai_search.search(["振動", "軸受"], top_k=2)
    assert [h[0] for h in hits] == ["α", "β"]
    assert hits[0][2] == 9.5


def test_search_returns_empty_when_terms_empty(monkeypatch):
    monkeypatch.setenv("AZURE_SEARCH_ENDPOINT", "https://example.search.windows.net")
    monkeypatch.setenv("AZURE_SEARCH_API_KEY", "xxx")
    assert ai_search.search([]) == []
