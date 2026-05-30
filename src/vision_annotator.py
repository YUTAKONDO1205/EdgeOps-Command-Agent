"""
Vision evidence renderer.

Takes the structured ``regions`` from the Vision Agent and produces three
artefacts the UI can show the user as **判断根拠の画像**:

1. ``overlay``  — the original photo with each region's normalised bbox
                   drawn as a translucent severity-coloured rectangle plus
                   a label strip (``region_id · severity · confidence%``).
                   This is what the user sees as "AI が見た場所".
2. ``crops``    — per-region cropped thumbnails (≤256 px) for side-by-side
                   inspection in the UI.
3. ``enhanced`` — the same image after auto-contrast + saturation boost,
                   which surfaces subtle discolouration that flat photos hide.
                   Useful for the "AI に見えていたこと" callout.

All outputs are returned as base64 data URLs so they embed straight into
the JSON pipeline response — no extra HTTP hop required from the frontend.
"""
from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps


# Severity → outline RGB. Matches the UI badge palette.
SEVERITY_RGB: dict[str, tuple[int, int, int]] = {
    "normal":   (34, 197, 94),
    "minor":    (245, 158, 11),
    "moderate": (249, 115, 22),
    "severe":   (239, 68, 68),
}
DEFAULT_RGB = (148, 163, 184)


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in ("arial.ttf", "Arial.ttf", "DejaVuSans.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(candidate, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _to_data_url(img: Image.Image, *, quality: int = 85) -> str:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=quality, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _denormalize(bbox: Iterable[float], width: int, height: int) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = list(bbox)[:4]
    # Clamp + ensure ordered.
    x0, x1 = sorted((max(0.0, min(1.0, x0)), max(0.0, min(1.0, x1))))
    y0, y1 = sorted((max(0.0, min(1.0, y0)), max(0.0, min(1.0, y1))))
    return (int(x0 * width), int(y0 * height), int(x1 * width), int(y1 * height))


def _resize_to_max_side(img: Image.Image, max_side: int) -> Image.Image:
    longest = max(img.size)
    if longest <= max_side:
        return img
    ratio = max_side / longest
    return img.resize((int(img.size[0] * ratio), int(img.size[1] * ratio)),
                      Image.Resampling.LANCZOS)


def _draw_label(draw: ImageDraw.ImageDraw, anchor: tuple[int, int],
                text: str, fill: tuple[int, int, int], font) -> None:
    """Draw a pill-style label above the bbox top-left corner."""
    pad_x, pad_y = 4, 2
    try:
        tb = draw.textbbox((0, 0), text, font=font)
        tw, th = tb[2] - tb[0], tb[3] - tb[1]
    except AttributeError:
        # Pillow < 10 / ImageFont.load_default
        tw, th = draw.textsize(text, font=font)
    x, y = anchor
    # Pull label above the box; if it would clip the top, place inside the box.
    y_label = y - th - 2 * pad_y - 2
    inside = y_label < 0
    if inside:
        y_label = y + 2
    box_xy = [x, y_label, x + tw + 2 * pad_x, y_label + th + 2 * pad_y]
    draw.rectangle(box_xy, fill=fill + (235,))
    draw.text((x + pad_x, y_label + pad_y), text, fill=(255, 255, 255), font=font)


def annotate(
    image_bytes: bytes,
    regions: list[dict[str, Any]],
    *,
    max_side: int = 1280,
    crop_thumb_side: int = 256,
    enhance: bool = True,
) -> dict[str, Any]:
    """Render the evidence images for the Vision Agent output.

    Parameters
    ----------
    image_bytes : raw bytes of the primary inspection photo
    regions     : the ``regions`` list from the Vision Agent (each region
                  should carry ``bbox`` as four floats in [0, 1]; regions
                  without bbox are skipped silently)
    max_side    : longest edge for the overlay / enhanced outputs (keeps
                  data URLs reasonable in size for the frontend)
    crop_thumb_side : longest edge for per-region crops

    Returns
    -------
    A dict with ``overlay``, ``enhanced`` (or None), ``crops`` (dict keyed
    by region_id), and ``base_size`` for reference. Failures return a dict
    with the same keys but mostly empty so callers don't have to defend.
    """
    try:
        base = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except Exception:
        return {"overlay": None, "enhanced": None, "crops": {},
                "base_size": [0, 0], "error": "could_not_open_image"}

    base = _resize_to_max_side(base, max_side)
    w, h = base.size

    overlay_rgba = base.convert("RGBA")
    boxes_rgba = Image.new("RGBA", base.size, (0, 0, 0, 0))
    boxes_draw = ImageDraw.Draw(boxes_rgba)
    font = _load_font(max(12, int(min(w, h) * 0.018)))

    crops: dict[str, str] = {}
    drawn_regions = 0

    for region in regions:
        if not isinstance(region, dict):
            continue
        bbox = region.get("bbox")
        if not bbox or len(bbox) < 4:
            continue
        try:
            x0, y0, x1, y1 = _denormalize(bbox, w, h)
        except (ValueError, TypeError):
            continue
        if x1 - x0 < 6 or y1 - y0 < 6:
            continue  # too tiny — almost certainly a parse glitch

        severity = str(region.get("severity", "minor")).lower()
        rgb = SEVERITY_RGB.get(severity, DEFAULT_RGB)

        # Soft fill + sharp outline.
        boxes_draw.rectangle([x0, y0, x1, y1], fill=rgb + (52,))
        boxes_draw.rectangle([x0, y0, x1, y1], outline=rgb + (255,), width=3)

        # Label
        try:
            conf = int(round(float(region.get("confidence_score", 0))))
        except (TypeError, ValueError):
            conf = 0
        label = f"{region.get('region_id', '?')} · {severity} · {conf}%"
        _draw_label(boxes_draw, (x0, y0), label, rgb, font)

        # Per-region crop thumbnail
        pad = int(min(w, h) * 0.015)
        crop_box = (max(0, x0 - pad), max(0, y0 - pad),
                    min(w, x1 + pad), min(h, y1 + pad))
        crop = base.crop(crop_box).convert("RGB")
        crop.thumbnail((crop_thumb_side, crop_thumb_side), Image.Resampling.LANCZOS)
        crops[str(region.get("region_id", f"region-{drawn_regions}"))] = _to_data_url(crop)

        drawn_regions += 1

    overlay = Image.alpha_composite(overlay_rgba, boxes_rgba).convert("RGB")

    enhanced_url = None
    if enhance:
        try:
            adj = ImageOps.autocontrast(base, cutoff=2)
            adj = ImageEnhance.Contrast(adj).enhance(1.15)
            adj = ImageEnhance.Color(adj).enhance(1.10)
            adj = ImageEnhance.Sharpness(adj).enhance(1.10)
            enhanced_url = _to_data_url(adj)
        except Exception:
            enhanced_url = None

    return {
        "overlay": _to_data_url(overlay),
        "enhanced": enhanced_url,
        "crops": crops,
        "base_size": [w, h],
        "rendered_region_count": drawn_regions,
    }


def annotate_path(path: str | Path, regions: list[dict[str, Any]], **kw) -> dict[str, Any]:
    return annotate(Path(path).read_bytes(), regions, **kw)
