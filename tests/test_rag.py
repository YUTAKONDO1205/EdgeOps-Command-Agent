"""Tests for src/rag.py — local + uploaded backends. Azure AI Search is
exercised separately in test_ai_search via a stubbed client."""
from __future__ import annotations

import pytest

from src import rag


def test_active_backend_defaults_to_local(tmp_side_files):
    assert rag.active_backend() == "local_txt"


def test_active_backend_switches_after_upload(tmp_side_files):
    rag.add_uploaded_chunks([("Chapter Test", "振動 RMS が 0.4G を超えた場合は即時停止")])
    assert rag.active_backend() == "local+uploaded"


def test_uploaded_chunks_appear_in_search(tmp_side_files):
    rag.add_uploaded_chunks([
        ("一意セクション名XYZ", "ZZZ_UNIQUE_KEYWORD_QQQ が検出された場合の対応を記述"),
    ])
    hits = rag.search(["ZZZ_UNIQUE_KEYWORD_QQQ"], top_k=5)
    titles = [h.section_title for h in hits]
    assert "一意セクション名XYZ" in titles


def test_build_query_from_findings_extracts_hints():
    terms = rag.build_query_from_findings(
        primary_concern="軸受帯域エネルギー増加",
        evidence_lines=["振動RMS=0.45 G", "軸受温度 52℃"],
    )
    assert "振動" in terms or "軸受" in terms
    assert "点検" in terms  # always added


def test_clear_uploaded_chunks_resets(tmp_side_files):
    rag.add_uploaded_chunks([("a", "x" * 50), ("b", "y" * 50)])
    assert rag.uploaded_chunk_count() == 2
    n = rag.clear_uploaded_chunks()
    assert n == 2
    assert rag.uploaded_chunk_count() == 0


def test_search_with_empty_terms_returns_empty(tmp_side_files):
    assert rag.search([]) == []
    assert rag.search(["", "   "]) == []


def test_ingest_pdf_bytes_when_azure_not_configured(tmp_side_files):
    # Build a tiny valid PDF inline using the helper from the pdf_loader tests.
    from tests.test_pdf_loader import _hand_built_pdf
    pdf = _hand_built_pdf([
        "Chapter 1 vibration\nVibration RMS at 0.40G is critical.",
    ])
    result = rag.ingest_pdf_bytes(pdf, source_name="t.pdf")
    assert result["chunks_extracted"] >= 1
    assert result["azure_status"] == "not_configured"
    assert result["pushed_to_azure"] == 0
    assert result["added_to_local"] >= 1
