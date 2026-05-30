"""End-to-end-ish: drive the agents.run_pipeline in mock mode and verify the
artifacts that downstream UIs depend on (work order markdown, report markdown,
risk classification)."""
from __future__ import annotations

import pandas as pd
import pytest

from src import agents, report_generator, risk_engine, signal_analysis, utils


@pytest.fixture
def critical_features():
    df = pd.read_csv(utils.DATA_DIR / "critical_sensor.csv")
    feats = signal_analysis.analyze(df)
    risk = risk_engine.assess(feats)
    return feats, risk


def test_pipeline_yields_all_eight_agents(critical_features):
    feats, risk = critical_features
    pipeline = agents.run_pipeline(
        features=feats, risk=risk, image_path=None,
        inspection_memo="test memo",
        history_summary="（履歴なし）", inventory_summary="（在庫なし）",
    )
    assert pipeline.intake is not None and pipeline.intake.output
    assert pipeline.governance is not None and pipeline.governance.output
    for ar in (pipeline.signal, pipeline.vision, pipeline.manual_rag,
               pipeline.root_cause, pipeline.action_plan, pipeline.whatif):
        assert ar.output, f"empty output for {ar.name}"
    assert risk.risk_level == "Critical"
    # PipelineResult.agents now exposes 8 entries when intake+governance present
    assert len(pipeline.agents) == 8


def test_work_order_markdown_contains_required_sections(critical_features):
    feats, risk = critical_features
    pipeline = agents.run_pipeline(
        features=feats, risk=risk, image_path=None,
        inspection_memo="memo", history_summary="", inventory_summary="",
    )
    md = report_generator.render_work_order(pipeline, equipment_id="Pump-03")
    for section in ("## 作業手順", "## 必要工具", "## 必要部品", "## 安全上の注意"):
        assert section in md, f"missing section {section} in work order"
    assert "Pump-03" in md


def test_management_report_includes_executive_summary(critical_features):
    feats, risk = critical_features
    pipeline = agents.run_pipeline(
        features=feats, risk=risk, image_path=None,
        inspection_memo="memo", history_summary="", inventory_summary="",
    )
    md = report_generator.render_management_report(pipeline, equipment_id="Pump-03")
    assert "エグゼクティブサマリー" in md
    assert "推奨対応" in md


def test_normal_preset_produces_normal_risk():
    df = pd.read_csv(utils.DATA_DIR / "normal_sensor.csv")
    feats = signal_analysis.analyze(df)
    risk = risk_engine.assess(feats)
    assert risk.risk_level == "Normal"


def test_ambiguous_preset_sets_ambiguity_flag():
    df = pd.read_csv(utils.DATA_DIR / "ambiguous_sensor.csv")
    feats = signal_analysis.analyze(df)
    risk = risk_engine.assess(feats)
    # Either ambiguity flag is set, or risk is Warning — both indicate
    # the "human confirmation" path is reachable.
    assert risk.ambiguity_flag or risk.risk_level == "Warning"
