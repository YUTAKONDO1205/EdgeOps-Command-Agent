"""
PDF → セクション分割テキスト。

Manual RAG 用に PDF をテキスト抽出し、見出しまたは固定長でチャンクに分割します。

設計
----
- 重い NLP は使わない。pypdf でテキスト抽出だけ行い、
  「第○章」「Section X」「数字. 」 などの見出しっぽい行で分割。
- 見出しが見つからなければ 1200 文字ごとに分割。
- 既存の ``rag.ManualChunk`` と同じ形に整形して返すので、
  ローカル RAG / Azure AI Search どちらにも流し込めます。
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


@dataclass(frozen=True)
class PdfChunk:
    section_title: str
    body: str
    page_start: int
    page_end: int


_HEADING_PATTERNS = [
    re.compile(r"^\s*第[0-9０-９一二三四五六七八九十]+[章節]"),
    re.compile(r"^\s*Section\s+[0-9IVXLC]+", re.IGNORECASE),
    re.compile(r"^\s*Chapter\s+[0-9IVXLC]+", re.IGNORECASE),
    re.compile(r"^\s*\[[0-9０-９]+(\.[0-9０-９]+)*\]"),
    re.compile(r"^\s*[0-9０-９]+\.\s+\S"),
]


def _looks_like_heading(line: str) -> bool:
    if not line.strip():
        return False
    if len(line) > 80:
        return False
    return any(pat.match(line) for pat in _HEADING_PATTERNS)


def extract_text_per_page(pdf_bytes: bytes) -> list[str]:
    """Return one entry per page. Empty pages are kept as empty strings to keep
    page indexing aligned with the source PDF."""
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(pdf_bytes))
    pages: list[str] = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    return pages


def chunk_pdf(
    pdf_bytes: bytes,
    *,
    source_name: str = "uploaded.pdf",
    fallback_chunk_size: int = 1200,
) -> list[PdfChunk]:
    """Convert PDF bytes into PdfChunks. Splits on detected headings,
    falls back to fixed-size windows when no headings are detected."""
    pages = extract_text_per_page(pdf_bytes)
    if not any(pages):
        return []

    chunks: list[PdfChunk] = []
    current_title = f"{source_name} (冒頭)"
    current_lines: list[str] = []
    current_start: int = 1
    current_page: int = 1

    def flush(end_page: int) -> None:
        body = "\n".join(line for line in current_lines if line.strip())
        if body.strip():
            chunks.append(PdfChunk(
                section_title=current_title.strip(),
                body=body.strip(),
                page_start=current_start,
                page_end=end_page,
            ))

    for page_idx, page_text in enumerate(pages, start=1):
        for line in page_text.splitlines():
            if _looks_like_heading(line):
                flush(end_page=current_page)
                current_lines = []
                current_title = line.strip()
                current_start = page_idx
            else:
                current_lines.append(line)
            current_page = page_idx
    flush(end_page=len(pages))

    # If no real heading-based split happened, fall back to fixed-size windows
    # so retrieval still gets reasonable chunks.
    if len(chunks) <= 1 and chunks and len(chunks[0].body) > fallback_chunk_size * 2:
        big = chunks[0]
        chunks = []
        text = big.body
        for i in range(0, len(text), fallback_chunk_size):
            piece = text[i:i + fallback_chunk_size]
            chunks.append(PdfChunk(
                section_title=f"{source_name} (chunk {i // fallback_chunk_size + 1})",
                body=piece.strip(),
                page_start=big.page_start,
                page_end=big.page_end,
            ))

    return chunks


def chunk_pdf_path(path: str | Path, **kwargs) -> list[PdfChunk]:
    p = Path(path)
    return chunk_pdf(p.read_bytes(), source_name=p.name, **kwargs)
