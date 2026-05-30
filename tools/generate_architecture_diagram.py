"""Generate the EdgeOps Command Agent architecture diagram as a PNG.

Why this exists
---------------
Hackathon rules require the architecture diagram be embedded in the Zenn
article. We render it deterministically from Python rather than checking in
a binary PNG that nobody can re-derive, so any later refactor can rebuild
the diagram from this script.

Usage:
    python tools/generate_architecture_diagram.py

Output:
    docs/architecture.png  (1920x1080, 144 DPI)
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT = PROJECT_ROOT / "docs" / "architecture.png"

W, H = 1920, 1080
PAD = 40

# Palette tuned for legibility when scaled down to a Zenn article column.
BG = (245, 247, 250)
INK = (15, 23, 42)
MUTED = (100, 116, 139)
CARD = (255, 255, 255)
BORDER = (203, 213, 225)

AZURE_BLUE = (0, 120, 212)
AGENT_PURPLE = (124, 58, 237)
EDGE_TEAL = (8, 145, 178)
ALERT_ORANGE = (234, 88, 12)
SAFE_GREEN = (22, 163, 74)
DANGER_RED = (220, 38, 38)


def _font(size: int, bold: bool = False):
    candidates = [
        "C:/Windows/Fonts/YuGothB.ttc" if bold else "C:/Windows/Fonts/YuGothR.ttc",
        "C:/Windows/Fonts/meiryob.ttc" if bold else "C:/Windows/Fonts/meiryo.ttc",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
    ]
    for c in candidates:
        if Path(c).exists():
            try:
                return ImageFont.truetype(c, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _box(draw, xy, fill, border, radius=14, width=2):
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=border, width=width)


def _text(draw, xy, text, font, fill=INK, anchor="lt"):
    draw.text(xy, text, fill=fill, font=font, anchor=anchor)


def _multiline(draw, xy, lines, font, fill=INK, line_height=None):
    if line_height is None:
        line_height = font.size + 6
    x, y = xy
    for ln in lines:
        draw.text((x, y), ln, fill=fill, font=font)
        y += line_height


def _arrow(draw, p1, p2, color=MUTED, width=2, head=10):
    draw.line([p1, p2], fill=color, width=width)
    x1, y1 = p1
    x2, y2 = p2
    import math
    angle = math.atan2(y2 - y1, x2 - x1)
    left = (x2 - head * math.cos(angle - math.pi / 7),
            y2 - head * math.sin(angle - math.pi / 7))
    right = (x2 - head * math.cos(angle + math.pi / 7),
             y2 - head * math.sin(angle + math.pi / 7))
    draw.polygon([p2, left, right], fill=color)


def main() -> None:
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    f_title = _font(48, bold=True)
    f_h2 = _font(28, bold=True)
    f_h3 = _font(22, bold=True)
    f_body = _font(18)
    f_small = _font(15)
    f_pill = _font(15, bold=True)

    # Title bar
    draw.rounded_rectangle((PAD, PAD, W - PAD, PAD + 88), radius=16,
                            fill=(15, 23, 42), outline=None)
    _text(draw, (PAD + 28, PAD + 14),
          "EdgeOps Command Agent — System Architecture",
          f_title, fill=(255, 255, 255))
    _text(draw, (PAD + 28, PAD + 62),
          "Microsoft Agent Hackathon 2026  ·  Azure × Multi-Agent × Edge AI",
          f_small, fill=(203, 213, 225))

    y = PAD + 88 + 28

    # ── Layer 1: Edge / Ingest ──────────────────────────────────────────
    layer_h = 130
    _box(draw, (PAD, y, W - PAD, y + layer_h), fill=(236, 254, 255),
         border=EDGE_TEAL, radius=18)
    _text(draw, (PAD + 24, y + 12),
          "① Edge / Ingest", f_h2, fill=EDGE_TEAL)
    blocks = [
        ("Spresense\n(振動 / 音響 / 温度 / 電流)", PAD + 24),
        ("Sensor CSV /\nFFT 特徴量", PAD + 280),
        ("点検写真\n(複数アングル可)", PAD + 540),
        ("PDF マニュアル\n+ 故障履歴 + 部品在庫", PAD + 800),
        ("作業者の点検メモ", PAD + 1140),
    ]
    for txt, x0 in blocks:
        _box(draw, (x0, y + 55, x0 + 230, y + layer_h - 10),
             fill=CARD, border=EDGE_TEAL, radius=10)
        for i, ln in enumerate(txt.split("\n")):
            _text(draw, (x0 + 14, y + 65 + i * 22), ln, f_small)

    # Cloud sink for edge data
    _box(draw, (PAD + 1400, y + 40, W - PAD - 24, y + layer_h - 10),
         fill=(219, 234, 254), border=AZURE_BLUE, radius=10)
    _text(draw, (PAD + 1414, y + 50), "Azure Event Hubs", f_h3, fill=AZURE_BLUE)
    _text(draw, (PAD + 1414, y + 80), "AMQP / HTTPS で集約", f_small, fill=MUTED)
    _text(draw, (PAD + 1414, y + 100), "未設定なら local JSONL", f_small, fill=MUTED)

    y += layer_h + 24

    # Down arrow
    _arrow(draw, (W // 2, y - 16), (W // 2, y + 10), color=INK, width=3, head=14)
    y += 22

    # ── Layer 2: Multi-Agent Orchestrator (8 agents) ─────────────────
    orch_h = 360
    _box(draw, (PAD, y, W - PAD, y + orch_h), fill=(250, 245, 255),
         border=AGENT_PURPLE, radius=18)
    _text(draw, (PAD + 24, y + 12),
          "② Multi-Agent Orchestrator", f_h2, fill=AGENT_PURPLE)
    _text(draw, (PAD + 360, y + 22),
          "Semantic Kernel `Kernel` + `AzureChatCompletion`  ·  Azure OpenAI (Microsoft Foundry)  ·  Mock fallback",
          f_small, fill=MUTED)

    agents8 = [
        ("①\nIntake",          "データ検証\n設備/期間整合"),
        ("②\nSignal Insight",  "FFT / RMS\nしきい値解釈"),
        ("③\nVision",          "領域分割\nseverity 判定"),
        ("④\nManual RAG",      "Azure AI Search\n判断根拠抽出"),
        ("⑤\nRoot Cause",      "尤度付き仮説\n×3"),
        ("⑥\nAction Planning", "作業手順\n工具 / 部品 / 期限"),
        ("⑦\nWhat-if",         "今点検 / 3日後 /\n1週間放置 比較"),
        ("⑧\nGovernance",      "不確実性集約\nHuman-in-the-loop"),
    ]
    card_w, card_h = 200, 200
    gap = (W - 2 * PAD - 8 * card_w) // 9
    cx = PAD + gap
    cy = y + 70
    for i, (name, sub) in enumerate(agents8):
        accent = AGENT_PURPLE
        if i == 0 or i == 7:
            accent = SAFE_GREEN if i == 7 else EDGE_TEAL
        _box(draw, (cx, cy, cx + card_w, cy + card_h), fill=CARD,
             border=accent, radius=12, width=2)
        # Number pill on top
        for j, ln in enumerate(name.split("\n")):
            _text(draw, (cx + card_w // 2, cy + 14 + j * 28), ln,
                  f_h3, fill=accent, anchor="mt")
        # Sub-text
        for j, ln in enumerate(sub.split("\n")):
            _text(draw, (cx + card_w // 2, cy + 96 + j * 22), ln,
                  f_small, fill=INK, anchor="mt")
        if i < len(agents8) - 1:
            _arrow(draw,
                   (cx + card_w + 4, cy + card_h // 2),
                   (cx + card_w + gap - 4, cy + card_h // 2),
                   color=AGENT_PURPLE, width=2, head=8)
        cx += card_w + gap

    # Rule Engine sidecar
    rule_y = cy + card_h + 30
    _box(draw, (PAD + 24, rule_y, PAD + 460, rule_y + 50),
         fill=(220, 252, 231), border=SAFE_GREEN, radius=10)
    _text(draw, (PAD + 38, rule_y + 14),
          "Rule Engine (監査可能 / LLM 外)  →  Risk Level の最終決定",
          f_small, fill=SAFE_GREEN)
    # HITL sidecar
    _box(draw, (PAD + 1080, rule_y, W - PAD - 24, rule_y + 50),
         fill=(252, 231, 243), border=DANGER_RED, radius=10)
    _text(draw, (PAD + 1098, rule_y + 14),
          "Human Approval  ·  承認 / 修正依頼 / 却下 + 理由テキスト",
          f_small, fill=DANGER_RED)

    y += orch_h + 24

    # Down arrow
    _arrow(draw, (W // 2, y - 16), (W // 2, y + 10), color=INK, width=3, head=14)
    y += 22

    # ── Layer 3: Outputs ─────────────────────────────────────────────
    out_h = 130
    _box(draw, (PAD, y, W - PAD, y + out_h), fill=(255, 247, 237),
         border=ALERT_ORANGE, radius=18)
    _text(draw, (PAD + 24, y + 12),
          "③ Outputs / Human-facing Artifacts",
          f_h2, fill=ALERT_ORANGE)
    outputs = [
        ("Command Center", "Health Score /\nRisk / 影響範囲"),
        ("Work Order", "現場作業者向け\n7ステップ手順"),
        ("Management Report", "1ページ\n経営層向け報告"),
        ("Teams Notification", "Adaptive Card\n(Power Automate)"),
        ("Audit Log Export", "JSON / CSV\n(Cosmos)"),
    ]
    bw = (W - 2 * PAD - 48) // len(outputs)
    bx = PAD + 24
    for title, sub in outputs:
        _box(draw, (bx, y + 50, bx + bw - 12, y + out_h - 10),
             fill=CARD, border=ALERT_ORANGE, radius=10)
        _text(draw, (bx + 14, y + 56), title, f_h3, fill=ALERT_ORANGE)
        for i, ln in enumerate(sub.split("\n")):
            _text(draw, (bx + 14, y + 86 + i * 18), ln, f_small, fill=MUTED)
        bx += bw

    y += out_h + 24

    # ── Layer 4: Data / Persistence ─────────────────────────────────
    persist_h = 110
    _box(draw, (PAD, y, W - PAD, y + persist_h), fill=(219, 234, 254),
         border=AZURE_BLUE, radius=18)
    _text(draw, (PAD + 24, y + 12),
          "④ Persistence / Azure Services",
          f_h2, fill=AZURE_BLUE)
    services = [
        ("Azure OpenAI", "Foundry · gpt-4o"),
        ("Azure AI Search", "Manual RAG"),
        ("Azure Blob Storage", "CSV / 画像 / PDF"),
        ("Azure Cosmos DB", "監査 / 実行履歴 / 通知"),
        ("Azure Container Apps", "FastAPI + Streamlit"),
        ("GitHub Actions", "CD パイプライン"),
    ]
    sw = (W - 2 * PAD - 48) // len(services)
    sx = PAD + 24
    for title, sub in services:
        _box(draw, (sx, y + 50, sx + sw - 12, y + persist_h - 10),
             fill=CARD, border=AZURE_BLUE, radius=10)
        _text(draw, (sx + 14, y + 56), title, f_h3, fill=AZURE_BLUE)
        _text(draw, (sx + 14, y + 84), sub, f_small, fill=MUTED)
        sx += sw

    # Footer
    _text(draw, (PAD + 24, H - 36),
          "© Kondo Yuta · EdgeOps Command Agent  ·  Microsoft Agent Hackathon 2026",
          f_small, fill=MUTED)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, "PNG", optimize=True)
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
