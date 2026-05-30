"""
Spresense ボードのファームを動かさずに、同じ JSON 形式の点検サンプルを
Event Hubs（または ローカル JSONL）に流し込むシミュレータ。

実機接続前のデモ・E2E 検証に使います。Spresense 側の実装サンプルは
``docs/spresense_firmware.md`` を参照。

使い方
------
    # 既定: 1 サンプル/ms 相当 × 5 秒 = 5000 件を、warning 強度で送る
    python data/spresense_simulator.py --equipment-id Pump-03 --intensity warning

    # 強度を critical にして 10 秒分流す
    python data/spresense_simulator.py --intensity critical --duration 10

    # Event Hubs が設定されていればそちらへ、なければ
    # ./_spresense_stream.jsonl に追記される。
"""
from __future__ import annotations

import argparse
import math
import random
import sys
import time
from pathlib import Path

# Allow running as a script: add project root to sys.path so `from src...` works
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.iot_ingest import send_events  # noqa: E402


SAMPLE_RATE_HZ = 1000.0


PROFILES = {
    "normal": {
        "vib_base_rms": 0.04,
        "vib_bearing_amp": 0.005,
        "sound_base": 42.0,
        "temp_base": 32.0,
        "current_base": 1.8,
    },
    "warning": {
        "vib_base_rms": 0.18,
        "vib_bearing_amp": 0.08,
        "sound_base": 50.0,
        "temp_base": 46.0,
        "current_base": 2.2,
    },
    "critical": {
        "vib_base_rms": 0.35,
        "vib_bearing_amp": 0.20,
        "sound_base": 58.0,
        "temp_base": 53.0,
        "current_base": 2.7,
    },
}


def generate_sample(profile: dict, t: float, rng: random.Random) -> dict:
    """One sample, modelled after data/generate_demo_data.py."""
    base = profile["vib_base_rms"]
    bearing = profile["vib_bearing_amp"]
    vib_x = rng.gauss(0, base * 0.7) + bearing * math.sin(2 * math.pi * 50.0 * t)
    vib_y = rng.gauss(0, base * 0.7) + bearing * math.sin(2 * math.pi * 60.0 * t)
    vib_z = (
        rng.gauss(0, base)
        + bearing * math.sin(2 * math.pi * 50.0 * t)
        + bearing * 0.9 * math.sin(2 * math.pi * 175.0 * t)
        + bearing * 0.7 * math.sin(2 * math.pi * 250.0 * t)
    )
    return {
        "vibration_x": round(vib_x, 5),
        "vibration_y": round(vib_y, 5),
        "vibration_z": round(vib_z, 5),
        "sound_level": round(profile["sound_base"] + rng.gauss(0, 0.8), 2),
        "temperature": round(profile["temp_base"] + 0.08 * t + rng.gauss(0, 0.2), 2),
        "current": round(profile["current_base"] + rng.gauss(0, 0.05), 3),
    }


def stream(*, equipment_id: str, device_id: str, intensity: str,
           duration_seconds: float, sample_rate_hz: float,
           batch_size: int, throttle: bool, seed: int = 42) -> dict:
    profile = PROFILES.get(intensity)
    if profile is None:
        raise SystemExit(f"unknown intensity {intensity!r}; choose from {list(PROFILES)}")
    total = int(duration_seconds * sample_rate_hz)
    rng = random.Random(seed)
    start_epoch = time.time()
    print(f"[simulator] generating {total} samples for {equipment_id} "
          f"(device={device_id}, intensity={intensity})...")
    sent_total = 0
    backend_summary: dict[str, int] = {}
    buf: list[dict] = []
    last_flush = time.time()
    for i in range(total):
        t = i / sample_rate_hz
        sample = generate_sample(profile, t, rng)
        sample.update({
            "device_id": device_id,
            "equipment_id": equipment_id,
            "timestamp": start_epoch + t,
        })
        buf.append(sample)
        if len(buf) >= batch_size:
            r = send_events(buf)
            sent_total += int(r.get("sent", 0))
            backend_summary[r.get("backend", "?")] = backend_summary.get(r.get("backend", "?"), 0) + int(r.get("sent", 0))
            buf = []
            if throttle:
                # pace to roughly real-time
                expected = (i + 1) / sample_rate_hz
                elapsed = time.time() - start_epoch
                sleep_for = expected - elapsed
                if sleep_for > 0:
                    time.sleep(min(sleep_for, 0.5))
        if time.time() - last_flush > 2.0:
            print(f"[simulator] progress: {i+1}/{total}")
            last_flush = time.time()
    if buf:
        r = send_events(buf)
        sent_total += int(r.get("sent", 0))
        backend_summary[r.get("backend", "?")] = backend_summary.get(r.get("backend", "?"), 0) + int(r.get("sent", 0))
    print(f"[simulator] done. sent {sent_total} samples via {backend_summary}")
    return {"sent": sent_total, "backends": backend_summary}


def main() -> None:
    ap = argparse.ArgumentParser(description="Spresense edge-device simulator")
    ap.add_argument("--equipment-id", default="Pump-03")
    ap.add_argument("--device-id", default="spresense-01")
    ap.add_argument("--intensity", default="warning", choices=list(PROFILES))
    ap.add_argument("--duration", type=float, default=5.0, help="seconds of data to generate")
    ap.add_argument("--rate", type=float, default=SAMPLE_RATE_HZ, help="samples per second")
    ap.add_argument("--batch-size", type=int, default=200)
    ap.add_argument("--throttle", action="store_true", help="pace sending to real-time")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    stream(
        equipment_id=args.equipment_id,
        device_id=args.device_id,
        intensity=args.intensity,
        duration_seconds=args.duration,
        sample_rate_hz=args.rate,
        batch_size=args.batch_size,
        throttle=args.throttle,
        seed=args.seed,
    )


if __name__ == "__main__":
    main()
