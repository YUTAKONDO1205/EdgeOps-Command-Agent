"""Tests for src/pdf_loader.py — generates a minimal PDF on the fly so we
don't need to ship a fixture binary."""
from __future__ import annotations

import io

from src import pdf_loader


def _make_pdf(pages: list[str]) -> bytes:
    """Build a tiny PDF using pypdf so the chunker has something real to parse."""
    from pypdf import PdfWriter
    # pypdf can't natively write text content, so we construct via reportlab-style.
    # Fall back to a hand-rolled PDF that pypdf can read back text from is hard;
    # instead use pypdf to *write* using its merger of source pages. For this
    # test we use a built-in feature: create empty page then inject content stream.
    writer = PdfWriter()
    for text in pages:
        # Add a blank A4 page
        writer.add_blank_page(width=595, height=842)
    buf = io.BytesIO()
    writer.write(buf)
    # The above produces a valid but textless PDF. For chunker behavior we
    # really need to test on something with extractable text. So construct
    # a tiny PDF by hand with one BT/ET text block per page.
    return _hand_built_pdf(pages)


def _hand_built_pdf(pages: list[str]) -> bytes:
    """Construct a minimal PDF 1.4 with one text block per page."""
    def esc(s: str) -> str:
        # Only escape parens/backslashes; non-ASCII handled by latin-1 fallback
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    objects: list[bytes] = [b""]  # 1-indexed
    contents_ids = []
    page_ids = []
    pages_id_placeholder = None

    def add(obj: bytes) -> int:
        objects.append(obj)
        return len(objects) - 1

    pages_id = 2  # reserve id 2 for /Pages

    # Build content stream + page object for each page
    next_id = 3
    page_obj_ids: list[int] = []
    content_obj_ids: list[int] = []
    for text in pages:
        body_lines = []
        for i, line in enumerate(text.splitlines() or [""]):
            # Encode to latin-1 with replacement so structure stays valid; we
            # don't actually rely on non-ASCII surviving here.
            safe = esc(line.encode("latin-1", errors="replace").decode("latin-1"))
            body_lines.append(f"T* ({safe}) Tj")
        stream_text = "BT /F1 12 Tf 50 800 Td 14 TL " + " ".join(body_lines) + " ET"
        stream_bytes = stream_text.encode("latin-1")
        content_obj = (
            f"<< /Length {len(stream_bytes)} >>\nstream\n".encode("latin-1")
            + stream_bytes
            + b"\nendstream"
        )
        content_obj_ids.append(next_id)
        next_id += 1
        page_obj_ids.append(next_id)
        next_id += 1

    font_id = next_id
    next_id += 1

    # Now actually add objects in order:
    # 1: Catalog
    # 2: Pages
    # 3..: alternating content/page, then font
    # Catalog
    add(b"<< /Type /Catalog /Pages 2 0 R >>")
    # Pages stub — we'll know kids after building content/page pairs
    add(b"")  # placeholder for /Pages
    # Re-add content and page objects in the IDs reserved above
    idx = 3
    for text, cid, pid in zip(pages, content_obj_ids, page_obj_ids):
        # content
        body_lines = []
        for line in text.splitlines() or [""]:
            safe = esc(line.encode("latin-1", errors="replace").decode("latin-1"))
            body_lines.append(f"T* ({safe}) Tj")
        stream_text = "BT /F1 12 Tf 50 800 Td 14 TL " + " ".join(body_lines) + " ET"
        stream_bytes = stream_text.encode("latin-1")
        content_obj = (
            f"<< /Length {len(stream_bytes)} >>\nstream\n".encode("latin-1")
            + stream_bytes
            + b"\nendstream"
        )
        add(content_obj)
        # page
        page_obj = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
            f"/Contents {cid} 0 R >>".encode("latin-1")
        )
        add(page_obj)
    # font
    add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    kids = " ".join(f"{pid} 0 R" for pid in page_obj_ids)
    pages_obj = f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode("latin-1")
    objects[2] = pages_obj

    # Serialize
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = [0]
    for i in range(1, len(objects)):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode("latin-1") + objects[i] + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objects)}\n".encode("latin-1")
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode("latin-1")
    out += b"trailer\n"
    out += f"<< /Size {len(objects)} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode("latin-1")
    return bytes(out)


def test_extract_text_per_page_returns_one_entry_per_page():
    pdf = _hand_built_pdf(["page one body", "page two body"])
    pages = pdf_loader.extract_text_per_page(pdf)
    assert len(pages) == 2
    assert "page one" in pages[0]
    assert "page two" in pages[1]


def test_chunk_pdf_splits_on_heading():
    pdf = _hand_built_pdf([
        "intro line",
        "Chapter 1 vibration\nVibration RMS threshold is 0.20",
        "Chapter 2 temperature\nBearing temperature warning at 45 C",
    ])
    chunks = pdf_loader.chunk_pdf(pdf, source_name="manual.pdf")
    # We expect at least one chunk per heading recognised by _HEADING_PATTERNS.
    titles = [c.section_title for c in chunks]
    assert any("Chapter 1" in t for t in titles)
    assert any("Chapter 2" in t for t in titles)


def test_chunk_pdf_falls_back_to_fixed_size_when_no_headings():
    # Long body with no heading patterns -> single chunk that exceeds the
    # fallback window -> chunker should split it.
    long_text = "A" * 3000
    pdf = _hand_built_pdf([long_text])
    chunks = pdf_loader.chunk_pdf(pdf, source_name="big.pdf", fallback_chunk_size=500)
    # After the no-heading fallback, we should see multiple chunks.
    assert len(chunks) > 1
    assert all(len(c.body) <= 600 for c in chunks)


def test_chunk_pdf_empty_returns_empty_list():
    # Build a PDF with no text-extractable content (just one blank page).
    from pypdf import PdfWriter
    w = PdfWriter()
    w.add_blank_page(width=595, height=842)
    buf = io.BytesIO()
    w.write(buf)
    chunks = pdf_loader.chunk_pdf(buf.getvalue(), source_name="blank.pdf")
    assert chunks == []
