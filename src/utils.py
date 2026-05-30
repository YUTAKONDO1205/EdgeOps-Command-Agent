"""Shared helpers: JSON extraction, env config, demo presets.

Presets are no longer fixed CSV files. They are generated on demand from
``src.equipment_catalog`` so every equipment × intensity combination is
available. The legacy short keys (``normal`` / ``warning`` / ``critical`` /
``ambiguous``) are kept as aliases for the Pump-03 variants — that's what
the original tests and demo scripts use.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import pandas as pd
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
ASSETS_DIR = PROJECT_ROOT / "assets"


@dataclass(frozen=True)
class DemoPreset:
    """Demo preset. ``sensor_loader`` produces the dataframe lazily so that
    catalog presets don't need a CSV file on disk. ``sensor_csv`` is kept
    for callers that want a Path (legacy Pump-03 entries only)."""
    key: str
    label: str
    sensor_loader: Callable[[], pd.DataFrame]
    image_path: Path
    inspection_memo: str
    equipment_id: str = "Pump-03"
    intensity: str = "normal"
    sensor_csv: Path | None = None  # legacy: file path when one exists


def _csv_loader(path: Path) -> Callable[[], pd.DataFrame]:
    def _load() -> pd.DataFrame:
        return pd.read_csv(path)
    return _load


def _catalog_loader(equipment_id: str, intensity: str) -> Callable[[], pd.DataFrame]:
    # Delayed import to avoid a circular dependency at module-load time.
    def _load() -> pd.DataFrame:
        from . import equipment_catalog
        return equipment_catalog.cached_sensor_df(equipment_id, intensity)
    return _load


def _build_presets() -> dict[str, DemoPreset]:
    from . import equipment_catalog  # lazy

    presets: dict[str, DemoPreset] = {}

    # Legacy Pump-03 presets first — these point at the shipped CSVs so the
    # existing tests / Zenn article references still resolve to byte-identical
    # data on the Pump-03 path.
    legacy_csv = {
        "normal":    (DATA_DIR / "normal_sensor.csv",    ASSETS_DIR / "pump_normal.png",
                      "定期点検。運転音・振動・温度ともに通常範囲内。前回点検からの変化は特になし。"),
        "warning":   (DATA_DIR / "warning_sensor.csv",   ASSETS_DIR / "pump_warning.png",
                      "前回点検時より運転音がわずかに大きくなっている印象。ボルト周辺に微小な変色あり。"),
        "critical":  (DATA_DIR / "critical_sensor.csv",  ASSETS_DIR / "pump_critical.png",
                      "明らかな異音（ゴロゴロという連続音）あり。軸受周辺が触れないほど熱い。"),
        "ambiguous": (DATA_DIR / "ambiguous_sensor.csv", ASSETS_DIR / "pump_warning.png",
                      "ときどき小さな音がする気がするが、再現性は不明。温度・振動は普段と大差ない印象。"),
    }
    for intensity, (csv_path, img_path, memo) in legacy_csv.items():
        loader = _csv_loader(csv_path) if csv_path.exists() else _catalog_loader("Pump-03", intensity)
        presets[intensity] = DemoPreset(
            key=intensity,
            label=f"Pump-03 / {intensity.capitalize()}",
            sensor_loader=loader,
            image_path=img_path,
            inspection_memo=memo,
            equipment_id="Pump-03",
            intensity=intensity,
            sensor_csv=csv_path if csv_path.exists() else None,
        )

    # Full catalog × intensity matrix, keyed as "<EquipmentId>:<intensity>".
    for key, equipment_id, intensity in equipment_catalog.iter_presets():
        spec = equipment_catalog.get(equipment_id)
        if key in presets:
            continue
        # Pump-03 alias keys above already cover the Pump-03 row; we still want
        # the explicit "Pump-03:warning" form for the new UI.
        if equipment_id == "Pump-03":
            csv_path = legacy_csv[intensity][0]
            loader = _csv_loader(csv_path) if csv_path.exists() else _catalog_loader(equipment_id, intensity)
            csv_for_preset = csv_path if csv_path.exists() else None
        else:
            loader = _catalog_loader(equipment_id, intensity)
            csv_for_preset = None
        presets[key] = DemoPreset(
            key=key,
            label=equipment_catalog.preset_label(equipment_id, intensity),
            sensor_loader=loader,
            image_path=spec.image_paths.get(intensity, spec.image_paths["normal"]),
            inspection_memo=equipment_catalog.inspection_memo(equipment_id, intensity),
            equipment_id=equipment_id,
            intensity=intensity,
            sensor_csv=csv_for_preset,
        )
    return presets


# Lazy: `equipment_catalog` imports from this module, so initialising the
# preset dict at import time would create a cycle. Build on first access.
_DEMO_PRESETS_CACHE: dict[str, DemoPreset] | None = None


class _LazyPresetMap:
    """Dict-like view over the preset registry. Built on first access so the
    catalog module can finish importing first."""

    def _ensure(self) -> dict[str, DemoPreset]:
        global _DEMO_PRESETS_CACHE
        if _DEMO_PRESETS_CACHE is None:
            _DEMO_PRESETS_CACHE = _build_presets()
        return _DEMO_PRESETS_CACHE

    def __getitem__(self, key: str) -> DemoPreset:
        return self._ensure()[key]

    def get(self, key: str, default=None):
        return self._ensure().get(key, default)

    def __iter__(self):
        return iter(self._ensure())

    def __len__(self):
        return len(self._ensure())

    def __contains__(self, key: object) -> bool:
        return key in self._ensure()

    def items(self):
        return self._ensure().items()

    def values(self):
        return self._ensure().values()

    def keys(self):
        return self._ensure().keys()


DEMO_PRESETS: _LazyPresetMap = _LazyPresetMap()


def load_env() -> None:
    """Load .env if present. Safe to call multiple times."""
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)


def use_mock_mode() -> bool:
    """Mock mode when Azure OpenAI is not configured or explicitly disabled."""
    load_env()
    flag = os.getenv("EDGEOPS_USE_MOCK", "").strip().lower()
    if flag in {"true", "1", "yes"}:
        return True
    if flag in {"false", "0", "no"}:
        return False
    # Auto-detect: if endpoint or key missing, fall back to mock
    return not (os.getenv("AZURE_OPENAI_ENDPOINT") and os.getenv("AZURE_OPENAI_API_KEY"))


def extract_json(text: str) -> dict[str, Any] | list[Any] | None:
    """
    Pull the first JSON object/array out of a model response.

    LLMs sometimes wrap JSON in ```json fences or add prose. This recovers
    the structured payload without forcing the prompt to be perfectly clean.
    """
    if not text:
        return None
    fence = re.search(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", text, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1))
        except json.JSONDecodeError:
            pass
    # Greedy: take the largest balanced span starting from the first { or [
    start_obj = text.find("{")
    start_arr = text.find("[")
    candidates = [c for c in (start_obj, start_arr) if c >= 0]
    if not candidates:
        return None
    start = min(candidates)
    # Try progressively shorter slices from end to find valid JSON
    end = len(text)
    while end > start:
        snippet = text[start:end]
        try:
            return json.loads(snippet)
        except json.JSONDecodeError:
            end -= 1
    return None


def safe_get(d: dict[str, Any] | None, *keys: str, default: Any = None) -> Any:
    """Nested .get() that tolerates None / missing keys."""
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return cur


def truncate(text: str, limit: int = 400) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"
