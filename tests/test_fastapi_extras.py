"""Tests for the routes added on top of the original FastAPI surface:
analyze/with-uploads, audit log export, AI-Search seed, and SSE stream."""
from __future__ import annotations

import io
import json
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from src import iot_ingest, utils


@pytest.fixture
def client(tmp_side_files):
    return TestClient(app)


def test_analyze_with_uploads_requires_csv(client):
    r = client.post("/api/analyze/with-uploads", data={"equipment_id": "Pump-03"})
    assert r.status_code == 400


def test_analyze_with_uploads_csv_only(client):
    csv_bytes = (utils.DATA_DIR / "warning_sensor.csv").read_bytes()
    r = client.post(
        "/api/analyze/with-uploads",
        data={"equipment_id": "Pump-99", "inspection_memo": "ユーザー指定のメモ"},
        files={"sensor_csv": ("warning.csv", io.BytesIO(csv_bytes), "text/csv")},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["equipment_id"] == "Pump-99"
    assert data["preset_key"] == "uploaded"
    assert data["risk"]["risk_level"] in {"Warning", "Critical"}


def test_analyze_with_uploads_with_image_persists_blob(client, tmp_side_files):
    csv_bytes = (utils.DATA_DIR / "normal_sensor.csv").read_bytes()
    image_bytes = b"\xff\xd8\xff\xe0fakejpeg-bytes"
    r = client.post(
        "/api/analyze/with-uploads",
        data={"equipment_id": "Pump-77", "inspection_memo": ""},
        files={
            "sensor_csv": ("s.csv", io.BytesIO(csv_bytes), "text/csv"),
            "image": ("inspect.jpg", io.BytesIO(image_bytes), "image/jpeg"),
        },
    )
    assert r.status_code == 200
    # The image should have landed in the local blob fallback area.
    # ``primary`` subdir distinguishes the main inspection shot from extra /
    # reference images now that the route accepts multiple uploads.
    cached = tmp_side_files / "_uploaded" / "images" / "Pump-77" / "primary" / "inspect.jpg"
    assert cached.exists()
    assert cached.read_bytes() == image_bytes


def test_audit_export_json(client):
    # Seed a couple of events
    client.post("/api/approval", json={
        "equipment_id": "Pump-44", "artifact": "Work Order",
        "action": "承認", "comment": "test", "risk_level": "Critical",
    })
    r = client.get("/api/runs/Pump-44/export?format=json")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    body = json.loads(r.content.decode("utf-8"))
    assert any(e.get("doc_type") == "approval" for e in body)


def test_audit_export_csv_has_bom_and_columns(client):
    client.post("/api/approval", json={
        "equipment_id": "Pump-44", "artifact": "Work Order",
        "action": "却下", "comment": "csv test", "risk_level": "Warning",
    })
    r = client.get("/api/runs/Pump-44/export?format=csv")
    assert r.status_code == 200
    text = r.content.decode("utf-8-sig")
    header = text.splitlines()[0]
    assert "timestamp" in header
    assert "action" in header
    assert "却下" in text


def test_audit_export_filter_by_doc_type(client):
    client.post("/api/approval", json={
        "equipment_id": "Pump-55", "artifact": "Work Order",
        "action": "承認", "comment": "", "risk_level": "Warning",
    })
    r = client.get("/api/runs/Pump-55/export?format=json&doc_type=approval")
    body = json.loads(r.content.decode("utf-8"))
    assert all(e.get("doc_type") == "approval" for e in body)


def test_search_stats_reports_backend(client):
    r = client.get("/api/search/stats")
    assert r.status_code == 200
    data = r.json()
    assert "active_backend" in data
    assert data["azure_search_configured"] is False


def test_search_seed_no_op_when_not_configured(client):
    r = client.post("/api/search/seed-from-local")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "not_configured"
    assert data["uploaded"] == 0


def test_health_includes_ai_search_flag(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert "ai_search_configured" in r.json()


def test_sse_stream_first_event_shape(client, tmp_side_files):
    """The SSE generator is an infinite async iterator, so we drive it
    directly with anyio and read the first emission. This avoids tying up
    a real HTTP connection that has no clean termination signal in tests."""
    import anyio
    from backend.main import spresense_stream

    iot_ingest.send_events([
        {"equipment_id": "Pump-03", "timestamp": 1.0 + i*0.001,
         "vibration_x": 0.01, "vibration_y": 0.01, "vibration_z": 0.5,
         "sound_level": 58, "temperature": 52, "current": 2.6}
        for i in range(200)
    ])

    async def grab_first():
        resp = await spresense_stream(equipment_id="Pump-03", poll_seconds=0.5)
        gen = resp.body_iterator
        async for chunk in gen:
            if b"data:" in chunk:
                return chunk
        return b""

    chunk = anyio.run(grab_first)
    line = next(l for l in chunk.decode("utf-8").splitlines() if l.startswith("data:"))
    payload = json.loads(line.removeprefix("data:").strip())
    assert payload["record_count"] == 200
    assert payload["risk_level"] == "Critical"
    assert payload["primary_concern"]


def test_request_id_header_round_trips(client):
    r = client.get("/api/health", headers={"x-request-id": "test-rid-1234"})
    assert r.status_code == 200
    assert r.headers["x-request-id"] == "test-rid-1234"
    assert "x-elapsed-ms" in r.headers


def test_error_envelope_shape(client):
    r = client.post("/api/analyze", json={"preset_key": "nonexistent"})
    assert r.status_code == 404
    body = r.json()
    assert "error" in body
    assert body["error"]["code"] == "http_404"


# ──────────────────────────────────────────────────────────────────────
# Multi-equipment endpoints
# ──────────────────────────────────────────────────────────────────────

def test_list_equipment_returns_full_catalog(client):
    r = client.get("/api/equipment")
    assert r.status_code == 200
    ids = {e["id"] for e in r.json()["equipment"]}
    assert ids == {"Pump-03", "Pump-01", "Motor-02", "Fan-04", "Compressor-05"}


def test_equipment_metadata_includes_normal_state(client):
    r = client.get("/api/equipment")
    motor = next(e for e in r.json()["equipment"] if e["id"] == "Motor-02")
    assert motor["kind"] == "motor"
    assert motor["normal_state"]["current_a"] > 3.0
    assert motor["downstream"]


def test_equipment_endpoint_includes_picker_visuals(client):
    """The custom EquipmentPicker (sidebar + Next.js) needs an icon and
    a per-kind accent colour from the server — make sure they're exposed."""
    r = client.get("/api/equipment")
    for eq in r.json()["equipment"]:
        assert eq["kind_icon"], f"missing icon for {eq['id']}"
        assert eq["kind_accent"].startswith("#"), f"bad accent for {eq['id']}"


def test_equipment_snapshot_normal(client):
    r = client.get("/api/equipment/Fan-04/snapshot?intensity=normal")
    assert r.status_code == 200
    data = r.json()
    assert data["risk_level"] == "Normal"
    assert data["equipment_id"] == "Fan-04"


def test_equipment_snapshot_critical(client):
    r = client.get("/api/equipment/Compressor-05/snapshot?intensity=critical")
    assert r.status_code == 200
    assert r.json()["risk_level"] == "Critical"


def test_equipment_snapshot_unknown_returns_404(client):
    r = client.get("/api/equipment/Unknown-99/snapshot")
    assert r.status_code == 404


def test_equipment_snapshot_bad_intensity_400(client):
    r = client.get("/api/equipment/Pump-03/snapshot?intensity=weird")
    assert r.status_code == 400


def test_presets_include_intensity_field(client):
    r = client.get("/api/presets")
    presets = r.json()["presets"]
    motor_critical = next(p for p in presets if p["key"] == "Motor-02:critical")
    assert motor_critical["equipment_id"] == "Motor-02"
    assert motor_critical["intensity"] == "critical"


def test_analyze_with_motor_critical_preset(client):
    r = client.post("/api/analyze", json={"preset_key": "Motor-02:critical"})
    assert r.status_code == 200
    data = r.json()
    assert data["equipment_id"] == "Motor-02"
    assert data["risk"]["risk_level"] == "Critical"


def test_analyze_with_compressor_normal_preset(client):
    """A compressor at 'normal' intensity must stay Normal — proves the
    per-equipment threshold override is wired through ``/api/analyze``."""
    r = client.post("/api/analyze", json={"preset_key": "Compressor-05:normal"})
    assert r.status_code == 200
    assert r.json()["risk"]["risk_level"] == "Normal"
