"""
BBOX verification helper. Implements the crop→draw→open→fix→repeat loop
disciplined by the `bbox-draw-loop` skill, adapted for the equipment
photos shipped in ``assets/``.

Usage (from project root, e.g. inside a Python REPL or via -c):

    from tools.bbox_verify import (
        draw_single, draw_all_for_kind, crop_for,
        load_layouts, save_layouts, dump_current,
    )

    # 1. raw crop — confirm we're looking at the right part of the photo
    crop_for("pump", "bearing-housing", "output/bbox_check/_raw.png")

    # 2. annotated overlay (single bbox)
    draw_single("pump", "bearing-housing",
                bbox=(0.50, 0.30, 0.78, 0.60),
                out="output/bbox_check/pump_bearing.png")

    # 3. all bboxes for a kind on a single image (sanity check the set)
    draw_all_for_kind("pump", out="output/bbox_check/pump_all.png")

All outputs go under ``output/bbox_check/`` so the working files stay
out of the source tree.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# Make ``src`` importable when running from project root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from src import equipment_catalog  # noqa: E402
from src.utils import ASSETS_DIR  # noqa: E402


OUTPUT_DIR = PROJECT_ROOT / "output" / "bbox_check"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# 12-colour palette so adjacent regions on the same image stay visually
# distinguishable (severity-based colours collapse to a single hue per kind).
PALETTE = [
    (220, 38, 38),    # red
    (37, 99, 235),    # blue
    (22, 163, 74),    # green
    (217, 119, 6),    # orange
    (147, 51, 234),   # purple
    (14, 165, 233),   # cyan
    (236, 72, 153),   # pink
    (132, 204, 22),   # lime
    (180, 83, 9),     # amber-700
    (6, 95, 70),      # emerald-900
    (190, 24, 93),    # rose-700
    (88, 28, 135),    # purple-900
]


def _load_font(size: int):
    for c in ("arial.ttf", "Arial.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(c, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _photo_path(kind: str, intensity: str = "normal") -> Path:
    return equipment_catalog._img(kind, intensity)


def _denorm(bbox, w: int, h: int) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = bbox
    x0, x1 = sorted((max(0.0, min(1.0, x0)), max(0.0, min(1.0, x1))))
    y0, y1 = sorted((max(0.0, min(1.0, y0)), max(0.0, min(1.0, y1))))
    return int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h)


def crop_for(kind: str, region_id: str, out: str | Path | None = None,
             intensity: str = "normal") -> Path:
    """Step 1 of the loop: raw crop from the photo at the *current* layout
    coordinates, with a tiny margin. No overlay yet — just confirms we are
    looking at the right area of the image."""
    img = Image.open(_photo_path(kind, intensity))
    w, h = img.size
    bbox = equipment_catalog.default_bbox(_kind_to_equipment(kind), region_id, intensity)
    margin = 0.04  # show context around the bbox
    x0, y0, x1, y1 = bbox
    crop_bbox = (max(0.0, x0 - margin), max(0.0, y0 - margin),
                 min(1.0, x1 + margin), min(1.0, y1 + margin))
    px = _denorm(crop_bbox, w, h)
    out_path = Path(out) if out else OUTPUT_DIR / f"{kind}_{region_id}_raw.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.crop(px).save(out_path, "PNG")
    return out_path


def _kind_to_equipment(kind: str) -> str:
    """Pick any equipment_id of the requested kind so default_bbox resolves."""
    for spec in equipment_catalog.list_equipment():
        if spec.kind == kind:
            return spec.id
    return "Pump-03"


def draw_single(
    kind: str,
    region_id: str,
    *,
    bbox: tuple[float, float, float, float] | None = None,
    out: str | Path | None = None,
    intensity: str = "normal",
    color: tuple[int, int, int] = (220, 38, 38),
    draw_label: bool = True,
) -> Path:
    """Step 2: render a single bbox over the full photo so we can verify
    placement against the real feature. Save without scaling so pixel-level
    misalignment is visible at full resolution."""
    if bbox is None:
        bbox = equipment_catalog.default_bbox(_kind_to_equipment(kind), region_id, intensity)
    img = Image.open(_photo_path(kind, intensity)).convert("RGB")
    w, h = img.size
    x0, y0, x1, y1 = _denorm(bbox, w, h)
    overlay = img.copy()
    rgba = overlay.convert("RGBA")
    fill = Image.new("RGBA", img.size, (0, 0, 0, 0))
    fd = ImageDraw.Draw(fill)
    fd.rectangle([x0, y0, x1, y1], fill=color + (40,))
    fd.rectangle([x0, y0, x1, y1], outline=color + (255,), width=4)
    merged = Image.alpha_composite(rgba, fill).convert("RGB")
    if draw_label:
        d = ImageDraw.Draw(merged)
        font = _load_font(max(14, int(min(w, h) * 0.020)))
        text = region_id
        try:
            tb = d.textbbox((0, 0), text, font=font)
            tw, th = tb[2] - tb[0], tb[3] - tb[1]
        except AttributeError:
            tw, th = d.textsize(text, font=font)
        pad = 5
        ly = max(0, y0 - th - 2 * pad - 2)
        d.rectangle([x0, ly, x0 + tw + 2 * pad, ly + th + 2 * pad], fill=color + (255,))
        d.text((x0 + pad, ly + pad), text, fill=(255, 255, 255), font=font)
    out_path = Path(out) if out else OUTPUT_DIR / f"{kind}_{region_id}.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    merged.save(out_path, "PNG")
    return out_path


def draw_all_for_kind(
    kind: str,
    *,
    layouts: dict[str, tuple[float, float, float, float]] | None = None,
    out: str | Path | None = None,
    intensity: str = "normal",
) -> Path:
    """Render every region for the kind on a single image, each in its
    own palette colour. Use after individual regions are tight to check
    the set isn't accidentally overlapping or leaving holes."""
    if layouts is None:
        kind_layouts = equipment_catalog.BBOX_LAYOUTS[kind]
        layouts = kind_layouts.get(intensity) or kind_layouts.get("normal") or {}
    img = Image.open(_photo_path(kind, intensity)).convert("RGB")
    w, h = img.size
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    font = _load_font(max(14, int(min(w, h) * 0.018)))
    for i, (region_id, bbox) in enumerate(layouts.items()):
        color = PALETTE[i % len(PALETTE)]
        x0, y0, x1, y1 = _denorm(bbox, w, h)
        od.rectangle([x0, y0, x1, y1], fill=color + (35,))
        od.rectangle([x0, y0, x1, y1], outline=color + (255,), width=3)
        text = region_id
        try:
            tb = od.textbbox((0, 0), text, font=font)
            tw, th = tb[2] - tb[0], tb[3] - tb[1]
        except AttributeError:
            tw, th = od.textsize(text, font=font)
        pad = 4
        ly = max(0, y0 - th - 2 * pad - 2)
        od.rectangle([x0, ly, x0 + tw + 2 * pad, ly + th + 2 * pad], fill=color + (245,))
        od.text((x0 + pad, ly + pad), text, fill=(255, 255, 255), font=font)
    merged = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    out_path = Path(out) if out else OUTPUT_DIR / f"{kind}_ALL.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    merged.save(out_path, "PNG")
    return out_path


def overlay_grid(kind: str, *, intensity: str = "normal",
                 step: float = 0.05, out: str | Path | None = None) -> Path:
    """Burn a percentage gridline overlay onto the photo so coordinates can
    be read directly off the image. Major gridlines every ``step`` (default
    5%); minor every step/5. Used to derive normalised bboxes by eye."""
    img = Image.open(_photo_path(kind, intensity)).convert("RGB")
    w, h = img.size
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    font = _load_font(max(11, int(min(w, h) * 0.013)))
    # Minor gridlines
    minor = step / 5.0
    minor_px = max(1, int(round(min(w, h) * minor)))
    for p in [i * minor for i in range(int(1.0 / minor) + 1)]:
        x = int(p * w)
        y = int(p * h)
        d.line([(x, 0), (x, h)], fill=(80, 100, 200, 110), width=1)
        d.line([(0, y), (w, y)], fill=(80, 100, 200, 110), width=1)
    # Major gridlines + labels
    for p in [i * step for i in range(int(1.0 / step) + 1)]:
        x = int(p * w)
        y = int(p * h)
        d.line([(x, 0), (x, h)], fill=(255, 0, 0, 200), width=2)
        d.line([(0, y), (w, y)], fill=(255, 0, 0, 200), width=2)
        label = f"{p:.2f}"
        # x-axis labels on the top
        d.rectangle([x + 2, 2, x + 50, 22], fill=(0, 0, 0, 220))
        d.text((x + 4, 4), label, fill=(255, 255, 255), font=font)
        # y-axis labels on the left
        d.rectangle([2, y + 2, 50, y + 22], fill=(0, 0, 0, 220))
        d.text((4, y + 4), label, fill=(255, 255, 255), font=font)
    merged = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    out_path = Path(out) if out else OUTPUT_DIR / f"{kind}_grid.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    merged.save(out_path, "PNG")
    return out_path


def dump_current(kind: str, intensity: str = "normal") -> str:
    """Pretty-print the currently registered bboxes for (kind, intensity)."""
    kind_layouts = equipment_catalog.BBOX_LAYOUTS[kind]
    layout = kind_layouts.get(intensity) or kind_layouts.get("normal") or {}
    lines = [f"# {kind} / {intensity}"]
    for rid, bbox in layout.items():
        lines.append(f"  {rid:20s} = ({bbox[0]:.3f}, {bbox[1]:.3f}, {bbox[2]:.3f}, {bbox[3]:.3f})")
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Visual verification of bbox layouts")
    ap.add_argument("kind", choices=["pump", "motor", "fan", "compressor"])
    ap.add_argument("--region", default=None)
    ap.add_argument("--bbox", nargs=4, type=float, default=None,
                    help="x0 y0 x1 y1 (normalised). Overrides the catalog value.")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    if args.region:
        bbox = tuple(args.bbox) if args.bbox else None
        path = draw_single(args.kind, args.region, bbox=bbox, out=args.out)
    else:
        path = draw_all_for_kind(args.kind, out=args.out)
    print(path)
