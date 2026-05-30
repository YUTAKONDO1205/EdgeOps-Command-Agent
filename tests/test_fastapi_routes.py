"""TestClient-based integration for backend/main.py.

The test runs in mock mode + all Azure services disabled, so what we're
verifying is the routing wiring + JSON shapes — not the actual cloud calls."""
from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.main import app


@pytest.fixture
def client(tmp_side_files):
    return TestClient(app)


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["mock_mode"] is True
    # All Azure services should report unconfigured in the test env
    for k in ("blob_configured", "cosmos_configured", "teams_configured"):
        assert data[k] is False


def test_presets_lists_all_demo_keys(client):
    r = client.get("/api/presets")
    assert r.status_code == 200
    keys = {p["key"] for p in r.json()["presets"]}
    assert {"normal", "warning", "critical", "ambiguous"} <= keys


def test_analyze_returns_full_payload(client):
    r = client.post("/api/analyze", json={"preset_key": "critical"})
    assert r.status_code == 200
    data = r.json()
    assert data["risk"]["risk_level"] == "Critical"
    # The 6 core agents must always be present; intake + governance are
    # also expected on every modern run.
    expected = {"intake", "signal", "vision", "manual_rag", "root_cause",
                "action_plan", "whatif", "governance"}
    assert set(data["agents"].keys()) == expected
    assert "## 作業手順" in data["work_order_md"]
    assert "エグゼクティブサマリー" in data["management_report_md"]


def test_analyze_unknown_preset_returns_404(client):
    r = client.post("/api/analyze", json={"preset_key": "no-such-preset"})
    assert r.status_code == 404


def test_teams_notify_returns_skipped_without_webhook(client):
    r = client.post("/api/teams/notify", json={
        "equipment_id": "Pump-03", "risk_level": "Critical",
        "health_score": 10, "primary_concern": "x",
    })
    assert r.status_code == 200
    assert r.json()["payload_kind"] == "skipped"


def test_approval_persists_to_local_store(client):
    r = client.post("/api/approval", json={
        "equipment_id": "Pump-03", "artifact": "Work Order",
        "action": "承認", "comment": "test approval",
        "risk_level": "Critical",
    })
    assert r.status_code == 200
    assert r.json()["saved"] is True
    # And shows up in the per-equipment history
    rr = client.get("/api/runs/Pump-03")
    assert rr.status_code == 200
    events = rr.json()["runs"]
    assert any(e.get("doc_type") == "approval" and e.get("action") == "承認" for e in events)


def test_upload_image_stores_locally(client, tmp_path):
    blob = b"\xff\xd8\xff\xe0fakejpeg"
    r = client.post("/api/upload/image",
                    files={"file": ("test.jpg", io.BytesIO(blob), "image/jpeg")})
    assert r.status_code == 200
    data = r.json()
    assert data["backend"] == "local"
    assert "test.jpg" in data["blob_name"]


def test_upload_pdf_chunks_and_indexes(client):
    from tests.test_pdf_loader import _hand_built_pdf
    pdf = _hand_built_pdf([
        "Chapter 1 intro",
        "Chapter 2 vibration\nVibration RMS threshold 0.30G is critical.",
    ])
    r = client.post("/api/upload/pdf",
                    files={"file": ("m.pdf", io.BytesIO(pdf), "application/pdf")})
    assert r.status_code == 200
    data = r.json()
    assert data["rag"]["chunks_extracted"] >= 1
    assert data["blob"]["backend"] == "local"


def test_upload_pdf_rejects_non_pdf(client):
    r = client.post("/api/upload/pdf",
                    files={"file": ("m.txt", io.BytesIO(b"hi"), "text/plain")})
    assert r.status_code == 400


def test_spresense_recent_empty_when_no_stream(client):
    r = client.get("/api/spresense/recent")
    assert r.status_code == 200
    assert r.json()["source"] == "empty"
