"""
Rule-based risk engine.

The LLM agents interpret and narrate, but the *risk classification* itself
is rule-based so it stays predictable and auditable — a hard requirement
for any real maintenance workflow.

Rules are derived from data/maintenance_manual.txt (Sections 1-4).
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from .signal_analysis import SignalFeatures


# Default thresholds — calibrated for centrifugal pumps (Pump-03 / Pump-01).
# Other equipment kinds override via EQUIPMENT_THRESHOLDS below.
VIB_RMS_WARN = 0.20
VIB_RMS_CRIT = 0.30
VIB_PEAK_CRIT = 0.80
SOUND_WARN = 48.0
SOUND_CRIT = 55.0
TEMP_WARN = 45.0
TEMP_CRIT = 50.0
TEMP_TREND_CRIT = 0.5  # ℃/s sustained rise
CURRENT_CRIT = 2.5
BEARING_BAND_WARN = 0.25
BEARING_BAND_CRIT = 0.40


@dataclass(frozen=True)
class RiskThresholds:
    """Per-equipment risk thresholds. ``None`` falls back to the module
    default. We override per-kind because a fan's "loud" baseline and
    a compressor's "hot" baseline would constantly trip pump-tuned rules."""
    vib_rms_warn: float = VIB_RMS_WARN
    vib_rms_crit: float = VIB_RMS_CRIT
    vib_peak_crit: float = VIB_PEAK_CRIT
    sound_warn: float = SOUND_WARN
    sound_crit: float = SOUND_CRIT
    temp_warn: float = TEMP_WARN
    temp_crit: float = TEMP_CRIT
    temp_trend_crit: float = TEMP_TREND_CRIT
    current_crit: float = CURRENT_CRIT
    bearing_band_warn: float = BEARING_BAND_WARN
    bearing_band_crit: float = BEARING_BAND_CRIT


DEFAULT_THRESHOLDS = RiskThresholds()

# Per-equipment overrides. Tuned so:
#  - Motor-02 tolerates higher steady-state current (3.5A nominal)
#  - Fan-04 tolerates a louder baseline (Vベルト騒音)
#  - Compressor-05 tolerates higher steady-state temperature
EQUIPMENT_THRESHOLDS: dict[str, RiskThresholds] = {
    "Motor-02": RiskThresholds(
        current_crit=5.0,
        sound_warn=50.0, sound_crit=57.0,
        temp_warn=50.0, temp_crit=58.0,
    ),
    "Fan-04": RiskThresholds(
        sound_warn=55.0, sound_crit=62.0,
        current_crit=2.5,
        vib_rms_warn=0.15, vib_rms_crit=0.25,
    ),
    "Compressor-05": RiskThresholds(
        temp_warn=55.0, temp_crit=65.0,
        sound_warn=55.0, sound_crit=62.0,
        current_crit=6.0,
        vib_rms_warn=0.25, vib_rms_crit=0.40,
    ),
}


def thresholds_for(equipment_id: str | None) -> RiskThresholds:
    if equipment_id is None:
        return DEFAULT_THRESHOLDS
    return EQUIPMENT_THRESHOLDS.get(equipment_id, DEFAULT_THRESHOLDS)


@dataclass
class RiskFinding:
    indicator: str            # e.g. "vibration_rms"
    value: float
    threshold: float
    level: str                # "Normal" | "Warning" | "Critical"
    note: str                 # human-readable evidence

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RiskAssessment:
    risk_level: str           # "Normal" | "Warning" | "Critical"
    health_score: int         # 0-100
    findings: list[RiskFinding]
    ambiguity_flag: bool      # True when signals are mixed / inconclusive
    primary_concern: str      # short label for UI

    def evidence_lines(self) -> list[str]:
        return [f.note for f in self.findings if f.level != "Normal"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_level": self.risk_level,
            "health_score": self.health_score,
            "findings": [f.to_dict() for f in self.findings],
            "ambiguity_flag": self.ambiguity_flag,
            "primary_concern": self.primary_concern,
        }


def _classify(value: float, warn: float, crit: float, *, lower_is_better: bool = True) -> str:
    if lower_is_better:
        if value >= crit:
            return "Critical"
        if value >= warn:
            return "Warning"
        return "Normal"
    if value <= crit:
        return "Critical"
    if value <= warn:
        return "Warning"
    return "Normal"


_LEVEL_ORDER = {"Normal": 0, "Warning": 1, "Critical": 2}


def assess(features: SignalFeatures, *, equipment_id: str | None = None) -> RiskAssessment:
    """Rule-based classification. Pass ``equipment_id`` to apply per-equipment
    threshold overrides (Motor-02 / Fan-04 / Compressor-05). Without it, the
    pump-tuned defaults are used."""
    th = thresholds_for(equipment_id)
    findings: list[RiskFinding] = []

    # Vibration RMS
    lvl = _classify(features.vibration_rms, th.vib_rms_warn, th.vib_rms_crit)
    findings.append(RiskFinding(
        indicator="vibration_rms",
        value=features.vibration_rms,
        threshold=th.vib_rms_warn,
        level=lvl,
        note=f"振動RMS={features.vibration_rms:.3f} G（警告閾値 {th.vib_rms_warn}, 危険閾値 {th.vib_rms_crit}）",
    ))

    # Vibration peak (impact)
    if features.vibration_peak >= th.vib_peak_crit:
        findings.append(RiskFinding(
            indicator="vibration_peak",
            value=features.vibration_peak,
            threshold=th.vib_peak_crit,
            level="Critical",
            note=f"短時間ピーク {features.vibration_peak:.2f} G が {th.vib_peak_crit} G を超過。インパクト性の異常を疑う",
        ))

    # Sound
    lvl = _classify(features.sound_max_db, th.sound_warn, th.sound_crit)
    findings.append(RiskFinding(
        indicator="sound_max_db",
        value=features.sound_max_db,
        threshold=th.sound_warn,
        level=lvl,
        note=f"最大音響レベル={features.sound_max_db:.1f} dB（警告 {th.sound_warn}, 危険 {th.sound_crit}）",
    ))

    # Temperature absolute
    lvl = _classify(features.temperature_max_c, th.temp_warn, th.temp_crit)
    findings.append(RiskFinding(
        indicator="temperature_max_c",
        value=features.temperature_max_c,
        threshold=th.temp_warn,
        level=lvl,
        note=f"最高温度={features.temperature_max_c:.1f}℃（警告 {th.temp_warn}, 危険 {th.temp_crit}）",
    ))

    # Temperature trend
    if features.temperature_trend_c_per_s >= th.temp_trend_crit:
        findings.append(RiskFinding(
            indicator="temperature_trend",
            value=features.temperature_trend_c_per_s,
            threshold=th.temp_trend_crit,
            level="Critical",
            note=f"温度上昇傾向 {features.temperature_trend_c_per_s:.2f}℃/s。急速な温度上昇を検出",
        ))

    # Current
    if features.current_mean_a >= th.current_crit:
        findings.append(RiskFinding(
            indicator="current_mean_a",
            value=features.current_mean_a,
            threshold=th.current_crit,
            level="Critical",
            note=f"電流平均 {features.current_mean_a:.2f} A が定格 {th.current_crit} A を超過",
        ))

    # Bearing band energy ratio
    lvl = _classify(features.bearing_band_energy_ratio, th.bearing_band_warn, th.bearing_band_crit)
    if lvl != "Normal":
        findings.append(RiskFinding(
            indicator="bearing_band_energy_ratio",
            value=features.bearing_band_energy_ratio,
            threshold=th.bearing_band_warn,
            level=lvl,
            note=(
                f"100-300Hz帯域のエネルギー比率={features.bearing_band_energy_ratio:.2f}。"
                "軸受異常の典型周波数帯に振動が集中"
            ),
        ))

    overall = max((f.level for f in findings), key=lambda l: _LEVEL_ORDER[l], default="Normal")

    # Health score: scale subtraction by per-equipment thresholds so a
    # compressor's hot baseline (50°C nominal) doesn't ding it on score.
    score = 100
    score -= min(int(max(features.vibration_rms - th.vib_rms_warn * 0.5, 0) * 200), 35)
    score -= int(max(features.sound_max_db - (th.sound_warn - 3.0), 0) * 1.2)
    score -= int(max(features.temperature_max_c - (th.temp_warn - 7.0), 0) * 1.5)
    score -= int(max(features.current_mean_a - (th.current_crit - 0.3), 0) * 30)
    score -= int(features.bearing_band_energy_ratio * 40)
    score = max(0, min(100, score))

    criticals = sum(1 for f in findings if f.level == "Critical")
    warnings = sum(1 for f in findings if f.level == "Warning")
    isolated_impact = (
        features.vibration_rms < th.vib_rms_warn
        and features.vibration_peak >= 0.4
    )
    ambiguity = (criticals == 0 and warnings >= 1) or isolated_impact

    primary = _primary_concern(features, findings)

    return RiskAssessment(
        risk_level=overall,
        health_score=score,
        findings=findings,
        ambiguity_flag=ambiguity,
        primary_concern=primary,
    )


def _primary_concern(features: SignalFeatures, findings: list[RiskFinding]) -> str:
    # Pick the most severe non-normal finding's indicator, with a friendly name
    severity_sorted = sorted(findings, key=lambda f: -_LEVEL_ORDER[f.level])
    label_map = {
        "vibration_rms": "振動レベル上昇",
        "vibration_peak": "インパクト振動",
        "sound_max_db": "音響レベル上昇",
        "temperature_max_c": "温度上昇",
        "temperature_trend": "急激な温度上昇",
        "current_mean_a": "電流値上昇",
        "bearing_band_energy_ratio": "軸受帯域エネルギー増加",
    }
    for f in severity_sorted:
        if f.level != "Normal":
            return label_map.get(f.indicator, f.indicator)
    return "特記事項なし"
