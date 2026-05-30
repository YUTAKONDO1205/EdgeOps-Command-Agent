"""Tests for src/equipment_catalog.py — verifies that:
- every equipment is present and has a consistent kind/threshold story
- sensor synthesis produces data that maps to the expected risk level
  through risk_engine.assess(equipment_id=...) for the matching intensity
- preset registration covers the full catalog × intensity matrix
- per-equipment risk thresholds are honoured (motor tolerates more current,
  fan more sound, compressor more temperature)
"""
from __future__ import annotations

import pandas as pd
import pytest

from src import equipment_catalog, risk_engine, signal_analysis, utils


# ───────────────────────────────────────────────────────────────────────
# Catalog shape
# ───────────────────────────────────────────────────────────────────────

EXPECTED_IDS = {"Pump-03", "Pump-01", "Motor-02", "Fan-04", "Compressor-05"}


def test_catalog_contains_all_expected_equipment():
    assert {e.id for e in equipment_catalog.list_equipment()} == EXPECTED_IDS


def test_each_spec_has_required_metadata():
    for eq in equipment_catalog.list_equipment():
        assert eq.label
        assert eq.kind in {"pump", "motor", "fan", "compressor"}
        assert eq.location
        assert eq.description
        assert eq.rotation_hz > 0
        assert eq.normal_vib_amp > 0
        assert eq.bearing_center_hz > 0
        assert eq.downstream
        # Image paths exist for every intensity key
        assert set(eq.image_paths.keys()) >= {"normal", "warning", "critical", "ambiguous"}


def test_get_unknown_raises():
    with pytest.raises(KeyError):
        equipment_catalog.get("does-not-exist")


# ───────────────────────────────────────────────────────────────────────
# Sensor generation determinism + shape
# ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("equipment_id", sorted(EXPECTED_IDS))
@pytest.mark.parametrize("intensity", ["normal", "warning", "critical", "ambiguous"])
def test_generate_sensor_df_shape(equipment_id, intensity):
    df = equipment_catalog.generate_sensor_df(equipment_id, intensity)
    assert isinstance(df, pd.DataFrame)
    assert len(df) == equipment_catalog.N_SAMPLES
    required = {"timestamp", "vibration_x", "vibration_y", "vibration_z",
                "sound_level", "temperature", "current"}
    assert required <= set(df.columns)


def test_generate_sensor_df_is_deterministic():
    a = equipment_catalog.generate_sensor_df("Motor-02", "warning")
    b = equipment_catalog.generate_sensor_df("Motor-02", "warning")
    pd.testing.assert_frame_equal(a, b)


def test_seed_changes_output():
    a = equipment_catalog.generate_sensor_df("Pump-01", "normal", seed=1)
    b = equipment_catalog.generate_sensor_df("Pump-01", "normal", seed=2)
    assert not a.equals(b)


# ───────────────────────────────────────────────────────────────────────
# Risk classification works end-to-end for every (equipment, intensity)
# ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("equipment_id", sorted(EXPECTED_IDS))
def test_normal_intensity_classifies_as_normal(equipment_id):
    df = equipment_catalog.generate_sensor_df(equipment_id, "normal")
    feats = signal_analysis.analyze(df)
    risk = risk_engine.assess(feats, equipment_id=equipment_id)
    assert risk.risk_level == "Normal", \
        f"{equipment_id} normal intensity should classify as Normal, got {risk.risk_level}"


@pytest.mark.parametrize("equipment_id", sorted(EXPECTED_IDS))
def test_critical_intensity_classifies_as_critical(equipment_id):
    df = equipment_catalog.generate_sensor_df(equipment_id, "critical")
    feats = signal_analysis.analyze(df)
    risk = risk_engine.assess(feats, equipment_id=equipment_id)
    assert risk.risk_level == "Critical", \
        f"{equipment_id} critical intensity should classify as Critical, got {risk.risk_level}"


@pytest.mark.parametrize("equipment_id", sorted(EXPECTED_IDS))
def test_warning_intensity_classifies_at_least_warning(equipment_id):
    df = equipment_catalog.generate_sensor_df(equipment_id, "warning")
    feats = signal_analysis.analyze(df)
    risk = risk_engine.assess(feats, equipment_id=equipment_id)
    assert risk.risk_level in {"Warning", "Critical"}, \
        f"{equipment_id} warning intensity should classify at least as Warning, got {risk.risk_level}"


# ───────────────────────────────────────────────────────────────────────
# Per-equipment threshold overrides
# ───────────────────────────────────────────────────────────────────────

def test_thresholds_for_unknown_falls_back_to_default():
    th = risk_engine.thresholds_for("does-not-exist")
    assert th is risk_engine.DEFAULT_THRESHOLDS


def test_motor_tolerates_higher_current_than_pump():
    pump_th = risk_engine.thresholds_for("Pump-03")
    motor_th = risk_engine.thresholds_for("Motor-02")
    assert motor_th.current_crit > pump_th.current_crit


def test_fan_tolerates_louder_baseline():
    pump_th = risk_engine.thresholds_for("Pump-03")
    fan_th = risk_engine.thresholds_for("Fan-04")
    assert fan_th.sound_warn > pump_th.sound_warn
    assert fan_th.sound_crit > pump_th.sound_crit


def test_compressor_tolerates_higher_temperature():
    pump_th = risk_engine.thresholds_for("Pump-03")
    compressor_th = risk_engine.thresholds_for("Compressor-05")
    assert compressor_th.temp_warn > pump_th.temp_warn
    assert compressor_th.temp_crit > pump_th.temp_crit


def test_pump_baseline_under_default_thresholds_would_misclassify_compressor():
    """Sanity check: a compressor's 'normal' frame would trip pump-tuned
    thresholds. The per-equipment override is what keeps it Normal."""
    df = equipment_catalog.generate_sensor_df("Compressor-05", "normal")
    feats = signal_analysis.analyze(df)
    with_override = risk_engine.assess(feats, equipment_id="Compressor-05")
    without_override = risk_engine.assess(feats, equipment_id=None)
    # With the equipment id, normal stays normal; with pump-default thresholds
    # the compressor's hot baseline pushes it at least to Warning.
    assert with_override.risk_level == "Normal"
    assert without_override.risk_level in {"Warning", "Critical"}


# ───────────────────────────────────────────────────────────────────────
# Preset registry
# ───────────────────────────────────────────────────────────────────────

def test_iter_presets_yields_full_matrix():
    keys = list(equipment_catalog.iter_presets())
    assert len(keys) == len(EXPECTED_IDS) * 4
    for key, equip_id, intensity in keys:
        assert key == f"{equip_id}:{intensity}"
        assert equip_id in EXPECTED_IDS
        assert intensity in {"normal", "warning", "critical", "ambiguous"}


def test_DEMO_PRESETS_exposes_legacy_and_new_keys():
    keys = set(utils.DEMO_PRESETS.keys())
    # Legacy short keys (Pump-03 aliases)
    assert {"normal", "warning", "critical", "ambiguous"} <= keys
    # Every catalog entry has its own keyed variant too
    for eid in EXPECTED_IDS:
        for intensity in ("normal", "warning", "critical", "ambiguous"):
            assert f"{eid}:{intensity}" in keys


def test_legacy_preset_targets_pump03():
    p = utils.DEMO_PRESETS["warning"]
    assert p.equipment_id == "Pump-03"
    assert p.intensity == "warning"


def test_new_preset_loader_returns_dataframe():
    p = utils.DEMO_PRESETS["Motor-02:critical"]
    df = p.sensor_loader()
    assert isinstance(df, pd.DataFrame)
    assert "vibration_z" in df.columns


def test_inspection_memo_varies_by_kind():
    pump_memo = equipment_catalog.inspection_memo("Pump-03", "critical")
    compressor_memo = equipment_catalog.inspection_memo("Compressor-05", "critical")
    assert pump_memo != compressor_memo
    # Compressor memo should reference compressor-specific vocabulary
    assert any(token in compressor_memo for token in ("吐出", "圧", "ロード"))


def test_preset_label_includes_equipment_and_intensity():
    label = equipment_catalog.preset_label("Fan-04", "critical")
    assert "Fan-04" in label
    assert "Critical" in label


# ───────────────────────────────────────────────────────────────────────
# Icon + accent metadata used by the new equipment pickers
# ───────────────────────────────────────────────────────────────────────

def test_every_spec_has_icon_and_accent():
    for eq in equipment_catalog.list_equipment():
        assert eq.kind_icon
        # Accent must look like a CSS hex color
        assert eq.kind_accent.startswith("#")
        assert len(eq.kind_accent) in (4, 7)


def test_distinct_icons_per_kind():
    icons = {eq.kind: eq.kind_icon for eq in equipment_catalog.list_equipment()}
    # We have 4 distinct kinds in the catalog; their icons must be distinct
    # so the picker is readable at a glance.
    assert len(set(icons.values())) == len(icons)


def test_distinct_accents_per_kind():
    accents = {eq.kind: eq.kind_accent for eq in equipment_catalog.list_equipment()}
    assert len(set(accents.values())) == len(accents)
