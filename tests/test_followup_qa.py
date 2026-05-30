"""Tests for the grounded follow-up Q&A agent (mock-mode routing)."""
from __future__ import annotations

import pandas as pd

from src import agents, equipment_catalog, risk_engine, signal_analysis


def _pipeline(equipment_id: str = "Motor-02", intensity: str = "critical"):
    df = equipment_catalog.cached_sensor_df(equipment_id, intensity)
    feat = signal_analysis.analyze(df)
    risk = risk_engine.assess(feat, equipment_id=equipment_id)
    return agents.run_pipeline(
        features=feat, risk=risk, image_path=None, inspection_memo="",
        history_summary="", inventory_summary="", equipment_id=equipment_id,
    )


def test_qa_answer_has_required_shape():
    res = _pipeline()
    out = agents.run_followup_qa("この設備はなぜ異常なの？", res)
    assert isinstance(out, dict)
    for key in ("answer", "grounded_in", "confidence", "human_confirmation_required"):
        assert key in out
    assert out["answer"]


def test_cause_question_grounds_in_root_cause():
    res = _pipeline()
    out = agents.run_followup_qa("原因は何ですか", res)
    assert "Root Cause Agent" in out["grounded_in"]


def test_cost_question_returns_yen_figure():
    res = _pipeline("Motor-02", "critical")
    out = agents.run_followup_qa("放置するといくらの損失？コストは？", res)
    assert "¥" in out["answer"]
    assert "business_case" in out["grounded_in"]


def test_deadline_question_returns_hours():
    res = _pipeline("Pump-03", "critical")
    out = agents.run_followup_qa("いつまでに対応すべき？期限は", res)
    assert "時間以内" in out["answer"]
    assert "Action Planning Agent" in out["grounded_in"]


def test_parts_question_grounds_in_inventory():
    res = _pipeline("Motor-02", "critical")
    out = agents.run_followup_qa("必要な部品と在庫は？", res)
    assert "parts_inventory.csv" in out["grounded_in"]


def test_default_falls_back_to_headline_verdict():
    res = _pipeline("Pump-03", "warning")
    out = agents.run_followup_qa("こんにちは", res)
    assert res.equipment_id in out["answer"]
    assert out["confidence"] == "low"
