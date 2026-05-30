"""Tests for src/teams_notify.py — covers payload shape, env detection, and
the no-op skip path. Network calls are mocked so the test suite stays offline.
"""
from __future__ import annotations

import json
from unittest.mock import patch

from src import teams_notify


def test_is_configured_false_without_env():
    assert teams_notify.is_configured() is False


def test_is_configured_true_with_webhook(monkeypatch):
    monkeypatch.setenv("TEAMS_WEBHOOK_URL", "https://example.invalid/hook")
    assert teams_notify.is_configured() is True


def test_skip_path_without_webhook():
    r = teams_notify.notify_alert(
        equipment_id="Pump-03",
        risk_level="Critical",
        health_score=42,
        primary_concern="軸受帯域",
    )
    assert r.ok is False
    assert r.payload_kind == "skipped"
    assert r.status_code is None


def test_adaptive_card_shape_contains_required_facts():
    raw = teams_notify.preview_payload(
        equipment_id="Pump-03",
        risk_level="Critical",
        health_score=42,
        primary_concern="軸受帯域エネルギー増加",
        deadline_hours=24,
        body_lines=["温度 52℃", "異音継続"],
    )
    payload = json.loads(raw)
    # Envelope
    assert payload["type"] == "message"
    assert payload["attachments"][0]["contentType"] == "application/vnd.microsoft.card.adaptive"
    body = payload["attachments"][0]["content"]["body"]
    # First TextBlock is the title
    assert "Pump-03" in body[0]["text"]
    # FactSet contains every label we promise the UI shows
    factset = next(b for b in body if b["type"] == "FactSet")
    titles = {f["title"] for f in factset["facts"]}
    assert {"設備", "リスク", "ヘルススコア", "主要懸念", "対応期限"} <= titles


def test_adaptive_card_falls_back_to_messagecard(monkeypatch):
    """When the AdaptiveCard POST returns 4xx, the legacy MessageCard form is sent."""
    monkeypatch.setenv("TEAMS_WEBHOOK_URL", "https://example.invalid/hook")
    sent_payloads: list[dict] = []

    class FakeResp:
        def __init__(self, status_code: int):
            self.status_code = status_code
            self.text = "bad request"

    def fake_post(url, json=None, headers=None, timeout=None):
        sent_payloads.append(json)
        # First call (adaptive) fails, second (messagecard) succeeds.
        return FakeResp(400 if len(sent_payloads) == 1 else 200)

    with patch("src.teams_notify.requests.post", side_effect=fake_post):
        r = teams_notify.notify_alert(
            equipment_id="Pump-03", risk_level="Warning",
            health_score=70, primary_concern="温度",
        )
    assert r.ok is True
    assert r.payload_kind == "messagecard"
    assert len(sent_payloads) == 2
    # Second payload uses the MessageCard schema
    assert sent_payloads[1]["@type"] == "MessageCard"


def test_request_exception_returns_error_result(monkeypatch):
    monkeypatch.setenv("TEAMS_WEBHOOK_URL", "https://example.invalid/hook")
    import requests
    with patch("src.teams_notify.requests.post",
               side_effect=requests.RequestException("network down")):
        r = teams_notify.notify_alert(
            equipment_id="Pump-03", risk_level="Critical",
            health_score=10, primary_concern="x",
        )
    assert r.ok is False
    assert "network down" in r.detail
