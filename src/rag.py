"""
Manual retrieval facade.

3 backends, picked at search time:

1. **Azure AI Search** — used automatically when ``AZURE_SEARCH_ENDPOINT`` /
   ``AZURE_SEARCH_API_KEY`` are set. Index schema lives in ``src/ai_search.py``.
2. **In-memory uploaded PDFs** — chunks added by the UI's PDF uploader land
   here. Useful for "drop a manual PDF and immediately query it" demos.
3. **Local keyword RAG** over ``data/maintenance_manual.txt`` — the original
   demo path. No external services required.

The original public surface (``search(terms, top_k) -> list[RetrievalResult]``
and ``build_query_from_findings``) is preserved so callers in
``src/agents.py`` don't need to change.
"""
from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Iterable

from .utils import DATA_DIR


MANUAL_PATH = DATA_DIR / "maintenance_manual.txt"
SECTION_SEPARATOR = re.compile(r"^─{5,}\s*$", re.MULTILINE)


@dataclass(frozen=True)
class ManualChunk:
    section_title: str
    body: str
    keywords: tuple[str, ...]


@dataclass(frozen=True)
class RetrievalResult:
    section_title: str
    body: str
    score: float


_KEYWORD_HINTS: dict[str, tuple[str, ...]] = {
    "振動": ("振動", "vibration", "RMS", "ピーク", "G"),
    "音響": ("音響", "騒音", "dB", "異音"),
    "温度": ("温度", "温度上昇", "℃"),
    "電流": ("電流", "A", "過負荷"),
    "軸受": ("軸受", "ベアリング", "bearing"),
    "ボルト": ("ボルト", "締結", "緩み", "トルク"),
    "点検": ("点検", "確認", "手順"),
    "報告": ("報告", "報告書", "記載"),
}


# ────────────────────────────────────────────────────────────────────────
# Local manual loader (data/maintenance_manual.txt)
# ────────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _load_chunks() -> list[ManualChunk]:
    if not MANUAL_PATH.exists():
        return []
    text = MANUAL_PATH.read_text(encoding="utf-8")
    chunks: list[ManualChunk] = []
    current_title = ""
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer, current_title
        body = "\n".join(buffer).strip()
        if body:
            kws: set[str] = set()
            for hint, words in _KEYWORD_HINTS.items():
                if any(w in body for w in words) or hint in body:
                    kws.update(words)
                    kws.add(hint)
            chunks.append(ManualChunk(section_title=current_title or "(無題)", body=body, keywords=tuple(sorted(kws))))
        buffer = []

    for line in text.splitlines():
        if SECTION_SEPARATOR.match(line):
            continue
        if line.startswith("第") and ("章" in line or "節" in line):
            flush()
            current_title = line.strip()
            continue
        if line.startswith("[") and "]" in line:
            buffer.append(line)
            continue
        buffer.append(line)
    flush()
    return chunks


# ────────────────────────────────────────────────────────────────────────
# In-memory store for uploaded PDFs (UI handoff target)
# ────────────────────────────────────────────────────────────────────────

_UPLOADED_CHUNKS: list[ManualChunk] = []
_UPLOADED_LOCK = Lock()


def _derive_keywords(body: str) -> tuple[str, ...]:
    kws: set[str] = set()
    for hint, words in _KEYWORD_HINTS.items():
        if hint in body or any(w in body for w in words):
            kws.update(words)
            kws.add(hint)
    return tuple(sorted(kws))


def add_uploaded_chunks(items: Iterable[tuple[str, str]]) -> int:
    """Register PDF-derived chunks. Each item is (section_title, body).
    Returns the number of chunks added."""
    added = 0
    with _UPLOADED_LOCK:
        for title, body in items:
            if not body.strip():
                continue
            _UPLOADED_CHUNKS.append(ManualChunk(
                section_title=title or "(no title)",
                body=body.strip(),
                keywords=_derive_keywords(body),
            ))
            added += 1
    return added


def uploaded_chunk_count() -> int:
    with _UPLOADED_LOCK:
        return len(_UPLOADED_CHUNKS)


def clear_uploaded_chunks() -> int:
    with _UPLOADED_LOCK:
        n = len(_UPLOADED_CHUNKS)
        _UPLOADED_CHUNKS.clear()
        return n


# ────────────────────────────────────────────────────────────────────────
# Scoring helpers shared by local + in-memory backends
# ────────────────────────────────────────────────────────────────────────

def _score_chunks(chunks: list[ManualChunk], normalized: list[str]) -> list[tuple[float, ManualChunk]]:
    scored: list[tuple[float, ManualChunk]] = []
    for chunk in chunks:
        body_lower = chunk.body.lower()
        score = 0.0
        for term in normalized:
            t = term.lower()
            if not t:
                continue
            count = body_lower.count(t)
            if count:
                score += 1.0 + 0.3 * count
            if t in (k.lower() for k in chunk.keywords):
                score += 0.5
        if score > 0:
            scored.append((score, chunk))
    return scored


# ────────────────────────────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────────────────────────────

def active_backend() -> str:
    """Return which backend search() will use right now."""
    try:
        from . import ai_search
        if ai_search.is_configured():
            return "azure_ai_search"
    except Exception:
        pass
    if uploaded_chunk_count() > 0:
        return "local+uploaded"
    return "local_txt"


def search(query_terms: list[str], top_k: int = 3) -> list[RetrievalResult]:
    """Top-k retrieval. Dispatches across backends transparently."""
    normalized = [q.strip() for q in (query_terms or []) if q and q.strip()]
    if not normalized:
        return []

    # 1) Azure AI Search if configured.
    try:
        from . import ai_search
        if ai_search.is_configured():
            ai_hits = ai_search.search(normalized, top_k=top_k)
            if ai_hits:
                return [
                    RetrievalResult(section_title=t, body=b, score=round(float(s), 2))
                    for (t, b, s) in ai_hits
                ]
            # Fall through to local if Azure index is empty.
    except Exception:
        # If Azure side throws, degrade gracefully.
        pass

    # 2) Local (txt) + in-memory uploaded PDFs.
    chunks = list(_load_chunks())
    with _UPLOADED_LOCK:
        chunks.extend(_UPLOADED_CHUNKS)
    if not chunks:
        return []
    scored = _score_chunks(chunks, normalized)
    scored.sort(key=lambda x: -x[0])
    return [
        RetrievalResult(section_title=c.section_title, body=c.body, score=round(s, 2))
        for s, c in scored[:top_k]
    ]


def build_query_from_findings(primary_concern: str, evidence_lines: list[str]) -> list[str]:
    """Pull retrieval keywords out of the risk-engine findings."""
    terms = {primary_concern}
    for line in evidence_lines:
        for hint in _KEYWORD_HINTS:
            if hint in line:
                terms.add(hint)
    terms.update({"点検", "手順"})
    return [t for t in terms if t]


# ────────────────────────────────────────────────────────────────────────
# Ingestion helpers used by the UI
# ────────────────────────────────────────────────────────────────────────

def ingest_pdf_bytes(pdf_bytes: bytes, *, source_name: str = "uploaded.pdf",
                     push_to_azure: bool = True) -> dict[str, int | str]:
    """One-shot helper: chunk a PDF, register it in the in-memory store,
    and (optionally) push it to Azure AI Search when configured.

    Returns a small dict the UI can render verbatim.
    """
    from .pdf_loader import chunk_pdf
    pdf_chunks = chunk_pdf(pdf_bytes, source_name=source_name)
    added_local = add_uploaded_chunks(((c.section_title, c.body) for c in pdf_chunks))

    pushed_azure = 0
    azure_status = "skipped"
    if push_to_azure:
        try:
            from . import ai_search
            if ai_search.is_configured():
                docs = [
                    ai_search.IndexedDoc(
                        id=str(uuid.uuid4()),
                        section_title=c.section_title,
                        body=c.body,
                        keywords=list(_derive_keywords(c.body)),
                        source_name=source_name,
                        page_start=c.page_start,
                        page_end=c.page_end,
                    )
                    for c in pdf_chunks
                ]
                pushed_azure = ai_search.upload_docs(docs)
                azure_status = "uploaded" if pushed_azure else "no_docs_uploaded"
            else:
                azure_status = "not_configured"
        except Exception as exc:  # pragma: no cover
            azure_status = f"error: {exc}"

    return {
        "source_name": source_name,
        "chunks_extracted": len(pdf_chunks),
        "added_to_local": added_local,
        "pushed_to_azure": pushed_azure,
        "azure_status": azure_status,
    }


def seed_azure_search_from_local_manual() -> dict[str, int | str]:
    """Push the local maintenance_manual.txt sections into Azure AI Search.
    Lets the demo "promote" the local corpus to the cloud index in one click."""
    chunks = _load_chunks()
    try:
        from . import ai_search
    except Exception as exc:
        return {"status": f"import_error: {exc}", "uploaded": 0}
    if not ai_search.is_configured():
        return {"status": "not_configured", "uploaded": 0}
    docs = [
        ai_search.IndexedDoc(
            id=str(uuid.uuid4()),
            section_title=c.section_title,
            body=c.body,
            keywords=list(c.keywords),
            source_name="maintenance_manual.txt",
        )
        for c in chunks
    ]
    n = ai_search.upload_docs(docs)
    return {"status": "ok" if n else "uploaded_zero", "uploaded": n}
