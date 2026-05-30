"""
Equipment catalog + per-equipment sensor synthesis.

Why this module exists
----------------------
The original demo shipped 4 fixed sensor CSVs for Pump-03. To make the
Command Center reflect a real multi-asset shop floor, we need:

- a registry of every monitored equipment (with sensible characteristics
  per kind: pump / motor / fan / compressor),
- a way to produce plausible sensor data for any equipment at any
  intensity (normal / warning / critical / ambiguous), and
- downstream-impact metadata so the impact analysis card works for
  every asset, not just Pump-03.

Sensor synthesis follows the same model as ``data/generate_demo_data.py``
(baseline rotation + noise + bearing-fault harmonics + impacts), but
parametrised by the equipment kind so each asset has its own
"personality": fans are loud, compressors run hot, motors carry more
current, etc. All assets share the existing risk-engine schema; per-
equipment threshold overrides live in ``risk_engine.EQUIPMENT_THRESHOLDS``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from .utils import ASSETS_DIR, DATA_DIR


SAMPLE_RATE_HZ = 1000
N_SAMPLES = 4096


@dataclass(frozen=True)
class EquipmentSpec:
    id: str
    label: str
    kind: str           # "pump" | "motor" | "fan" | "compressor"
    location: str
    description: str
    rotation_hz: float
    normal_vib_amp: float          # baseline RMS-ish amplitude on the primary axis
    normal_sound_db: float
    normal_temp_c: float
    normal_current_a: float
    bearing_center_hz: float       # center frequency of the bearing-fault band
    downstream: tuple[str, ...]
    image_paths: dict[str, Path]   # intensity -> image path

    @property
    def normal_state_summary(self) -> str:
        return (
            f"{self.kind.upper()} / {self.rotation_hz:.0f}Hz / "
            f"基準 振動 {self.normal_vib_amp:.2f}G / 温度 {self.normal_temp_c:.0f}℃ / "
            f"音響 {self.normal_sound_db:.0f}dB"
        )

    @property
    def kind_icon(self) -> str:
        """Single-glyph icon for compact pickers. Emoji so we don't ship fonts."""
        return KIND_ICONS.get(self.kind, "⚙")

    @property
    def kind_accent(self) -> str:
        """Hex color associated with the equipment kind — used by chips/borders."""
        return KIND_ACCENTS.get(self.kind, "#64748b")


# Per-kind iconography. Picked for high contrast in monochrome terminals
# *and* recognisability in colour UIs.
KIND_ICONS: dict[str, str] = {
    "pump": "💧",
    "motor": "⚙",
    "fan": "🌀",
    "compressor": "🛢",
}

KIND_ACCENTS: dict[str, str] = {
    "pump": "#2563eb",        # blue — water
    "motor": "#a855f7",       # purple — electrical
    "fan": "#0891b2",         # cyan — airflow
    "compressor": "#f97316",  # orange — heat / pressure
}


# Equipment-kind inspection checklists. Injected into the Vision Agent prompt
# so the model knows *what to look for* in each photo rather than searching
# generically for "anomalies." Wording matches the language a Japanese
# maintenance technician would use in a field report.
INSPECTION_CHECKLISTS: dict[str, list[str]] = {
    "pump": [
        "固定ボルト周辺の変色 / 錆び / 緩み兆候",
        "軸受ハウジング表面の油滲み・漏れ跡",
        "メカニカルシール周辺の漏出",
        "カップリングカバーの破損・変形",
        "配管接続フランジの漏れ・継手の変色",
        "塗装剥がれ・熱変色（特に軸受側）",
    ],
    "motor": [
        "端子箱の焼損・変色・カバー破損",
        "冷却フィン間の粉塵堆積",
        "ファンカバー（後端）の破損・通風阻害",
        "ケーブルグランド周辺の被覆劣化・変色",
        "通風口（ベンチレーション）の塵詰まり",
        "本体表面の熱変色・塗装の焦げ",
    ],
    "fan": [
        "Vベルトのひび割れ・伸び・滑り跡",
        "羽根の変形・欠け・付着物",
        "保護ガード網の損傷・歪み",
        "軸受ハウジングのオイル滲み",
        "羽根バランスのずれを示す異物・付着粉",
        "プーリ偏摩耗・ベルトミスアライメント",
    ],
    "compressor": [
        "圧力計の指針位置（緑/黄/赤帯）",
        "オイル覗き窓の油位・油の色",
        "安全弁周辺の油・煤・吹出し跡",
        "ベルトカバー / シリンダヘッドの破損・変形",
        "配管継手・ドレンバルブの漏れ",
        "冷却フィン汚れ・タンク下のオイルパドル",
    ],
}


def inspection_checklist(equipment_id: str) -> list[str]:
    """Return the kind-specific list of inspection points used by the
    Vision Agent prompt. Falls back to the pump checklist for unknowns."""
    try:
        kind = get(equipment_id).kind
    except KeyError:
        kind = "pump"
    return INSPECTION_CHECKLISTS.get(kind, INSPECTION_CHECKLISTS["pump"])


# Valid region_ids the Vision Agent is allowed to emit. Constrained so the
# UI can render consistent badges + the data is comparable across runs.
REGION_VOCABULARY: dict[str, list[str]] = {
    "pump": ["bolt-upper-row", "bolt-lower-row", "bearing-housing", "mechanical-seal",
             "shaft-coupling", "casing-surface", "pipe-flange", "drain-port", "other"],
    "motor": ["terminal-box", "cooling-fins", "fan-cover", "cable-gland",
              "ventilation-slots", "frame-surface", "shaft-end", "name-plate", "other"],
    "fan": ["v-belt", "blade-tip", "blade-hub", "guard-mesh", "bearing-housing",
            "pulley", "shaft-coupling", "frame-surface", "other"],
    "compressor": ["pressure-gauge", "oil-sight-glass", "safety-valve", "belt-cover",
                   "cylinder-head", "pipe-fitting", "drain-valve", "cooling-fins",
                   "tank-surface", "status-lamp", "other"],
}


def region_vocabulary(equipment_id: str) -> list[str]:
    try:
        kind = get(equipment_id).kind
    except KeyError:
        kind = "pump"
    return REGION_VOCABULARY.get(kind, REGION_VOCABULARY["pump"])


# Normalized bounding-box layouts per (kind, intensity, region_id).
# Coordinates are (x0, y0, x1, y1) in [0, 1]; x grows right, y grows down.
#
# Because the demo photos under ``assets/<kind>_<intensity>.png`` use
# different framings per intensity (normal vs warning vs critical are
# distinct shots of distinct units), each intensity has its own set of
# coordinates. ``default_bbox`` falls back to "normal" when an intensity
# is unknown.
BBOX_LAYOUTS: dict[str, dict[str, dict[str, tuple[float, float, float, float]]]] = {
    "pump": {
        # normal: pump body on the LEFT, motor on the RIGHT, silver flex
        # pipe on the far left, top discharge flange visible.
        "normal": {
            "bearing-housing":  (0.42, 0.20, 0.55, 0.42),
            "shaft-coupling":   (0.45, 0.25, 0.58, 0.40),
            "mechanical-seal":  (0.40, 0.30, 0.50, 0.45),
            "casing-surface":   (0.10, 0.10, 0.45, 0.65),
            "bolt-upper-row":   (0.10, 0.05, 0.35, 0.25),
            "bolt-lower-row":   (0.13, 0.78, 0.50, 0.92),
            "pipe-flange":      (0.00, 0.30, 0.18, 0.55),
            "drain-port":       (0.25, 0.55, 0.40, 0.72),
        },
        # warning: pump on LEFT, motor on RIGHT, dark suction pipe far left.
        "warning": {
            "bearing-housing":  (0.42, 0.20, 0.55, 0.55),
            "shaft-coupling":   (0.48, 0.25, 0.62, 0.50),
            "mechanical-seal":  (0.32, 0.20, 0.42, 0.50),
            "casing-surface":   (0.05, 0.05, 0.45, 0.70),
            "bolt-upper-row":   (0.05, 0.00, 0.30, 0.18),
            "bolt-lower-row":   (0.05, 0.78, 0.55, 0.92),
            "pipe-flange":      (0.00, 0.20, 0.10, 0.60),
            "drain-port":       (0.10, 0.55, 0.30, 0.80),
        },
        # critical: motor on LEFT, pump on RIGHT, orange discharge flange
        # top-right, dark oil leak below the pump body.
        "critical": {
            "bearing-housing":  (0.18, 0.18, 0.32, 0.45),
            "shaft-coupling":   (0.10, 0.20, 0.22, 0.45),
            "mechanical-seal":  (0.32, 0.28, 0.42, 0.45),
            "casing-surface":   (0.32, 0.20, 0.62, 0.70),
            "bolt-upper-row":   (0.55, 0.00, 0.95, 0.12),
            "bolt-lower-row":   (0.55, 0.30, 0.70, 0.55),
            "pipe-flange":      (0.55, 0.00, 0.95, 0.27),
            "drain-port":       (0.30, 0.55, 0.62, 0.78),
        },
    },
    "motor": {
        # normal: motor body left, shaft+coupling on right, dark round
        # fan grill on far left, terminal box on top-center.
        "normal": {
            "terminal-box":      (0.40, 0.08, 0.55, 0.28),
            "cable-gland":       (0.36, 0.05, 0.50, 0.28),
            "cooling-fins":      (0.25, 0.15, 0.78, 0.72),
            "fan-cover":         (0.02, 0.20, 0.25, 0.85),
            "ventilation-slots": (0.04, 0.20, 0.20, 0.78),
            "frame-surface":     (0.10, 0.10, 0.80, 0.85),
            "shaft-end":         (0.78, 0.30, 0.96, 0.65),
            "name-plate":        (0.42, 0.42, 0.62, 0.65),
        },
        # warning: motor centered, exposed shaft sticking out to the LEFT,
        # right-side grill on the right, terminal box on top.
        "warning": {
            "terminal-box":      (0.42, 0.22, 0.65, 0.55),
            "cable-gland":       (0.55, 0.50, 0.72, 0.85),
            "cooling-fins":      (0.20, 0.08, 0.75, 0.70),
            "fan-cover":         (0.65, 0.15, 0.88, 0.55),
            "ventilation-slots": (0.68, 0.18, 0.85, 0.50),
            "frame-surface":     (0.10, 0.05, 0.90, 0.80),
            "shaft-end":         (0.00, 0.32, 0.18, 0.58),
            "name-plate":        (0.30, 0.40, 0.40, 0.55),
        },
        # critical: motor with terminal box on top (small box with eye-bolt),
        # right-side fan-cover grill, cable exiting at the bottom.
        "critical": {
            "terminal-box":      (0.30, 0.00, 0.55, 0.18),
            "cable-gland":       (0.30, 0.65, 0.46, 0.85),
            "cooling-fins":      (0.15, 0.13, 0.65, 0.78),
            "fan-cover":         (0.02, 0.20, 0.16, 0.65),
            "ventilation-slots": (0.04, 0.25, 0.18, 0.78),
            "frame-surface":     (0.15, 0.10, 0.85, 0.85),
            "shaft-end":         (0.70, 0.20, 0.93, 0.85),
            "name-plate":        (0.43, 0.43, 0.58, 0.62),
        },
    },
    "fan": {
        # normal: axial fan with v-belt drive on the right.
        "normal": {
            "frame-surface":   (0.05, 0.05, 0.70, 0.95),
            "blade-tip":       (0.40, 0.18, 0.65, 0.50),
            "blade-hub":       (0.20, 0.20, 0.55, 0.62),
            "guard-mesh":      (0.18, 0.30, 0.50, 0.85),
            "bearing-housing": (0.48, 0.40, 0.62, 0.62),
            "shaft-coupling":  (0.42, 0.35, 0.55, 0.55),
            "pulley":          (0.55, 0.50, 0.78, 0.78),
            "v-belt":          (0.55, 0.45, 0.78, 0.85),
        },
        # warning: 3-blade impeller side view, v-belt visible right.
        "warning": {
            "frame-surface":   (0.05, 0.05, 0.62, 0.95),
            "blade-tip":       (0.10, 0.10, 0.50, 0.50),
            "blade-hub":       (0.30, 0.20, 0.55, 0.55),
            "guard-mesh":      (0.25, 0.05, 0.55, 0.55),
            "bearing-housing": (0.40, 0.50, 0.58, 0.78),
            "shaft-coupling":  (0.40, 0.40, 0.55, 0.62),
            "pulley":          (0.50, 0.40, 0.75, 0.78),
            "v-belt":          (0.55, 0.40, 0.85, 0.85),
        },
        # critical: clogged axial fan; no v-belt / pulley in frame so those
        # boxes intentionally collapse to the centre.
        "critical": {
            "frame-surface":   (0.05, 0.05, 0.95, 0.95),
            "blade-tip":       (0.18, 0.10, 0.55, 0.45),
            "blade-hub":       (0.30, 0.22, 0.55, 0.50),
            "guard-mesh":      (0.05, 0.05, 0.22, 0.85),
            "bearing-housing": (0.32, 0.32, 0.55, 0.55),
            "shaft-coupling":  (0.40, 0.30, 0.50, 0.50),
            "pulley":          (0.40, 0.40, 0.55, 0.55),
            "v-belt":          (0.40, 0.40, 0.55, 0.55),
        },
    },
    "compressor": {
        # normal: V-config compressor with motor right, tank below, clean.
        "normal": {
            "cylinder-head":   (0.20, 0.02, 0.55, 0.22),
            "cooling-fins":    (0.22, 0.05, 0.55, 0.30),
            "safety-valve":    (0.32, 0.00, 0.42, 0.08),
            "pressure-gauge":  (0.10, 0.22, 0.25, 0.40),
            "oil-sight-glass": (0.30, 0.30, 0.40, 0.42),
            "belt-cover":      (0.45, 0.05, 0.65, 0.40),
            "pipe-fitting":    (0.20, 0.10, 0.40, 0.22),
            "drain-valve":     (0.10, 0.55, 0.30, 0.75),
            "tank-surface":    (0.05, 0.42, 0.95, 0.85),
            "status-lamp":     (0.78, 0.30, 0.86, 0.38),
        },
        # warning: V cylinders with motor right, smaller tank.
        "warning": {
            "cylinder-head":   (0.25, 0.02, 0.55, 0.25),
            "cooling-fins":    (0.28, 0.05, 0.55, 0.32),
            "safety-valve":    (0.40, 0.00, 0.48, 0.10),
            "pressure-gauge":  (0.10, 0.30, 0.25, 0.50),
            "oil-sight-glass": (0.32, 0.32, 0.40, 0.42),
            "belt-cover":      (0.45, 0.05, 0.65, 0.45),
            "pipe-fitting":    (0.30, 0.10, 0.45, 0.22),
            "drain-valve":     (0.05, 0.55, 0.18, 0.78),
            "tank-surface":    (0.05, 0.45, 0.85, 0.85),
            "status-lamp":     (0.58, 0.30, 0.68, 0.42),
        },
        # critical: dirty old compressor; gauge on left, tank below.
        "critical": {
            "cylinder-head":   (0.12, 0.02, 0.50, 0.22),
            "cooling-fins":    (0.18, 0.05, 0.50, 0.28),
            "safety-valve":    (0.40, 0.02, 0.50, 0.14),
            "pressure-gauge":  (0.02, 0.30, 0.20, 0.50),
            "oil-sight-glass": (0.30, 0.25, 0.40, 0.36),
            "belt-cover":      (0.48, 0.15, 0.70, 0.45),
            "pipe-fitting":    (0.20, 0.00, 0.42, 0.15),
            "drain-valve":     (0.10, 0.65, 0.35, 0.85),
            "tank-surface":    (0.05, 0.40, 0.95, 0.85),
            "status-lamp":     (0.80, 0.22, 0.90, 0.32),
        },
    },
}


def default_bbox(
    equipment_id: str,
    region_id: str,
    intensity: str = "normal",
) -> tuple[float, float, float, float]:
    """Best-effort default bbox for a region.

    Looks up ``BBOX_LAYOUTS[kind][intensity][region_id]``; falls back to
    "normal" intensity, then to the pump kind, then to a centred 50%
    rectangle so the annotator always has something safe to draw."""
    try:
        kind = get(equipment_id).kind
    except KeyError:
        kind = "pump"
    per_intensity = BBOX_LAYOUTS.get(kind) or BBOX_LAYOUTS["pump"]
    per_region = per_intensity.get(intensity) or per_intensity.get("normal") or {}
    if region_id in per_region:
        return per_region[region_id]
    # As a last resort, scan all intensities of this kind for a matching
    # region — useful for unusual intensity values the catalog hasn't tuned.
    for table in per_intensity.values():
        if region_id in table:
            return table[region_id]
    return (0.25, 0.25, 0.75, 0.75)


# Per-kind image fallback. The demo placeholders ship as pump_*.jpg; other
# kinds reuse them until real photos are supplied. (See
# ``assets/generate_placeholders.py`` for the dedicated motor/fan/compressor
# placeholders that get written when that script runs.)
def _img(kind: str, intensity: str) -> Path:
    """Resolve the canonical photo path for (kind, intensity). PNG files
    win when present (the photorealistic GPT-Image-2 set); .jpg files
    are accepted as a fallback (the original PIL placeholders)."""
    for ext in ("png", "jpg", "jpeg"):
        primary = ASSETS_DIR / f"{kind}_{intensity}.{ext}"
        if primary.exists():
            return primary
    for ext in ("png", "jpg", "jpeg"):
        pump = ASSETS_DIR / f"pump_{intensity}.{ext}"
        if pump.exists():
            return pump
    return ASSETS_DIR / "pump_normal.jpg"


def _images_for(kind: str) -> dict[str, Path]:
    # "ambiguous" reuses the "warning" art on purpose — visually similar.
    return {
        "normal": _img(kind, "normal"),
        "warning": _img(kind, "warning"),
        "critical": _img(kind, "critical"),
        "ambiguous": _img(kind, "warning"),
    }


CATALOG: dict[str, EquipmentSpec] = {
    "Pump-03": EquipmentSpec(
        id="Pump-03",
        label="Pump-03 (一次給水ポンプ)",
        kind="pump",
        location="製造ライン1 / 給水系",
        description="3000 rpm 遠心ポンプ。Tank-A・Reactor-1 への送液を担う基幹設備。",
        rotation_hz=50.0,
        normal_vib_amp=0.15,
        normal_sound_db=42.0,
        normal_temp_c=35.0,
        normal_current_a=2.1,
        bearing_center_hz=175.0,
        downstream=("Tank-A（送液停止）", "Reactor-1（原料供給遅延）"),
        image_paths=_images_for("pump"),
    ),
    "Pump-01": EquipmentSpec(
        id="Pump-01",
        label="Pump-01 (二次循環ポンプ)",
        kind="pump",
        location="製造ライン1 / 冷却循環",
        description="3000 rpm 循環ポンプ。Tank-B への冷却液循環。冗長化なし。",
        rotation_hz=50.0,
        normal_vib_amp=0.13,
        normal_sound_db=41.0,
        normal_temp_c=33.0,
        normal_current_a=1.9,
        bearing_center_hz=175.0,
        downstream=("Tank-B（送液停止）",),
        image_paths=_images_for("pump"),
    ),
    "Motor-02": EquipmentSpec(
        id="Motor-02",
        label="Motor-02 (コンベア駆動モータ)",
        kind="motor",
        location="搬送ライン2",
        description="11kW 三相誘導モータ。コンベア-2 を駆動。負荷変動が大きい。",
        rotation_hz=30.0,
        normal_vib_amp=0.10,
        normal_sound_db=43.0,
        normal_temp_c=40.0,
        normal_current_a=3.5,
        bearing_center_hz=160.0,
        downstream=("コンベア-2（搬送停止）", "充填ライン3（投入遅延）"),
        image_paths=_images_for("motor"),
    ),
    "Fan-04": EquipmentSpec(
        id="Fan-04",
        label="Fan-04 (乾燥炉送風ファン)",
        kind="fan",
        location="乾燥炉-1",
        description="排気送風機。Vベルト駆動。乾燥炉内の温度均一化に必須。",
        rotation_hz=20.0,
        normal_vib_amp=0.08,
        normal_sound_db=46.0,
        normal_temp_c=30.0,
        normal_current_a=1.5,
        bearing_center_hz=130.0,
        downstream=("乾燥炉-1（温度逸脱の可能性）", "次工程梱包-A"),
        image_paths=_images_for("fan"),
    ),
    "Compressor-05": EquipmentSpec(
        id="Compressor-05",
        label="Compressor-05 (圧縮空気供給機)",
        kind="compressor",
        location="ユーティリティ室",
        description="工場全域のエアアクチュエータに 0.7 MPa を供給。代替系統なし。",
        rotation_hz=40.0,
        normal_vib_amp=0.16,
        normal_sound_db=44.0,
        normal_temp_c=42.0,
        normal_current_a=3.8,
        bearing_center_hz=220.0,
        downstream=("空気圧アクチュエータ全系統", "塗装ブース", "包装ライン"),
        image_paths=_images_for("compressor"),
    ),
}


# Per-intensity multipliers — tuned so generated frames hit the right
# risk_engine bucket for each equipment-kind with its own thresholds.
INTENSITY_PROFILES: dict[str, dict[str, float]] = {
    "normal":   {"vib": 1.0, "sound_offset": 0.0,  "temp_offset": 0.0,  "temp_trend": 0.02, "current_mult": 1.00, "bearing_amp": 0.0,  "impacts": 0},
    "warning":  {"vib": 1.6, "sound_offset": 8.0,  "temp_offset": 9.0,  "temp_trend": 0.10, "current_mult": 1.10, "bearing_amp": 0.5,  "impacts": 0},
    "critical": {"vib": 2.6, "sound_offset": 16.0, "temp_offset": 16.0, "temp_trend": 0.45, "current_mult": 1.30, "bearing_amp": 1.4,  "impacts": 10},
    "ambiguous":{"vib": 1.1, "sound_offset": 2.5,  "temp_offset": 2.0,  "temp_trend": 0.05, "current_mult": 1.02, "bearing_amp": 0.1,  "impacts": 4},
}


def list_equipment() -> list[EquipmentSpec]:
    return list(CATALOG.values())


def get(equipment_id: str) -> EquipmentSpec:
    if equipment_id not in CATALOG:
        raise KeyError(f"unknown equipment_id: {equipment_id}")
    return CATALOG[equipment_id]


def _baseline_axis(t: np.ndarray, amp: float, rotation_hz: float, rng: np.random.Generator) -> np.ndarray:
    rotation = amp * np.sin(2 * np.pi * rotation_hz * t)
    noise = 0.4 * amp * rng.standard_normal(t.size)
    return rotation + noise


def _bearing_fault(t: np.ndarray, center_hz: float, severity_amp: float, rng: np.random.Generator) -> np.ndarray:
    """Two harmonics around the bearing-fault center frequency, with random phase."""
    if severity_amp <= 0:
        return np.zeros_like(t)
    h1 = severity_amp * np.sin(2 * np.pi * center_hz * t + rng.uniform(0, 2 * np.pi))
    h2 = 0.6 * severity_amp * np.sin(2 * np.pi * (center_hz * 2) * t + rng.uniform(0, 2 * np.pi))
    return h1 + h2


def _seed_from(equipment_id: str, intensity: str) -> int:
    # Deterministic per (equipment, intensity) so the UI stays reproducible
    # across page reloads but each asset still looks different.
    return (hash((equipment_id, intensity)) & 0x7FFFFFFF) or 1


def generate_sensor_df(
    equipment_id: str,
    intensity: str,
    *,
    seed: int | None = None,
    n_samples: int = N_SAMPLES,
    sample_rate_hz: int = SAMPLE_RATE_HZ,
) -> pd.DataFrame:
    """Produce a (timestamp, vib_xyz, sound, temperature, current) DataFrame
    sized for the existing signal pipeline. Deterministic per (equip, intensity)
    unless ``seed`` is given."""
    spec = get(equipment_id)
    if intensity not in INTENSITY_PROFILES:
        raise ValueError(f"unknown intensity: {intensity}")
    p = INTENSITY_PROFILES[intensity]
    rng = np.random.default_rng(seed if seed is not None else _seed_from(equipment_id, intensity))
    t = np.arange(n_samples) / sample_rate_hz

    vib_amp = spec.normal_vib_amp * p["vib"]
    bearing = _bearing_fault(t, spec.bearing_center_hz, p["bearing_amp"] * spec.normal_vib_amp, rng)

    vx = _baseline_axis(t, vib_amp * 0.75, spec.rotation_hz, rng) + bearing * 0.6
    vy = _baseline_axis(t, vib_amp * 0.70, spec.rotation_hz, rng) + bearing * 0.5
    vz = _baseline_axis(t, vib_amp,        spec.rotation_hz, rng) + bearing

    impacts = int(p["impacts"])
    if impacts > 0 and intensity == "critical":
        idx = rng.choice(t.size, size=impacts, replace=False)
        vz[idx] += rng.uniform(0.6, 1.2, size=impacts)
    elif impacts > 0 and intensity == "ambiguous":
        idx = rng.choice(t.size, size=impacts, replace=False)
        # Stay below the 0.8G impact-Critical threshold on purpose: this is
        # what makes the case "ambiguous" rather than "critical".
        vz[idx] += rng.uniform(0.28, 0.42, size=impacts)

    sound = (
        spec.normal_sound_db + p["sound_offset"]
        + 1.2 * rng.standard_normal(t.size)
        + 0.1 * p["sound_offset"] * t / max(t[-1], 1e-6) * 0.5
    )
    temperature = (
        spec.normal_temp_c + p["temp_offset"]
        + p["temp_trend"] * t
        + 0.3 * rng.standard_normal(t.size)
    )
    current = spec.normal_current_a * p["current_mult"] + 0.06 * rng.standard_normal(t.size)

    return pd.DataFrame({
        "timestamp": np.round(t, 4),
        "vibration_x": np.round(vx, 5),
        "vibration_y": np.round(vy, 5),
        "vibration_z": np.round(vz, 5),
        "sound_level": np.round(sound, 3),
        "temperature": np.round(temperature, 3),
        "current": np.round(current, 4),
    })


# ───────────────────────────────────────────────────────────────────────
# Inspection memos — kind- and intensity-specific
# ───────────────────────────────────────────────────────────────────────

_MEMO_TEMPLATES: dict[str, dict[str, str]] = {
    "pump": {
        "normal":   "定期点検。運転音・振動・温度ともに通常範囲内。前回点検からの変化は特になし。",
        "warning":  "前回点検時より運転音がわずかに大きくなっている印象。ボルト周辺に微小な変色あり。",
        "critical": "明らかな異音（ゴロゴロという連続音）あり。軸受周辺が触れないほど熱い。漏れ跡と錆び。",
        "ambiguous":"ときどき小さな音がする気がするが、再現性は不明。温度・振動は普段と大差ない印象。",
    },
    "motor": {
        "normal":   "通常運転。負荷電流・温度ともに定常。回転は安定。",
        "warning":  "電流値がわずかに上昇傾向。負荷増加か劣化開始か、判断保留。",
        "critical": "巻線温度が高い。異臭の兆候。負荷変動時に異音。即時管理者確認推奨。",
        "ambiguous":"短時間の電流スパイクが散発。前回比で特に異常とは断定できず。",
    },
    "fan": {
        "normal":   "送風量・運転音とも通常。ベルト張力も適正。",
        "warning":  "Vベルトのこすれ音らしき音が発生。振動はわずかに増加。",
        "critical": "明確なベルト鳴きと振動増加。送風量低下の疑い。乾燥炉温度監視を強化中。",
        "ambiguous":"特定の回転数域でのみ振動が増す。再現性が低く判定保留。",
    },
    "compressor": {
        "normal":   "吐出圧・温度ともに規定範囲内。アンロード比率も通常。",
        "warning":  "吐出温度がじりじり上昇傾向。アンロード頻度が低下傾向。",
        "critical": "ロード継続時間が長く、温度が常時高い。安全弁手前まで圧力上昇。停止検討。",
        "ambiguous":"夜間運転時の温度上昇が散見。日中は通常値に戻る。原因切り分け中。",
    },
}


def inspection_memo(equipment_id: str, intensity: str) -> str:
    spec = get(equipment_id)
    table = _MEMO_TEMPLATES.get(spec.kind, _MEMO_TEMPLATES["pump"])
    return table.get(intensity, table["normal"])


def preset_label(equipment_id: str, intensity: str) -> str:
    spec = get(equipment_id)
    intensity_label = {
        "normal": "Normal",
        "warning": "Warning",
        "critical": "Critical",
        "ambiguous": "Ambiguous",
    }.get(intensity, intensity)
    return f"{spec.id} / {intensity_label}"


# ───────────────────────────────────────────────────────────────────────
# CSV override map — for the Pump-03 demo we ship hand-tuned CSVs (the
# original demo data). When a CSV exists for a (equipment, intensity), it
# wins over the synthetic generator so the **sidebar / FastAPI snapshot /
# Next.js fleet view** all read the same numbers the Streamlit Command
# Center loads via DEMO_PRESETS. Without this, Pump-03's sidebar score
# disagreed with the hero score because one path used CSV and the other
# used synth.
# ───────────────────────────────────────────────────────────────────────

_CSV_OVERRIDES: dict[tuple[str, str], Path] = {
    ("Pump-03", "normal"):    DATA_DIR / "normal_sensor.csv",
    ("Pump-03", "warning"):   DATA_DIR / "warning_sensor.csv",
    ("Pump-03", "critical"):  DATA_DIR / "critical_sensor.csv",
    ("Pump-03", "ambiguous"): DATA_DIR / "ambiguous_sensor.csv",
}


# ───────────────────────────────────────────────────────────────────────
# Caching — the sensor dataframe is deterministic per (equip, intensity),
# so cache it to keep the Command Center snappy when re-rendering N cards.
# ───────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=64)
def cached_sensor_df(equipment_id: str, intensity: str) -> pd.DataFrame:
    """Memoised wrapper — call this from UI code so re-renders are cheap.

    For (equipment, intensity) pairs that have a shipped CSV (Pump-03 demo),
    we load the CSV. Everyone else uses the synthetic generator. This keeps
    Streamlit's Command Center, the sidebar fleet view, the FastAPI
    ``/api/equipment/{id}/snapshot`` and the Next.js fleet grid in agreement.
    """
    override = _CSV_OVERRIDES.get((equipment_id, intensity))
    if override is not None and override.exists():
        return pd.read_csv(override)
    return generate_sensor_df(equipment_id, intensity)


def reset_cache() -> None:
    cached_sensor_df.cache_clear()


def iter_presets(intensities: Iterable[str] = ("normal", "warning", "critical", "ambiguous")):
    """Generate (preset_key, equipment_id, intensity) tuples covering the
    full catalog × intensities cross product. Keys are stable so they can
    be saved into Cosmos / used in URLs."""
    for equip_id in CATALOG:
        for intensity in intensities:
            yield (f"{equip_id}:{intensity}", equip_id, intensity)
