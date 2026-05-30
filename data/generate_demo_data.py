"""
Generates the four demo sensor CSVs.

Run once after cloning:
    python data/generate_demo_data.py

Each file has 1024 samples at 100Hz (10.24s window), with these columns:
    timestamp, vibration_x, vibration_y, vibration_z,
    sound_level, temperature, current

The signals are synthesized to be plausible for a centrifugal pump
(Pump-03 in the demo narrative). Characteristics:

- normal:    low-amplitude broadband noise, dominant ~50Hz rotation
- warning:   elevated 120Hz sideband (bearing wear precursor),
             mild temperature drift, sound rising
- critical:  large 120Hz + 240Hz harmonics (bearing damage),
             sound and temp clearly elevated, current trending up
- ambiguous: normal-ish baseline with intermittent spikes —
             the AI should NOT confidently call this Critical
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd


SAMPLE_RATE = 1000  # Hz — industrial vibration acquisition rate
N_SAMPLES = 4096
ROT_HZ = 50.0       # nominal pump rotation frequency (3000 rpm)
DATA_DIR = Path(__file__).parent


def _time_axis() -> np.ndarray:
    return np.arange(N_SAMPLES) / SAMPLE_RATE


def _baseline_vibration(t: np.ndarray, amp: float, rng: np.random.Generator) -> np.ndarray:
    rotation = amp * np.sin(2 * np.pi * ROT_HZ * t)
    noise = 0.4 * amp * rng.standard_normal(t.size)
    return rotation + noise


def _bearing_fault(t: np.ndarray, severity: float, rng: np.random.Generator) -> np.ndarray:
    # Bearing wear shows up as elevated components around 2x and 4x rotation
    c1 = severity * np.sin(2 * np.pi * 120.0 * t + rng.uniform(0, 2 * np.pi))
    c2 = 0.6 * severity * np.sin(2 * np.pi * 240.0 * t + rng.uniform(0, 2 * np.pi))
    return c1 + c2


def make_normal(seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    t = _time_axis()
    vx = _baseline_vibration(t, amp=0.12, rng=rng)
    vy = _baseline_vibration(t, amp=0.10, rng=rng)
    vz = _baseline_vibration(t, amp=0.15, rng=rng)
    sound = 42.0 + 1.2 * rng.standard_normal(t.size)
    temperature = 35.0 + 0.02 * t + 0.3 * rng.standard_normal(t.size)
    current = 2.1 + 0.05 * rng.standard_normal(t.size)
    return _to_df(t, vx, vy, vz, sound, temperature, current)


def make_warning(seed: int = 2) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    t = _time_axis()
    vx = _baseline_vibration(t, amp=0.18, rng=rng) + _bearing_fault(t, 0.08, rng)
    vy = _baseline_vibration(t, amp=0.16, rng=rng) + _bearing_fault(t, 0.07, rng)
    vz = _baseline_vibration(t, amp=0.20, rng=rng) + _bearing_fault(t, 0.10, rng)
    sound = 48.5 + 1.2 * rng.standard_normal(t.size) + 0.15 * t
    temperature = 41.0 + 0.08 * t + 0.3 * rng.standard_normal(t.size)
    current = 2.30 + 0.06 * rng.standard_normal(t.size)
    return _to_df(t, vx, vy, vz, sound, temperature, current)


def make_critical(seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    t = _time_axis()
    vx = _baseline_vibration(t, amp=0.28, rng=rng) + _bearing_fault(t, 0.35, rng)
    vy = _baseline_vibration(t, amp=0.25, rng=rng) + _bearing_fault(t, 0.30, rng)
    vz = _baseline_vibration(t, amp=0.40, rng=rng) + _bearing_fault(t, 0.55, rng)
    # add intermittent impact transients
    impact_idx = rng.choice(t.size, size=12, replace=False)
    vz[impact_idx] += rng.uniform(0.6, 1.2, size=impact_idx.size)
    sound = 58.0 + 2.0 * rng.standard_normal(t.size) + 0.6 * t
    temperature = 46.0 + 0.45 * t + 0.5 * rng.standard_normal(t.size)
    current = 2.85 + 0.10 * rng.standard_normal(t.size) + 0.05 * t
    return _to_df(t, vx, vy, vz, sound, temperature, current)


def make_ambiguous(seed: int = 4) -> pd.DataFrame:
    """Baseline is near-normal but with sporadic spikes — AI should hedge.

    Intentionally NOT triggering the Critical impact threshold (0.8 G):
    spikes top out around 0.45-0.55 G so the rule-based engine sees
    Warning + ambiguity_flag rather than Critical. The narrative is:
    "indicators sit close to normal but with intermittent ticks — we
    can't confidently call it." This is where the AI must say
    'human confirmation required'.
    """
    rng = np.random.default_rng(seed)
    t = _time_axis()
    vx = _baseline_vibration(t, amp=0.14, rng=rng)
    vy = _baseline_vibration(t, amp=0.12, rng=rng)
    vz = _baseline_vibration(t, amp=0.17, rng=rng)
    spike_idx = rng.choice(t.size, size=4, replace=False)
    vz[spike_idx] += rng.uniform(0.28, 0.42, size=spike_idx.size)
    sound = 44.5 + 1.6 * rng.standard_normal(t.size)
    sound[spike_idx] += rng.uniform(2, 4, size=spike_idx.size)
    temperature = 36.5 + 0.05 * t + 0.4 * rng.standard_normal(t.size)
    current = 2.15 + 0.07 * rng.standard_normal(t.size)
    return _to_df(t, vx, vy, vz, sound, temperature, current)


def _to_df(t, vx, vy, vz, sound, temperature, current) -> pd.DataFrame:
    return pd.DataFrame({
        "timestamp": np.round(t, 4),
        "vibration_x": np.round(vx, 5),
        "vibration_y": np.round(vy, 5),
        "vibration_z": np.round(vz, 5),
        "sound_level": np.round(sound, 3),
        "temperature": np.round(temperature, 3),
        "current": np.round(current, 4),
    })


def main() -> None:
    plan = {
        "normal_sensor.csv": make_normal(),
        "warning_sensor.csv": make_warning(),
        "critical_sensor.csv": make_critical(),
        "ambiguous_sensor.csv": make_ambiguous(),
    }
    for name, df in plan.items():
        out = DATA_DIR / name
        df.to_csv(out, index=False)
        print(f"wrote {out} ({len(df)} rows)")


if __name__ == "__main__":
    main()
