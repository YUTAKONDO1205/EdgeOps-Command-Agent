"""Tests for src/vision_annotator.py and the bbox plumbing.

These prove that:
- annotate() returns valid data URLs for overlay/enhanced/crops
- regions without bboxes are skipped gracefully
- severity drives the outline colour (verified by sampling pixels)
- _normalize_vision_output back-fills missing bboxes from the catalog map
- run_vision_agent attaches evidence_images when given a real image path
- the placeholder JPEGs we ship are valid render targets
"""
from __future__ import annotations

import base64
import io
from pathlib import Path

import pytest
from PIL import Image

from src import agents, equipment_catalog, risk_engine, signal_analysis, vision_annotator


def _decode_data_url(data_url: str) -> Image.Image:
    assert data_url.startswith("data:image/jpeg;base64,")
    raw = base64.b64decode(data_url.split(",", 1)[1])
    return Image.open(io.BytesIO(raw))


def _png_bytes(size=(400, 300), color=(180, 180, 200)) -> bytes:
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def _risk(equipment_id, intensity):
    df = equipment_catalog.cached_sensor_df(equipment_id, intensity)
    feats = signal_analysis.analyze(df)
    return feats, risk_engine.assess(feats, equipment_id=equipment_id)


# ───────────────────────────────────────────────────────────────────────
# bbox layouts
# ───────────────────────────────────────────────────────────────────────

def test_default_bbox_returns_known_layout():
    bbox = equipment_catalog.default_bbox("Pump-03", "bearing-housing")
    assert len(bbox) == 4
    for c in bbox:
        assert 0.0 <= c <= 1.0


def test_default_bbox_fallback_for_unknown_region():
    bbox = equipment_catalog.default_bbox("Pump-03", "no-such-region")
    # Falls back to a centred rectangle
    assert bbox == (0.25, 0.25, 0.75, 0.75)


def test_default_bbox_fallback_for_unknown_equipment():
    bbox = equipment_catalog.default_bbox("Unknown-99", "bearing-housing")
    # Unknown equipment falls back to pump layout
    assert len(bbox) == 4


# ───────────────────────────────────────────────────────────────────────
# annotate()
# ───────────────────────────────────────────────────────────────────────

def test_annotate_returns_overlay_and_crops_with_valid_data_urls():
    img_bytes = _png_bytes((640, 480))
    regions = [
        {"region_id": "bolt-upper-row", "bbox": [0.2, 0.3, 0.7, 0.4],
         "severity": "minor", "confidence_score": 65},
        {"region_id": "bearing-housing", "bbox": [0.25, 0.4, 0.5, 0.7],
         "severity": "severe", "confidence_score": 88},
    ]
    out = vision_annotator.annotate(img_bytes, regions)
    assert out["overlay"]
    assert out["enhanced"]
    assert out["base_size"] == [640, 480]
    assert out["rendered_region_count"] == 2
    overlay_img = _decode_data_url(out["overlay"])
    assert overlay_img.size == (640, 480)
    # Per-region crops keyed by region_id
    assert set(out["crops"].keys()) == {"bolt-upper-row", "bearing-housing"}
    for url in out["crops"].values():
        crop_img = _decode_data_url(url)
        # Thumbnail caps the long side at 256
        assert max(crop_img.size) <= 256


def test_annotate_skips_regions_without_bbox():
    img_bytes = _png_bytes()
    regions = [
        {"region_id": "no-bbox", "severity": "minor"},  # missing bbox
        {"region_id": "good", "bbox": [0.1, 0.1, 0.5, 0.5], "severity": "minor"},
    ]
    out = vision_annotator.annotate(img_bytes, regions)
    assert out["rendered_region_count"] == 1
    assert "good" in out["crops"]
    assert "no-bbox" not in out["crops"]


def test_annotate_skips_zero_size_bbox():
    img_bytes = _png_bytes()
    regions = [{"region_id": "tiny", "bbox": [0.5, 0.5, 0.5, 0.5], "severity": "minor"}]
    out = vision_annotator.annotate(img_bytes, regions)
    assert out["rendered_region_count"] == 0


def test_annotate_clamps_out_of_range_coords():
    img_bytes = _png_bytes((200, 200))
    regions = [{"region_id": "wide", "bbox": [-0.5, -0.3, 1.5, 1.3],
                "severity": "moderate", "confidence_score": 70}]
    out = vision_annotator.annotate(img_bytes, regions)
    assert out["rendered_region_count"] == 1
    crop = _decode_data_url(out["crops"]["wide"])
    # Crop should cover most of the image after clamping
    assert crop.size[0] > 50 and crop.size[1] > 50


def test_annotate_resizes_oversized_images():
    img_bytes = _png_bytes((4000, 3000))
    regions = [{"region_id": "r", "bbox": [0.1, 0.1, 0.5, 0.5], "severity": "minor"}]
    out = vision_annotator.annotate(img_bytes, regions, max_side=800)
    assert max(out["base_size"]) <= 800


def test_annotate_handles_corrupt_image():
    out = vision_annotator.annotate(b"not-an-image", [])
    assert out["overlay"] is None
    assert out["error"] == "could_not_open_image"


def test_annotate_severity_colours_distinct():
    """The overlay should encode severity visually. We sample pixels around
    each box and verify the dominant outline colour matches the severity."""
    img_bytes = _png_bytes((600, 200), color=(255, 255, 255))
    regions = [
        {"region_id": "a", "bbox": [0.05, 0.2, 0.30, 0.85],
         "severity": "severe", "confidence_score": 90},
        {"region_id": "b", "bbox": [0.40, 0.2, 0.65, 0.85],
         "severity": "minor",  "confidence_score": 55},
        {"region_id": "c", "bbox": [0.75, 0.2, 0.95, 0.85],
         "severity": "normal", "confidence_score": 70},
    ]
    out = vision_annotator.annotate(img_bytes, regions, enhance=False)
    overlay = _decode_data_url(out["overlay"]).convert("RGB")
    px = overlay.load()

    def sample_outline(region_idx: int) -> tuple[int, int, int]:
        # Sample 1px outside the top-left corner of the bbox where the
        # rectangle outline lives.
        bbox = regions[region_idx]["bbox"]
        x0 = int(bbox[0] * overlay.size[0]) + 1
        y0 = int(bbox[1] * overlay.size[1]) + 1
        return px[x0, y0]

    severe_px = sample_outline(0)
    minor_px = sample_outline(1)
    normal_px = sample_outline(2)
    # Severe outline should be red-dominant; normal should be green-dominant
    assert severe_px[0] > severe_px[1] and severe_px[0] > severe_px[2]
    assert normal_px[1] > normal_px[0]
    # The three outlines must not all be the same colour
    assert len({severe_px, minor_px, normal_px}) >= 2


# ───────────────────────────────────────────────────────────────────────
# Normalization + agent integration
# ───────────────────────────────────────────────────────────────────────

def test_normalize_backfills_missing_bbox():
    feats, risk = _risk("Pump-03", "warning")
    partial = {
        "regions": [
            {"region_id": "bearing-housing", "observation": "x"},  # no bbox
        ],
    }
    out = agents._normalize_vision_output(partial, risk=risk,
                                          equipment_id="Pump-03", has_reference=False)
    region = out["regions"][0]
    assert "bbox" in region
    assert len(region["bbox"]) == 4


def test_mock_vision_emits_bbox_for_every_region():
    feats, risk = _risk("Compressor-05", "critical")
    out = agents._mock_vision(risk, memo="", equipment_id="Compressor-05",
                              has_reference=False)
    for r in out["regions"]:
        assert "bbox" in r
        assert len(r["bbox"]) == 4
        assert all(0.0 <= c <= 1.0 for c in r["bbox"])


def test_run_vision_agent_attaches_evidence_images(tmp_path):
    # Render a placeholder JPEG locally so the annotator has something to draw.
    img_path = tmp_path / "fake_pump.jpg"
    Image.new("RGB", (640, 480), (200, 210, 220)).save(img_path, "JPEG")
    feats, risk = _risk("Pump-03", "critical")
    result = agents.run_vision_agent(
        image_path=img_path,
        inspection_memo="test",
        risk=risk,
        equipment_id="Pump-03",
        features=feats,
    )
    evidence = result.output.get("evidence_images")
    assert isinstance(evidence, dict)
    assert evidence["overlay"], "overlay should be a base64 data URL"
    assert evidence["overlay"].startswith("data:image/jpeg;base64,")
    assert evidence["enhanced"]
    assert evidence["crops"], "crops should be non-empty for mock critical run"
    assert evidence["rendered_region_count"] >= 1


def test_run_vision_agent_no_image_means_no_evidence(tmp_path):
    feats, risk = _risk("Pump-03", "warning")
    result = agents.run_vision_agent(
        image_path=None, inspection_memo="", risk=risk,
        equipment_id="Pump-03", features=feats,
    )
    # Without a primary image the annotator is skipped.
    assert "evidence_images" not in result.output


def test_ships_placeholder_images_are_annotatable():
    """The placeholder images in assets/ must be openable by the annotator —
    they're what local-only demos render against."""
    from src import utils
    for name in ("pump_normal.jpg", "motor_critical.jpg",
                 "fan_warning.jpg", "compressor_critical.jpg"):
        path = utils.ASSETS_DIR / name
        if not path.exists():
            continue  # tolerate partial generation
        regions = [{"region_id": "x", "bbox": [0.1, 0.1, 0.6, 0.6],
                    "severity": "minor", "confidence_score": 50}]
        out = vision_annotator.annotate_path(path, regions)
        assert out["overlay"]
