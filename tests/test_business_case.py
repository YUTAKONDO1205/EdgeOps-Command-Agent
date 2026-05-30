"""Tests for the deterministic ROI / business-impact estimator.

These lock the headline numbers so the figures shown in the UI and the
management report stay auditable and don't silently drift.
"""
from __future__ import annotations

import importlib

import pytest

from src import business_case


def _reload_with_cost(monkeypatch, value: str | None):
    if value is None:
        monkeypatch.delenv("EDGEOPS_LINE_STOP_COST_JPY_PER_HOUR", raising=False)
    else:
        monkeypatch.setenv("EDGEOPS_LINE_STOP_COST_JPY_PER_HOUR", value)
    return business_case


def test_critical_uses_equipment_specific_history():
    # Motor-02's only Critical event in failure_history.csv is 7h.
    impact = business_case.estimate("Motor-02", "Critical")
    assert impact.expected_downtime_hours == pytest.approx(7.0)
    assert impact.escalation_probability == 1.0
    # 7h * 300,000 + parts 15,600 + 50% premium 7,800
    assert impact.unplanned_stop_cost == 2_100_000
    assert impact.run_to_failure_cost == 2_123_400
    # gross avoided = run_to_failure - parts(planned) = 2,123,400 - 15,600
    assert impact.gross_avoided_cost == 2_107_800
    assert impact.expected_avoided_cost == 2_107_800  # prob 1.0


def test_motor02_bearing_is_a_lead_time_risk():
    impact = business_case.estimate("Motor-02", "Critical")
    assert impact.lead_time_risk is True
    assert impact.parts and impact.parts[0].out_of_stock is True
    assert impact.parts[0].lead_time_days == 5


def test_warning_scales_by_escalation_probability():
    impact = business_case.estimate("Fan-04", "Warning")
    assert impact.escalation_probability == 0.4
    # expected = gross * 0.4 (allow rounding)
    assert impact.expected_avoided_cost == round(impact.gross_avoided_cost * 0.4)
    assert impact.expected_avoided_cost < impact.gross_avoided_cost


def test_normal_has_zero_avoided_cost():
    impact = business_case.estimate("Pump-03", "Normal")
    assert impact.expected_downtime_hours == 0.0
    assert impact.expected_avoided_cost == 0
    assert "¥0" in impact.headline_one_liner()


def test_cost_per_hour_is_env_tunable(monkeypatch):
    bc = _reload_with_cost(monkeypatch, "500000")
    impact = bc.estimate("Pump-03", "Critical")
    assert impact.line_stop_cost_per_hour == 500_000
    assert impact.unplanned_stop_cost == round(impact.expected_downtime_hours * 500_000)


def test_markdown_block_is_self_contained():
    md = business_case.estimate("Compressor-05", "Critical").to_markdown_block()
    assert "ビジネスインパクト" in md
    assert "期待回避コスト" in md
    assert "¥" in md
    assert "前提:" in md


def test_unknown_equipment_degrades_gracefully():
    impact = business_case.estimate("Nonexistent-99", "Critical")
    # No history -> default downtime, no parts, but still a valid figure.
    assert impact.expected_downtime_hours > 0
    assert impact.run_to_failure_cost >= 0
    assert impact.parts == []
