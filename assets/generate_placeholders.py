"""
Generates simple placeholder inspection images for every equipment kind.

These are NOT real equipment photos — they are colored mock-ups that the
demo can show until real images are supplied. Replace ``<kind>_<state>.jpg``
files with actual inspection photos when available.

Run once after editing the catalog:
    python assets/generate_placeholders.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ASSETS = Path(__file__).parent
SIZE = (640, 480)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype("arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


STATE_PALETTES = {
    "normal": {
        "bg": (240, 245, 240),
        "body": (180, 200, 220),
        "shaft": (160, 160, 170),
        "bolt": (90, 90, 95),
        "status": (60, 180, 75),
        "label": "Normal",
        "notes": ["外観上の異常なし", "ボルト周辺に変色なし", "漏れ跡なし"],
    },
    "warning": {
        "bg": (250, 245, 220),
        "body": (200, 190, 170),
        "shaft": (160, 150, 130),
        "bolt": (110, 90, 70),
        "status": (240, 180, 40),
        "label": "Warning",
        "notes": ["ボルト周辺に軽微な変色", "軸受部に微小な漏れ跡の可能性", "追加撮影推奨: 軸受近接画像"],
    },
    "critical": {
        "bg": (250, 230, 225),
        "body": (170, 130, 110),
        "shaft": (130, 100, 90),
        "bolt": (110, 60, 50),
        "status": (220, 60, 60),
        "label": "Critical",
        "notes": ["軸受周辺に明確な漏れ跡", "ボルト周辺に錆び・変色", "外装に油滲み"],
    },
}


def _draw_pump(d: ImageDraw.ImageDraw, palette: dict) -> None:
    d.rounded_rectangle((140, 140, 500, 340), radius=24, fill=palette["body"], outline=(40, 40, 40), width=3)
    d.rectangle((100, 220, 540, 260), fill=palette["shaft"], outline=(40, 40, 40), width=2)
    for cx, cy in [(180, 170), (440, 170), (180, 310), (440, 310)]:
        d.ellipse((cx - 10, cy - 10, cx + 10, cy + 10), fill=palette["bolt"], outline=(20, 20, 20), width=2)


def _draw_motor(d: ImageDraw.ImageDraw, palette: dict) -> None:
    # Box with fins
    d.rectangle((180, 160, 480, 320), fill=palette["body"], outline=(40, 40, 40), width=3)
    for y in range(170, 320, 14):
        d.line((180, y, 480, y), fill=palette["bolt"], width=1)
    # Shaft + coupling
    d.rectangle((480, 220, 580, 260), fill=palette["shaft"], outline=(40, 40, 40), width=2)
    d.ellipse((570, 200, 620, 280), fill=palette["bolt"], outline=(20, 20, 20), width=2)


def _draw_fan(d: ImageDraw.ImageDraw, palette: dict) -> None:
    # Housing
    d.ellipse((140, 100, 500, 380), fill=palette["body"], outline=(40, 40, 40), width=3)
    # Hub
    d.ellipse((290, 220, 350, 280), fill=palette["bolt"], outline=(20, 20, 20), width=2)
    # Three blades
    cx, cy = 320, 250
    for ang_deg in (0, 120, 240):
        import math
        ang = math.radians(ang_deg)
        x2 = cx + int(140 * math.cos(ang))
        y2 = cy + int(140 * math.sin(ang))
        d.line((cx, cy, x2, y2), fill=palette["shaft"], width=12)


def _draw_compressor(d: ImageDraw.ImageDraw, palette: dict) -> None:
    # Tank
    d.rounded_rectangle((130, 200, 520, 340), radius=20, fill=palette["body"], outline=(40, 40, 40), width=3)
    # Cylinder head
    d.rectangle((250, 110, 400, 210), fill=palette["shaft"], outline=(40, 40, 40), width=3)
    # Pressure gauge
    d.ellipse((460, 130, 530, 200), fill="white", outline=(40, 40, 40), width=2)
    d.line((495, 165, 515, 145), fill=palette["status"], width=3)


KIND_DRAWERS = {
    "pump": _draw_pump,
    "motor": _draw_motor,
    "fan": _draw_fan,
    "compressor": _draw_compressor,
}


def _generate(kind: str, state: str) -> Image.Image:
    palette = STATE_PALETTES[state]
    img = Image.new("RGB", SIZE, palette["bg"])
    d = ImageDraw.Draw(img)
    KIND_DRAWERS[kind](d, palette)
    # Status indicator dot (top right)
    d.ellipse((560, 30, 600, 70), fill=palette["status"], outline=(0, 0, 0), width=2)
    # Title
    d.text((20, 20), f"{kind.upper()} / {palette['label']}", fill=(20, 20, 20), font=_font(24))
    # Notes
    y = 380
    for line in palette["notes"]:
        d.text((20, y), f"- {line}", fill=(40, 40, 40), font=_font(16))
        y += 22
    return img


def main() -> None:
    kinds = list(KIND_DRAWERS.keys())
    states = list(STATE_PALETTES.keys())
    for kind in kinds:
        for state in states:
            out = ASSETS / f"{kind}_{state}.jpg"
            img = _generate(kind, state)
            img.save(out, "JPEG", quality=85)
            print(f"wrote {out}")


if __name__ == "__main__":
    main()
