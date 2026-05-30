"""
Raw-data ingestion: turn an *arbitrary* sensor CSV into the canonical schema
that ``signal_analysis.analyze`` expects.

Demo presets are already in canonical form (``vibration_x/y/z``, ``sound_level``,
``temperature``, ``current``, ``timestamp``). Real field data almost never is —
columns are named ``accel_z`` / ``dB`` / ``temp_C`` / ``amps`` / ``time`` and the
sampling rate is whatever the logger used. Without normalisation, those columns
silently miss the canonical names and ``analyze`` degrades every channel to
zero, producing a meaningless "Normal".

This module:
  - auto-detects which raw column maps to each canonical channel (EN/JP aliases),
  - infers the sampling rate from a timestamp column when present,
  - applies an (auto or user-edited) mapping to produce a canonical dataframe,
  - reports which channels are present/missing so the UI and the Intake agent
    can say "did we have enough data to trust this?".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd


PRIMARY_AXIS = "vibration_z"

# Canonical channels in detection-priority order. Specific axes (x/y/z) are
# matched before the generic "vibration", so a lone "vibration" column lands on
# the primary axis instead of stealing an x/y slot.
CANONICAL_CHANNELS: list[str] = [
    "timestamp",
    "vibration_x",
    "vibration_y",
    "vibration_z",
    "sound_level",
    "temperature",
    "current",
]

# Normalised alias tokens per channel (substring match against the normalised
# column name). Longer/more specific tokens first. Deliberately omits ultra-
# ambiguous single letters ("a", "g") that would mis-grab unrelated columns.
_ALIASES: dict[str, list[str]] = {
    "timestamp":   ["timestamp", "datetime", "elapsed", "seconds", "time", "ts", "時刻", "時間", "経過"],
    "vibration_x": ["vibrationx", "vibx", "accelx", "accx", "ax", "振動x", "x軸"],
    "vibration_y": ["vibrationy", "viby", "accely", "accy", "ay", "振動y", "y軸"],
    "vibration_z": ["vibrationz", "vibz", "accelz", "accz", "az", "振動z", "z軸",
                    "vibration", "vibrate", "vib", "acceleration", "accel", "振動", "加速度"],
    "sound_level": ["soundlevel", "sound", "decibel", "noise", "acoustic", "spl", "db",
                    "mic", "音響", "騒音", "音圧", "音"],
    "temperature": ["temperature", "temp", "degc", "celsius", "℃", "温度", "気温"],
    "current":     ["current", "ampere", "amperage", "amps", "amp", "電流"],
}


def _normalise(name: str) -> str:
    """Lowercase, drop unit-parentheses and any separators/symbols, keeping
    alphanumerics and Japanese kana/kanji so JP headers still match."""
    s = re.sub(r"\(.*?\)|\[.*?\]|（.*?）", "", str(name)).lower()
    return re.sub(r"[^0-9a-z぀-ヿ一-鿿]", "", s)


@dataclass
class RawIngestResult:
    canonical_df: pd.DataFrame
    mapping: dict[str, str | None]          # canonical -> source column (or None)
    sample_rate_hz: float
    sample_rate_inferred: bool
    present_channels: list[str] = field(default_factory=list)
    missing_channels: list[str] = field(default_factory=list)
    sample_count: int = 0
    duration_seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)

    @property
    def has_primary_vibration(self) -> bool:
        return PRIMARY_AXIS in self.present_channels


def auto_detect_mapping(columns: list[str]) -> dict[str, str | None]:
    """Best-effort map of canonical channel -> source column name.

    Greedy and specific-first: each source column is claimed by at most one
    canonical channel, and channels are resolved in CANONICAL_CHANNELS order.
    """
    normalised = {col: _normalise(col) for col in columns}
    mapping: dict[str, str | None] = {ch: None for ch in CANONICAL_CHANNELS}
    claimed: set[str] = set()

    for channel in CANONICAL_CHANNELS:
        best_col: str | None = None
        best_score = 0
        for alias in _ALIASES[channel]:
            for col in columns:
                if col in claimed:
                    continue
                if alias and alias in normalised[col]:
                    # Prefer the longest alias hit (most specific match).
                    if len(alias) > best_score:
                        best_col, best_score = col, len(alias)
            if best_col is not None and best_score == len(_ALIASES[channel][0]):
                break  # exact top-priority alias — good enough
        if best_col is not None:
            mapping[channel] = best_col
            claimed.add(best_col)
    return mapping


def _to_elapsed_seconds(series: pd.Series) -> np.ndarray | None:
    """Convert a timestamp column to float seconds elapsed from the start.

    Handles numeric seconds and parseable datetime strings; returns None if it
    can't make sense of the column.
    """
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.notna().mean() > 0.9:
        vals = numeric.to_numpy(dtype=float)
        vals = vals - np.nanmin(vals)
        return np.nan_to_num(vals)
    dt = pd.to_datetime(series, errors="coerce")
    if dt.notna().mean() > 0.9:
        secs = (dt - dt.min()).dt.total_seconds().to_numpy(dtype=float)
        return np.nan_to_num(secs)
    return None


def infer_sample_rate(df: pd.DataFrame, timestamp_col: str | None) -> float | None:
    """Infer Hz from the median spacing of a timestamp column. None if not
    derivable (no/blank timestamp, non-monotonic, or zero spacing)."""
    if not timestamp_col or timestamp_col not in df.columns:
        return None
    secs = _to_elapsed_seconds(df[timestamp_col])
    if secs is None or secs.size < 2:
        return None
    diffs = np.diff(secs)
    diffs = diffs[diffs > 0]
    if diffs.size == 0:
        return None
    dt = float(np.median(diffs))
    if dt <= 0:
        return None
    fs = 1.0 / dt
    # Round to something tidy without distorting unusual rates.
    return round(fs, 2)


def apply_mapping(
    df: pd.DataFrame,
    mapping: dict[str, str | None],
    sample_rate_hz: float,
    *,
    sample_rate_inferred: bool = False,
) -> RawIngestResult:
    """Build the canonical dataframe from a raw dataframe + column mapping."""
    out = pd.DataFrame()
    warnings: list[str] = []

    ts_col = mapping.get("timestamp")
    if ts_col and ts_col in df.columns:
        secs = _to_elapsed_seconds(df[ts_col])
        if secs is not None and secs.size == len(df):
            out["timestamp"] = secs
        else:
            warnings.append(f"timestamp 列『{ts_col}』を秒に変換できなかったため、サンプリングレートから時間を推定します。")

    present: list[str] = []
    for channel in ("vibration_x", "vibration_y", "vibration_z",
                    "sound_level", "temperature", "current"):
        col = mapping.get(channel)
        if col and col in df.columns:
            numeric = pd.to_numeric(df[col], errors="coerce")
            bad = int(numeric.isna().sum())
            if bad:
                warnings.append(f"{channel}（{col}）: {bad} 件の非数値を 0 に補完しました。")
            out[channel] = numeric.fillna(0.0).to_numpy()
            present.append(channel)

    missing = [c for c in ("vibration_z", "sound_level", "temperature", "current") if c not in present]
    if PRIMARY_AXIS not in present:
        warnings.append(
            "主振動軸（vibration_z）が未割当です。FFT・軸受帯域・振動RMSが算出できないため、"
            "いずれかの振動/加速度列を vibration_z に割り当ててください。"
        )

    n = int(len(out)) if len(out.columns) else int(len(df))
    if "timestamp" in out.columns and len(out) >= 2:
        ts = out["timestamp"].to_numpy()
        duration = float(ts[-1] - ts[0])
    else:
        duration = (n / sample_rate_hz) if sample_rate_hz > 0 else 0.0

    return RawIngestResult(
        canonical_df=out,
        mapping=mapping,
        sample_rate_hz=sample_rate_hz,
        sample_rate_inferred=sample_rate_inferred,
        present_channels=present,
        missing_channels=missing,
        sample_count=n,
        duration_seconds=duration,
        warnings=warnings,
    )


def ingest(
    df: pd.DataFrame,
    *,
    mapping: dict[str, str | None] | None = None,
    sample_rate_hz: float | None = None,
) -> RawIngestResult:
    """One-shot convenience: auto-detect mapping + sample rate (unless given),
    then normalise. The UI uses the lower-level functions for an interactive
    edit step; callers/tests can use this for the happy path."""
    mapping = mapping or auto_detect_mapping(list(df.columns))
    inferred = False
    if sample_rate_hz is None:
        guessed = infer_sample_rate(df, mapping.get("timestamp"))
        if guessed and guessed > 0:
            sample_rate_hz, inferred = guessed, True
        else:
            from .signal_analysis import SAMPLE_RATE_HZ
            sample_rate_hz = SAMPLE_RATE_HZ
    return apply_mapping(df, mapping, sample_rate_hz, sample_rate_inferred=inferred)
