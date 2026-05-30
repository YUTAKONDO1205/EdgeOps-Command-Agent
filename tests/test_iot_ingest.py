"""Tests for src/iot_ingest.py — covers the local JSONL fallback end-to-end
and verifies the frame shaping for the signal pipeline."""
from __future__ import annotations

import time

import pandas as pd

from src import iot_ingest


def _sample(t_offset: float = 0.0, equipment_id: str = "Pump-03") -> dict:
    return {
        "device_id": "spresense-01",
        "equipment_id": equipment_id,
        "timestamp": time.time() + t_offset,
        "vibration_x": 0.01,
        "vibration_y": 0.02,
        "vibration_z": 0.03,
        "sound_level": 45.0,
        "temperature": 40.0,
        "current": 2.0,
    }


def test_send_and_fetch_round_trip(tmp_side_files):
    events = [_sample(i * 0.001) for i in range(10)]
    res = iot_ingest.send_events(events)
    assert res["sent"] == 10
    assert res["backend"] == "local_jsonl"

    fetched = iot_ingest.fetch_recent(equipment_id="Pump-03")
    assert fetched.source == "local_jsonl"
    assert fetched.record_count == 10
    assert isinstance(fetched.df, pd.DataFrame)


def test_fetch_normalizes_timestamp_to_relative(tmp_side_files):
    base = 1_700_000_000.0
    iot_ingest.send_events([
        {**_sample(), "timestamp": base + i * 0.001}
        for i in range(5)
    ])
    fetched = iot_ingest.fetch_recent()
    assert fetched.df["timestamp"].iloc[0] == 0.0
    assert fetched.df["timestamp"].iloc[-1] > 0


def test_fetch_filters_by_equipment(tmp_side_files):
    iot_ingest.send_events([_sample(equipment_id="A"), _sample(equipment_id="B"),
                            _sample(equipment_id="A")])
    fetched = iot_ingest.fetch_recent(equipment_id="A")
    assert fetched.record_count == 2
    assert (fetched.df["equipment_id"] == "A").all()


def test_fetch_empty_returns_empty_source(tmp_side_files):
    res = iot_ingest.fetch_recent()
    assert res.source == "empty"
    assert res.record_count == 0
    assert res.df.empty


def test_active_source_reflects_state(tmp_side_files):
    assert iot_ingest.active_source() == "none"
    iot_ingest.send_events([_sample()])
    assert iot_ingest.active_source() == "local_jsonl"


def test_reset_local_stream_clears_jsonl(tmp_side_files):
    iot_ingest.send_events([_sample() for _ in range(3)])
    n = iot_ingest.reset_local_stream()
    assert n == 3
    assert not iot_ingest.LOCAL_STREAM_JSONL.exists()


def test_missing_columns_filled_with_zeros(tmp_side_files):
    iot_ingest.send_events([
        # Intentionally omit vibration_x and current
        {"equipment_id": "Pump-03", "timestamp": 1.0,
         "vibration_y": 0.1, "vibration_z": 0.2,
         "sound_level": 40, "temperature": 30},
        {"equipment_id": "Pump-03", "timestamp": 1.001,
         "vibration_y": 0.1, "vibration_z": 0.2,
         "sound_level": 40, "temperature": 30},
    ])
    df = iot_ingest.fetch_recent(equipment_id="Pump-03").df
    assert "vibration_x" in df.columns
    assert "current" in df.columns
    assert (df["vibration_x"] == 0.0).all()
