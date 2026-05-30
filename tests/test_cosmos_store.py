"""Tests for src/cosmos_store.py — local JSONL fallback only."""
from __future__ import annotations

import json
from pathlib import Path

from src import cosmos_store


def test_run_round_trip(tmp_side_files):
    doc = cosmos_store.record_run(
        "Pump-03", risk_level="Warning", health_score=72,
        primary_concern="温度上昇", summary="summary",
        action_plan={"priority": "High"}, root_cause=None,
    )
    assert doc["_backend"] == "local"
    assert doc["doc_type"] == "run"
    assert doc["equipment_id"] == "Pump-03"
    runs = cosmos_store.latest_runs_across_equipment(limit=5)
    assert any(r["id"] == doc["id"] for r in runs)


def test_approval_and_alert_separated_by_doc_type(tmp_side_files):
    cosmos_store.record_approval("X", artifact="Work Order", action="承認",
                                 comment="ok", risk_level="Critical")
    cosmos_store.record_alert("X", risk_level="Critical", channel="teams",
                              ok=True, detail="sent")
    events = cosmos_store.recent_for_equipment("X", limit=20)
    types = {e["doc_type"] for e in events}
    assert types == {"approval", "alert"}


def test_recent_orders_newest_first(tmp_side_files):
    for i in range(3):
        cosmos_store.record_run(
            "Y", risk_level="Normal", health_score=90+i,
            primary_concern=f"c{i}", summary=str(i),
            action_plan=None, root_cause=None,
        )
    events = cosmos_store.recent_for_equipment("Y", limit=3)
    assert [e["summary"] for e in events] == ["2", "1", "0"]


def test_filter_by_doc_type(tmp_side_files):
    cosmos_store.record_run("Z", risk_level="Normal", health_score=100,
                            primary_concern="-", summary="r",
                            action_plan=None, root_cause=None)
    cosmos_store.record_approval("Z", artifact="Work Order", action="承認",
                                 comment="", risk_level="Normal")
    only_runs = cosmos_store.recent_for_equipment("Z", doc_types=["run"], limit=10)
    assert all(e["doc_type"] == "run" for e in only_runs)
    assert len(only_runs) == 1


def test_jsonl_is_valid_jsonlines(tmp_side_files):
    cosmos_store.record_run("J", risk_level="Critical", health_score=10,
                            primary_concern="x", summary="y",
                            action_plan=None, root_cause=None)
    text = Path(cosmos_store._LOCAL_LOG).read_text(encoding="utf-8")
    for line in text.splitlines():
        json.loads(line)  # raises if not valid JSON
