"""Tests for raw-data ingestion (arbitrary CSV -> canonical schema)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src import raw_ingest, signal_analysis


def test_auto_detect_common_english_aliases():
    cols = ["time", "accel_x", "accel_y", "accel_z", "dB", "temp_C", "amps"]
    m = raw_ingest.auto_detect_mapping(cols)
    assert m["timestamp"] == "time"
    assert m["vibration_x"] == "accel_x"
    assert m["vibration_y"] == "accel_y"
    assert m["vibration_z"] == "accel_z"
    assert m["sound_level"] == "dB"
    assert m["temperature"] == "temp_C"
    assert m["current"] == "amps"


def test_auto_detect_japanese_and_units():
    cols = ["時刻(s)", "振動Z (g)", "騒音 [dB]", "温度(℃)", "電流(A)"]
    m = raw_ingest.auto_detect_mapping(cols)
    assert m["timestamp"] == "時刻(s)"
    assert m["vibration_z"] == "振動Z (g)"
    assert m["sound_level"] == "騒音 [dB]"
    assert m["temperature"] == "温度(℃)"
    assert m["current"] == "電流(A)"


def test_lone_vibration_column_maps_to_primary_axis():
    m = raw_ingest.auto_detect_mapping(["t", "vibration", "noise"])
    assert m["vibration_z"] == "vibration"
    assert m["sound_level"] == "noise"
    # no x/y present
    assert m["vibration_x"] is None and m["vibration_y"] is None


def test_each_source_column_claimed_once():
    m = raw_ingest.auto_detect_mapping(["accel_z", "temperature"])
    assigned = [c for c in m.values() if c is not None]
    assert len(assigned) == len(set(assigned))


def test_infer_sample_rate_from_numeric_seconds():
    # 0.000, 0.001, 0.002 ... -> 1000 Hz
    df = pd.DataFrame({"time": np.arange(0, 1.0, 0.001), "vibration_z": np.zeros(1000)})
    fs = raw_ingest.infer_sample_rate(df, "time")
    assert fs == 1000.0


def test_infer_sample_rate_from_datetime():
    ts = pd.date_range("2026-01-01", periods=50, freq="20ms")  # 50 Hz
    df = pd.DataFrame({"timestamp": ts.astype(str), "vib": np.zeros(50)})
    fs = raw_ingest.infer_sample_rate(df, "timestamp")
    assert fs is not None and abs(fs - 50.0) < 0.5


def test_apply_mapping_produces_canonical_columns_and_warns_on_missing_primary():
    df = pd.DataFrame({"t": [0, 1, 2], "dB": [40, 41, 42], "temp": [30, 31, 32]})
    m = raw_ingest.auto_detect_mapping(list(df.columns))
    res = raw_ingest.apply_mapping(df, m, sample_rate_hz=10.0)
    assert "sound_level" in res.canonical_df.columns
    assert "temperature" in res.canonical_df.columns
    assert not res.has_primary_vibration
    assert "vibration_z" in res.missing_channels
    assert any("vibration_z" in w for w in res.warnings)


def test_ingest_roundtrips_into_analyze():
    # A real-ish raw frame with non-canonical names + 500 Hz timestamp.
    n = 1000
    t = np.arange(n) / 500.0
    rng = np.random.default_rng(0)
    df = pd.DataFrame({
        "time_s": t,
        "accel_z": 0.1 * np.sin(2 * np.pi * 50 * t) + 0.01 * rng.standard_normal(n),
        "mic_dB": 55 + rng.standard_normal(n),
        "temp_C": np.linspace(30, 35, n),
        "motor_amps": np.full(n, 3.2),
    })
    res = raw_ingest.ingest(df)
    assert res.sample_rate_inferred is True
    assert abs(res.sample_rate_hz - 500.0) < 1.0
    assert res.has_primary_vibration

    feats = signal_analysis.analyze(res.canonical_df, fs=res.sample_rate_hz)
    # Non-zero because we actually mapped the channels.
    assert feats.vibration_rms > 0
    assert feats.sound_max_db > 0
    assert feats.temperature_max_c >= 35 - 1
    assert feats.current_mean_a > 0
    assert feats.sample_count == n
