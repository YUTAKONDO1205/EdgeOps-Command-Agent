"""
Microsoft Teams Incoming Webhook 連携。

Power Automate の "Post adaptive card in chat" や Teams の Incoming Webhook
URL に対して Adaptive Card v1.4 を POST します。Webhook URL が未設定の場合は
no-op を返してアプリ全体は止めません。

使い方
------
    from src.teams_notify import notify_alert, is_configured

    if is_configured():
        notify_alert(
            equipment_id="Pump-03",
            risk_level="Critical",
            health_score=42,
            primary_concern="軸受帯域エネルギー増加",
            deadline_hours=24,
            work_order_url=None,  # optional
        )
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import requests

from .utils import load_env


_TEAMS_COLOR_BY_RISK = {
    "Normal": "Good",
    "Warning": "Warning",
    "Critical": "Attention",
}

_RGB_BY_RISK = {
    "Normal": "#22c55e",
    "Warning": "#f59e0b",
    "Critical": "#ef4444",
}


@dataclass
class TeamsConfig:
    webhook_url: str | None

    @classmethod
    def from_env(cls) -> "TeamsConfig":
        load_env()
        return cls(webhook_url=os.getenv("TEAMS_WEBHOOK_URL") or None)


def is_configured() -> bool:
    return bool(TeamsConfig.from_env().webhook_url)


def _build_adaptive_card(
    *,
    equipment_id: str,
    risk_level: str,
    health_score: int,
    primary_concern: str,
    deadline_hours: int | str | None,
    body_lines: list[str] | None = None,
    work_order_url: str | None = None,
) -> dict[str, Any]:
    color = _TEAMS_COLOR_BY_RISK.get(risk_level, "Default")
    body_lines = body_lines or []

    facts = [
        {"title": "設備", "value": equipment_id},
        {"title": "リスク", "value": risk_level},
        {"title": "ヘルススコア", "value": f"{health_score}/100"},
        {"title": "主要懸念", "value": primary_concern},
    ]
    if deadline_hours is not None:
        facts.append({"title": "対応期限", "value": f"{deadline_hours} 時間以内"})

    card_body: list[dict[str, Any]] = [
        {
            "type": "TextBlock",
            "size": "Large",
            "weight": "Bolder",
            "color": color,
            "text": f"🛠 EdgeOps Alert — {equipment_id}",
        },
        {
            "type": "TextBlock",
            "spacing": "None",
            "isSubtle": True,
            "text": f"リスクレベル: {risk_level}",
        },
        {
            "type": "FactSet",
            "facts": facts,
        },
    ]
    if body_lines:
        card_body.append({
            "type": "TextBlock",
            "wrap": True,
            "text": "  \n".join(body_lines),
        })

    actions: list[dict[str, Any]] = []
    if work_order_url:
        actions.append({
            "type": "Action.OpenUrl",
            "title": "作業指示書を開く",
            "url": work_order_url,
        })

    card_content = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": card_body,
    }
    if actions:
        card_content["actions"] = actions

    # Wrap in MessageCard envelope that both Incoming Webhook and Power Automate
    # accept. Power Automate also accepts the raw AdaptiveCard payload directly,
    # so we send both shapes via "attachments" — receivers pick what they parse.
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": card_content,
            }
        ],
    }


def _build_legacy_message_card(
    *,
    equipment_id: str,
    risk_level: str,
    health_score: int,
    primary_concern: str,
    deadline_hours: int | str | None,
    body_lines: list[str] | None = None,
    work_order_url: str | None = None,
) -> dict[str, Any]:
    """Legacy O365 connector "MessageCard" format. Some Teams Incoming Webhook
    endpoints still expect this rather than AdaptiveCard. We send this as a
    fallback so the same module works with classic webhooks too."""
    theme_color = _RGB_BY_RISK.get(risk_level, "0078D4").lstrip("#")
    facts = [
        {"name": "設備", "value": equipment_id},
        {"name": "リスク", "value": risk_level},
        {"name": "ヘルススコア", "value": f"{health_score}/100"},
        {"name": "主要懸念", "value": primary_concern},
    ]
    if deadline_hours is not None:
        facts.append({"name": "対応期限", "value": f"{deadline_hours} 時間以内"})
    sections = [{"activityTitle": f"🛠 EdgeOps Alert — {equipment_id}",
                 "activitySubtitle": f"リスクレベル: {risk_level}",
                 "facts": facts,
                 "markdown": True}]
    if body_lines:
        sections.append({"text": "  \n".join(body_lines)})

    payload: dict[str, Any] = {
        "@type": "MessageCard",
        "@context": "https://schema.org/extensions",
        "summary": f"EdgeOps Alert {equipment_id} {risk_level}",
        "themeColor": theme_color,
        "title": f"EdgeOps Alert — {equipment_id}",
        "sections": sections,
    }
    if work_order_url:
        payload["potentialAction"] = [{
            "@type": "OpenUri",
            "name": "作業指示書を開く",
            "targets": [{"os": "default", "uri": work_order_url}],
        }]
    return payload


@dataclass
class TeamsNotifyResult:
    ok: bool
    status_code: int | None
    detail: str
    payload_kind: str   # "adaptive" | "messagecard" | "skipped"


def notify_alert(
    *,
    equipment_id: str,
    risk_level: str,
    health_score: int,
    primary_concern: str,
    deadline_hours: int | str | None = None,
    body_lines: list[str] | None = None,
    work_order_url: str | None = None,
    timeout: float = 8.0,
    config: TeamsConfig | None = None,
) -> TeamsNotifyResult:
    """Send a Teams notification. Returns a TeamsNotifyResult instead of
    raising — the UI uses it to render success/failure feedback."""
    cfg = config or TeamsConfig.from_env()
    if not cfg.webhook_url:
        return TeamsNotifyResult(ok=False, status_code=None,
                                 detail="TEAMS_WEBHOOK_URL is not configured",
                                 payload_kind="skipped")

    common = dict(
        equipment_id=equipment_id,
        risk_level=risk_level,
        health_score=health_score,
        primary_concern=primary_concern,
        deadline_hours=deadline_hours,
        body_lines=body_lines,
        work_order_url=work_order_url,
    )

    # Try AdaptiveCard envelope first (Power Automate flow style).
    payload = _build_adaptive_card(**common)
    try:
        resp = requests.post(
            cfg.webhook_url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )
        if 200 <= resp.status_code < 300:
            return TeamsNotifyResult(ok=True, status_code=resp.status_code,
                                     detail="Adaptive Card delivered.",
                                     payload_kind="adaptive")
    except requests.RequestException as exc:
        return TeamsNotifyResult(ok=False, status_code=None,
                                 detail=f"Network error: {exc}",
                                 payload_kind="adaptive")

    # Fallback: classic MessageCard for legacy Incoming Webhook endpoints.
    legacy = _build_legacy_message_card(**common)
    try:
        resp2 = requests.post(
            cfg.webhook_url,
            json=legacy,
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )
        if 200 <= resp2.status_code < 300:
            return TeamsNotifyResult(ok=True, status_code=resp2.status_code,
                                     detail="MessageCard delivered (legacy fallback).",
                                     payload_kind="messagecard")
        return TeamsNotifyResult(ok=False, status_code=resp2.status_code,
                                 detail=resp2.text[:300],
                                 payload_kind="messagecard")
    except requests.RequestException as exc:
        return TeamsNotifyResult(ok=False, status_code=None,
                                 detail=f"Network error: {exc}",
                                 payload_kind="messagecard")


def preview_payload(**kwargs: Any) -> str:
    """Return the JSON payload (AdaptiveCard form) for inspection in the UI."""
    return json.dumps(_build_adaptive_card(**kwargs), ensure_ascii=False, indent=2)
