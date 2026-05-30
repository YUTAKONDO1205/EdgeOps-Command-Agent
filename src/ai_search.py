"""
Azure AI Search バックエンド。

設計
----
- 1 つのインデックスに ``ManualChunk`` 相当のドキュメントを格納:
    id (key), section_title, body (searchable, ja.lucene), keywords (collection),
    source_name, page_start, page_end, ingested_at
- 検索は keyword + Lucene の AND 検索。日本語アナライザ（ja.lucene）で形態素分割。
- ``search()`` の戻り値は ``rag.RetrievalResult`` と同じ形 (section_title, body, score)
  なので、呼び出し側は実装差を意識せずに済みます。

未設定時は呼ばれない想定（``rag.py`` がディスパッチ）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from .utils import load_env


DEFAULT_INDEX = "edgeops-manuals"


@dataclass
class AzureSearchConfig:
    endpoint: str | None
    api_key: str | None
    index_name: str

    @classmethod
    def from_env(cls) -> "AzureSearchConfig":
        load_env()
        return cls(
            endpoint=os.getenv("AZURE_SEARCH_ENDPOINT") or None,
            api_key=os.getenv("AZURE_SEARCH_API_KEY") or None,
            index_name=os.getenv("AZURE_SEARCH_INDEX", DEFAULT_INDEX),
        )

    def is_configured(self) -> bool:
        return bool(self.endpoint and self.api_key)


def is_configured() -> bool:
    return AzureSearchConfig.from_env().is_configured()


_CLIENT_CACHE: dict[str, Any] = {}


def _admin_client(cfg: AzureSearchConfig):
    from azure.core.credentials import AzureKeyCredential
    from azure.search.documents.indexes import SearchIndexClient
    key = f"admin/{cfg.endpoint}/{cfg.index_name}"
    if key in _CLIENT_CACHE:
        return _CLIENT_CACHE[key]
    c = SearchIndexClient(endpoint=cfg.endpoint, credential=AzureKeyCredential(cfg.api_key))
    _CLIENT_CACHE[key] = c
    return c


def _search_client(cfg: AzureSearchConfig):
    from azure.core.credentials import AzureKeyCredential
    from azure.search.documents import SearchClient
    key = f"search/{cfg.endpoint}/{cfg.index_name}"
    if key in _CLIENT_CACHE:
        return _CLIENT_CACHE[key]
    c = SearchClient(endpoint=cfg.endpoint, index_name=cfg.index_name,
                     credential=AzureKeyCredential(cfg.api_key))
    _CLIENT_CACHE[key] = c
    return c


def ensure_index(cfg: AzureSearchConfig | None = None) -> None:
    """Idempotent: create the index with the expected schema if it doesn't exist."""
    cfg = cfg or AzureSearchConfig.from_env()
    if not cfg.is_configured():
        return
    from azure.search.documents.indexes.models import (
        SearchIndex, SimpleField, SearchableField, SearchFieldDataType,
    )
    admin = _admin_client(cfg)
    try:
        admin.get_index(cfg.index_name)
        return
    except Exception:
        pass
    fields = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True, filterable=True),
        SearchableField(name="section_title", type=SearchFieldDataType.String,
                        analyzer_name="ja.lucene"),
        SearchableField(name="body", type=SearchFieldDataType.String,
                        analyzer_name="ja.lucene"),
        SimpleField(name="keywords", type=SearchFieldDataType.Collection(SearchFieldDataType.String),
                    filterable=True, searchable=True),
        SimpleField(name="source_name", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="page_start", type=SearchFieldDataType.Int32, filterable=True),
        SimpleField(name="page_end", type=SearchFieldDataType.Int32, filterable=True),
        SimpleField(name="ingested_at", type=SearchFieldDataType.DateTimeOffset, filterable=True, sortable=True),
    ]
    admin.create_index(SearchIndex(name=cfg.index_name, fields=fields))


@dataclass(frozen=True)
class IndexedDoc:
    id: str
    section_title: str
    body: str
    keywords: list[str]
    source_name: str
    page_start: int = 0
    page_end: int = 0


def upload_docs(docs: list[IndexedDoc], cfg: AzureSearchConfig | None = None) -> int:
    """Upload docs and return the number that succeeded."""
    cfg = cfg or AzureSearchConfig.from_env()
    if not cfg.is_configured() or not docs:
        return 0
    ensure_index(cfg)
    client = _search_client(cfg)
    from datetime import datetime, timezone
    iso = datetime.now(timezone.utc).isoformat()
    actions = [{
        "id": d.id,
        "section_title": d.section_title,
        "body": d.body,
        "keywords": d.keywords,
        "source_name": d.source_name,
        "page_start": d.page_start,
        "page_end": d.page_end,
        "ingested_at": iso,
    } for d in docs]
    result = client.upload_documents(actions)
    return sum(1 for r in result if getattr(r, "succeeded", False))


def search(query_terms: list[str], top_k: int = 3) -> list[tuple[str, str, float]]:
    """Return list of (section_title, body, score) tuples."""
    cfg = AzureSearchConfig.from_env()
    if not cfg.is_configured() or not query_terms:
        return []
    client = _search_client(cfg)
    query = " ".join(t for t in query_terms if t)
    try:
        results = client.search(
            search_text=query, top=top_k, query_type="simple",
            search_fields=["body", "section_title", "keywords"],
        )
        out: list[tuple[str, str, float]] = []
        for r in results:
            out.append((
                r.get("section_title", "(no title)"),
                r.get("body", ""),
                float(r.get("@search.score", 0.0)),
            ))
        return out
    except Exception:
        return []


def count_docs(cfg: AzureSearchConfig | None = None) -> int:
    cfg = cfg or AzureSearchConfig.from_env()
    if not cfg.is_configured():
        return 0
    try:
        client = _search_client(cfg)
        return client.get_document_count()
    except Exception:
        return 0
