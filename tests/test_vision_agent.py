"""Tests for the upgraded Vision Inspection Agent.

Covers:
- mock output schema (overview, regions, severity, confidence_score,
  signal_correlation, comparison_to_normal)
- per-equipment-kind region vocabulary
- severity escalation on Critical
- multi-image LLMClient method (mocked Azure call captures payload shape)
- _normalize_vision_output fills missing keys without crashing
- run_vision_agent forwards equipment_id + reference_image + features
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src import agents, equipment_catalog, risk_engine, signal_analysis, utils


def _risk(equipment_id: str, intensity: str):
    df = equipment_catalog.cached_sensor_df(equipment_id, intensity)
    feats = signal_analysis.analyze(df)
    return feats, risk_engine.assess(feats, equipment_id=equipment_id)


# ───────────────────────────────────────────────────────────────────────
# Mock schema
# ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("intensity", ["normal", "warning", "critical"])
def test_mock_vision_returns_full_schema(intensity):
    feats, risk = _risk("Pump-03", intensity)
    out = agents._mock_vision(risk, memo="t", equipment_id="Pump-03",
                              has_reference=True)
    for key in ("overview", "regions", "signal_correlation",
                "comparison_to_normal", "overall_confidence_score",
                "confidence", "visual_findings",
                "recommended_additional_shots", "human_confirmation_required"):
        assert key in out, f"missing key {key} on {intensity} mock"
    assert isinstance(out["regions"], list)
    assert all(isinstance(r, dict) for r in out["regions"])


def test_mock_vision_regions_use_kind_vocabulary():
    feats, risk = _risk("Compressor-05", "warning")
    out = agents._mock_vision(risk, memo="", equipment_id="Compressor-05",
                              has_reference=False)
    vocab = set(equipment_catalog.region_vocabulary("Compressor-05"))
    for r in out["regions"]:
        assert r["region_id"] in vocab, f"unknown region for compressor: {r['region_id']}"


def test_mock_vision_critical_promotes_severe():
    feats, risk = _risk("Pump-03", "critical")
    out = agents._mock_vision(risk, memo="", equipment_id="Pump-03",
                              has_reference=False)
    severities = [r["severity"] for r in out["regions"]]
    assert "severe" in severities, "Critical risk should include a severe region"


def test_mock_vision_normal_has_no_anomaly():
    feats, risk = _risk("Pump-03", "normal")
    out = agents._mock_vision(risk, memo="", equipment_id="Pump-03",
                              has_reference=False)
    assert all(r["severity"] == "normal" for r in out["regions"])


def test_mock_vision_comparison_filled_only_when_reference():
    feats, risk = _risk("Pump-03", "warning")
    with_ref = agents._mock_vision(risk, memo="", equipment_id="Pump-03",
                                   has_reference=True)
    without_ref = agents._mock_vision(risk, memo="", equipment_id="Pump-03",
                                      has_reference=False)
    assert with_ref["comparison_to_normal"] != ""
    assert without_ref["comparison_to_normal"] == ""


def test_mock_vision_visual_findings_backward_compat():
    """Legacy consumers (Work Order template) still read this flat list."""
    feats, risk = _risk("Motor-02", "warning")
    out = agents._mock_vision(risk, memo="", equipment_id="Motor-02",
                              has_reference=False)
    assert out["visual_findings"]
    for line in out["visual_findings"]:
        assert isinstance(line, str)


# ───────────────────────────────────────────────────────────────────────
# Normalization
# ───────────────────────────────────────────────────────────────────────

def test_normalize_fills_missing_keys():
    feats, risk = _risk("Pump-03", "warning")
    partial = {"overview": "test"}
    normalised = agents._normalize_vision_output(
        partial, risk=risk, equipment_id="Pump-03", has_reference=False)
    assert "regions" in normalised
    assert "signal_correlation" in normalised
    assert "confidence" in normalised
    assert "visual_findings" in normalised
    assert normalised["human_confirmation_required"] is True


def test_normalize_synthesises_visual_findings_from_regions():
    feats, risk = _risk("Pump-03", "warning")
    partial = {
        "overview": "test",
        "regions": [
            {"region_id": "bearing-housing", "observation": "油滲み"},
            {"region_id": "bolt-upper-row", "observation": "錆び"},
        ],
        "visual_findings": [],  # empty -> should be synthesised
    }
    out = agents._normalize_vision_output(partial, risk=risk,
                                          equipment_id="Pump-03", has_reference=False)
    assert out["visual_findings"]  # filled in from regions
    assert any("油滲み" in line for line in out["visual_findings"])


def test_normalize_with_reference_provides_default_comparison():
    feats, risk = _risk("Pump-03", "warning")
    partial = {"overview": "x"}
    out = agents._normalize_vision_output(partial, risk=risk,
                                          equipment_id="Pump-03", has_reference=True)
    assert out["comparison_to_normal"]


# ───────────────────────────────────────────────────────────────────────
# Prompt context helpers
# ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("equipment_id,kind_token", [
    ("Pump-03", "pump"),
    ("Motor-02", "motor"),
    ("Fan-04", "fan"),
    ("Compressor-05", "compressor"),
])
def test_vision_prompt_context_uses_kind(equipment_id, kind_token):
    kind, checklist, regions = agents._vision_prompt_context(equipment_id)
    assert kind_token in kind
    assert checklist  # non-empty checklist
    assert regions    # non-empty region vocab string


def test_signal_correlation_hint_mentions_primary_concern():
    feats, risk = _risk("Pump-03", "critical")
    hint = agents._signal_correlation_hint(feats, risk)
    assert risk.primary_concern in hint
    assert "振動RMS" in hint


# ───────────────────────────────────────────────────────────────────────
# Multi-image client method
# ───────────────────────────────────────────────────────────────────────

def test_complete_with_images_sends_multimodal_payload(monkeypatch, tmp_path):
    # Patch out the Azure SDK so we never make a network call.
    fake_resp = MagicMock()
    fake_resp.choices = [MagicMock(message=MagicMock(content='{"ok":true}'))]
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_resp

    client = agents.LLMClient(config=agents.LLMConfig(
        endpoint="https://x.openai.azure.com/", api_key="k", deployment="d",
        vision_deployment="vd", api_version="2024-08-01-preview",
    ))
    client._client = fake_client

    # Create two tiny PNG files via PIL so _encode_image succeeds.
    from PIL import Image
    p1 = tmp_path / "primary.jpg"
    p2 = tmp_path / "extra.jpg"
    Image.new("RGB", (50, 50), (200, 200, 200)).save(p1, "JPEG")
    Image.new("RGB", (50, 50), (100, 100, 100)).save(p2, "JPEG")

    client.complete_with_images(
        system="sys", user_text="hi",
        images=[(p1, "IMAGE 1 — primary"), (p2, "IMAGE 2 — extra")],
    )
    args, kwargs = fake_client.chat.completions.create.call_args
    messages = kwargs["messages"]
    user_content = messages[1]["content"]
    assert isinstance(user_content, list)
    # Expect: [text(user), text(IMAGE 1 caption), image, text(IMAGE 2 caption), image]
    assert sum(1 for c in user_content if c["type"] == "image_url") == 2
    assert any("IMAGE 1" in c.get("text", "") for c in user_content if c["type"] == "text")
    assert any("IMAGE 2" in c.get("text", "") for c in user_content if c["type"] == "text")


def test_encode_image_resizes_to_max_side(tmp_path):
    from PIL import Image
    p = tmp_path / "big.jpg"
    Image.new("RGB", (4000, 3000), (180, 180, 180)).save(p, "JPEG")
    data_url = agents._encode_image(p, max_side=512)
    # Decode the data URL back and check dimensions
    import base64, io
    payload = data_url.split(",", 1)[1]
    decoded = base64.b64decode(payload)
    img = Image.open(io.BytesIO(decoded))
    assert max(img.size) <= 512


# ───────────────────────────────────────────────────────────────────────
# run_vision_agent integration in mock mode
# ───────────────────────────────────────────────────────────────────────

def test_run_vision_agent_in_mock_returns_new_schema():
    feats, risk = _risk("Fan-04", "critical")
    result = agents.run_vision_agent(
        image_path=None, inspection_memo="test",
        risk=risk, equipment_id="Fan-04", features=feats,
    )
    assert result.source == "mock"
    out = result.output
    assert "regions" in out
    assert "signal_correlation" in out
    assert "overall_confidence_score" in out
    # Critical -> at least one severe region
    severities = [r["severity"] for r in out["regions"]]
    assert "severe" in severities


def test_run_vision_agent_forwards_reference_image(monkeypatch, tmp_path):
    # Force mock mode (no LLM call) and verify the comparison_to_normal field
    # populates whenever has_reference is true.
    monkeypatch.setenv("EDGEOPS_USE_MOCK", "true")
    from PIL import Image
    ref = tmp_path / "ref.jpg"
    Image.new("RGB", (50, 50), (255, 255, 255)).save(ref, "JPEG")

    feats, risk = _risk("Pump-03", "warning")
    result = agents.run_vision_agent(
        image_path=None,  # no primary image — agent still runs in mock
        inspection_memo="",
        risk=risk,
        equipment_id="Pump-03",
        reference_image_path=ref,
        features=feats,
    )
    assert result.output["comparison_to_normal"]
