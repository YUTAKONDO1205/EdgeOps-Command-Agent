"""Smoke tests for the Intake and Governance agents.

Both agents are deterministic in mock mode, so we exercise the shape of their
output and the bookkeeping their downstream consumers depend on (data quality
buckets, fallback tracking, approval-gating flags)."""
from __future__ import annotations

import pandas as pd
import pytest

from src import agents, risk_engine, signal_analysis, utils


@pytest.fixture
def normal_bundle():
    df = pd.read_csv(utils.DATA_DIR / "normal_sensor.csv")
    feats = signal_analysis.analyze(df)
    risk = risk_engine.assess(feats, equipment_id="Pump-03")
    return feats, risk


@pytest.fixture
def critical_bundle():
    df = pd.read_csv(utils.DATA_DIR / "critical_sensor.csv")
    feats = signal_analysis.analyze(df)
    risk = risk_engine.assess(feats, equipment_id="Pump-03")
    return feats, risk


# ── Intake ──────────────────────────────────────────────────────────────

def test_intake_marks_missing_image_and_memo(normal_bundle):
    feats, risk = normal_bundle
    res = agents.run_intake_agent(
        feats, risk,
        equipment_id="Pump-03",
        image_path=None,
        extra_image_paths=None,
        reference_image_path=None,
        inspection_memo="",
    )
    out = res.output
    assert out["data_quality"] in ("good", "acceptable", "degraded")
    assert "primary_image" in out["missing_sources"]
    assert "reference_image" in out["missing_sources"]
    assert "inspection_memo" in out["missing_sources"]
    assert out["sample_count"] == feats.sample_count
    assert abs(out["duration_seconds"] - round(feats.duration_seconds, 2)) < 0.01


def test_intake_promotes_quality_when_inputs_complete(critical_bundle, tmp_path):
    feats, risk = critical_bundle
    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")   # not a real PNG, just a non-empty file
    ref = tmp_path / "ref.png"
    ref.write_bytes(b"\x89PNG\r\n\x1a\n")
    res = agents.run_intake_agent(
        feats, risk,
        equipment_id="Pump-03",
        image_path=img,
        extra_image_paths=None,
        reference_image_path=ref,
        inspection_memo="軸受周辺で異音あり",
    )
    out = res.output
    assert "primary_image" in out["available_sources"]
    assert "reference_image" in out["available_sources"]
    assert "inspection_memo" in out["available_sources"]
    # With all inputs present the quality should not be 'degraded'.
    assert out["data_quality"] in ("good", "acceptable")


# ── Governance ──────────────────────────────────────────────────────────

def test_governance_requires_human_approval_on_critical(critical_bundle):
    feats, risk = critical_bundle
    pipeline = agents.run_pipeline(
        features=feats, risk=risk, image_path=None,
        inspection_memo="memo", history_summary="", inventory_summary="",
    )
    assert pipeline.governance is not None
    gov = pipeline.governance.output
    assert gov["human_approval_required"] is True
    assert gov["auto_executable"] is False
    assert isinstance(gov["approval_checkpoints"], list) and gov["approval_checkpoints"]
    assert isinstance(gov["safety_constraints"], list) and gov["safety_constraints"]


def test_governance_records_fallback_used(critical_bundle, monkeypatch):
    feats, risk = critical_bundle
    # Force mock mode so every agent reports source='mock'. The governance
    # agent should still emit a (possibly empty) fallback_used list — when
    # the *whole* run is mock-mode, no agent tried Azure so the list is
    # empty by design (mock != fallback).
    monkeypatch.setenv("EDGEOPS_USE_MOCK", "true")
    pipeline = agents.run_pipeline(
        features=feats, risk=risk, image_path=None,
        inspection_memo="memo", history_summary="", inventory_summary="",
    )
    assert pipeline.governance is not None
    gov = pipeline.governance.output
    # Mock mode → every agent is source=mock, so fallback_used list is empty
    # (that branch is reserved for *attempted* Azure calls that failed).
    assert isinstance(gov["fallback_used"], list)
    assert gov["fallback_used"] == []
