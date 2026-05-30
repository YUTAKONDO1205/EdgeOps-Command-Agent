"""
FastAPI backend for EdgeOps Command Agent.

Mirrors the Streamlit UI flow, exposing the Multi-Agent pipeline as a
JSON API. The Next.js frontend under ``frontend/`` calls these endpoints.

Endpoints
---------
GET  /api/health                       — liveness + active config summary
GET  /api/presets                      — list demo presets
POST /api/analyze                      — run the full pipeline on a preset
POST /api/analyze/with-uploads         — analyze using uploaded CSV/image/memo
POST /api/upload/pdf                   — ingest a PDF manual into RAG
POST /api/upload/image                 — upload an inspection image to Blob
POST /api/teams/notify                 — send a Teams alert
POST /api/approval                     — record an approval decision
GET  /api/runs                         — recent run history (Cosmos)
GET  /api/runs/{equipment_id}          — recent runs for one equipment
GET  /api/runs/{equipment_id}/export   — audit-log export as JSON or CSV
POST /api/spresense/ingest             — Spresense / Event Hubs sample push
GET  /api/spresense/recent             — fetch the latest ingested frame
GET  /api/spresense/stream             — SSE: live risk updates as samples arrive
POST /api/search/seed-from-local       — promote local manual to Azure AI Search
GET  /api/search/stats                 — count + active backend

Run locally
-----------
    uvicorn backend.main:app --reload --port 8000
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator

# Make `src` importable when launched from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

import pandas as pd

from src import (
    agents,
    ai_search,
    blob_store,
    cosmos_store,
    equipment_catalog,
    iot_ingest,
    rag,
    raw_ingest,
    report_generator,
    risk_engine,
    signal_analysis,
    teams_notify,
    utils,
)


# ───────────────────────────────────────────────────────────────────────
# Logging — structured-ish, single-line per record
# ───────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=os.getenv("EDGEOPS_LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s req=%(request_id)s — %(message)s",
)
logger = logging.getLogger("edgeops.backend")


class _RequestIdFilter(logging.Filter):
    """Ensure every record carries a ``request_id`` field even when the call
    site didn't pass one. Using a Filter instead of a LogRecord factory keeps
    us compatible with Python 3.14, where passing ``request_id`` again via
    ``extra=`` would otherwise raise "Attempt to overwrite" — the factory and
    the middleware can both want to set it."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = "-"
        return True


# Attach the filter to the root handler so every record routed through the
# basicConfig formatter (httpx, asyncio, etc.) carries the field. Otherwise
# third-party loggers raise "Formatting field not found in record: 'request_id'".
_request_id_filter = _RequestIdFilter()
for _h in logging.getLogger().handlers:
    _h.addFilter(_request_id_filter)
# Also attach to the named loggers we own, in case extra handlers are added.
for _name in ("edgeops.backend", "uvicorn", "uvicorn.access"):
    logging.getLogger(_name).addFilter(_request_id_filter)


app = FastAPI(
    title="EdgeOps Command Agent API",
    version="1.1.0",
    description="Multi-Agent maintenance assistant — backend service.",
)

_allowed = os.getenv("EDGEOPS_CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _allowed if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_logging(request: Request, call_next):
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:8]
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:
        # See note on the success path below for why we don't pass request_id
        # via extra=.
        logger.exception("[%s] unhandled error: %s", rid, exc)
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "internal_error", "message": str(exc)[:300], "request_id": rid}},
        )
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    response.headers["x-request-id"] = rid
    response.headers["x-elapsed-ms"] = str(elapsed_ms)
    # Embed the request id in the message rather than via extra=, because the
    # _RequestIdFilter already populates the record attribute. Some Python
    # versions (3.14+) raise "Attempt to overwrite 'request_id' in LogRecord"
    # when both paths try to set it.
    logger.info(
        "[%s] %s %s -> %s in %dms",
        rid, request.method, request.url.path, response.status_code, elapsed_ms,
    )
    return response


@app.exception_handler(HTTPException)
async def http_exc_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": f"http_{exc.status_code}", "message": exc.detail,
                            "request_id": request.headers.get("x-request-id", "-")}},
    )


# ───────────────────────────────────────────────────────────────────────
# Request / response models
# ───────────────────────────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    preset_key: str
    equipment_id: str | None = None


class TeamsNotifyRequest(BaseModel):
    equipment_id: str
    risk_level: str
    health_score: int
    primary_concern: str
    deadline_hours: int | None = None
    body_lines: list[str] | None = None
    work_order_url: str | None = None


class ApprovalRequest(BaseModel):
    equipment_id: str
    artifact: str          # "Work Order" | "Management Report"
    action: str            # "承認" | "修正依頼" | "却下"
    comment: str = ""
    risk_level: str | None = None


# ───────────────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────────────

def _history_summary(equipment_id: str) -> str:
    try:
        hist = pd.read_csv(utils.DATA_DIR / "failure_history.csv")
        same = hist[hist["equipment_id"] == equipment_id].tail(5)
        if same.empty:
            return "（同一設備の故障履歴なし）"
        lines = []
        for _, row in same.iterrows():
            lines.append(
                f"- {row['date']}: {row['risk_level']} / {row['detected_symptoms']} → "
                f"{row['root_cause']} → {row['action_taken']}（{row['downtime_hours']}h停止）"
            )
        return "\n".join(lines)
    except Exception as exc:  # pragma: no cover
        return f"（履歴読込エラー: {exc}）"


def _inventory_summary() -> str:
    try:
        inv = pd.read_csv(utils.DATA_DIR / "parts_inventory.csv")
        lines = [
            f"- {row['part_id']} {row['name']}: 在庫{row['stock']}（リードタイム{row['lead_time_days']}日）"
            for _, row in inv.head(6).iterrows()
        ]
        return "\n".join(lines)
    except Exception as exc:  # pragma: no cover
        return f"（在庫読込エラー: {exc}）"


def _pipeline_to_payload(pipeline: agents.PipelineResult, equipment_id: str) -> dict[str, Any]:
    agents_payload: dict[str, Any] = {
        "signal": {"source": pipeline.signal.source, "output": pipeline.signal.output},
        "vision": {"source": pipeline.vision.source, "output": pipeline.vision.output},
        "manual_rag": {"source": pipeline.manual_rag.source, "output": pipeline.manual_rag.output},
        "root_cause": {"source": pipeline.root_cause.source, "output": pipeline.root_cause.output},
        "action_plan": {"source": pipeline.action_plan.source, "output": pipeline.action_plan.output},
        "whatif": {"source": pipeline.whatif.source, "output": pipeline.whatif.output},
    }
    if pipeline.intake is not None:
        agents_payload["intake"] = {"source": pipeline.intake.source,
                                     "output": pipeline.intake.output}
    if pipeline.governance is not None:
        agents_payload["governance"] = {"source": pipeline.governance.source,
                                         "output": pipeline.governance.output}
    return {
        "risk": pipeline.risk.to_dict(),
        "agents": agents_payload,
        "work_order_md": report_generator.render_work_order(pipeline, equipment_id=equipment_id),
        "management_report_md": report_generator.render_management_report(pipeline, equipment_id=equipment_id),
    }


def _persist_run(pipeline: agents.PipelineResult, equipment_id: str) -> None:
    try:
        cosmos_store.record_run(
            equipment_id,
            risk_level=pipeline.risk.risk_level,
            health_score=pipeline.risk.health_score,
            primary_concern=pipeline.risk.primary_concern,
            summary=(pipeline.signal.output.get("summary") if isinstance(pipeline.signal.output, dict) else "") or "",
            action_plan=pipeline.action_plan.output if isinstance(pipeline.action_plan.output, dict) else None,
            root_cause=pipeline.root_cause.output if isinstance(pipeline.root_cause.output, dict) else None,
        )
    except Exception as exc:
        logger.warning("persist_run failed: %s", exc)


# ───────────────────────────────────────────────────────────────────────
# Routes — meta
# ───────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "mock_mode": utils.use_mock_mode(),
        "rag_backend": rag.active_backend(),
        "blob_configured": blob_store.is_configured(),
        "cosmos_configured": cosmos_store.is_configured(),
        "teams_configured": teams_notify.is_configured(),
        "ai_search_configured": ai_search.is_configured(),
        "spresense_source": iot_ingest.active_source(),
    }


@app.get("/api/presets")
def list_presets() -> dict[str, Any]:
    return {
        "presets": [
            {"key": p.key, "label": p.label, "equipment_id": p.equipment_id,
             "intensity": p.intensity, "memo": p.inspection_memo}
            for p in utils.DEMO_PRESETS.values()
        ]
    }


@app.get("/api/equipment")
def list_equipment() -> dict[str, Any]:
    """Equipment catalog (every monitored asset, not just Pump-03)."""
    return {
        "equipment": [
            {
                "id": eq.id,
                "label": eq.label,
                "kind": eq.kind,
                "kind_icon": eq.kind_icon,
                "kind_accent": eq.kind_accent,
                "location": eq.location,
                "description": eq.description,
                "rotation_hz": eq.rotation_hz,
                "downstream": list(eq.downstream),
                "normal_state": {
                    "vibration_amp": eq.normal_vib_amp,
                    "sound_db": eq.normal_sound_db,
                    "temperature_c": eq.normal_temp_c,
                    "current_a": eq.normal_current_a,
                },
            }
            for eq in equipment_catalog.list_equipment()
        ]
    }


@app.get("/api/equipment/{equipment_id}/snapshot")
def equipment_snapshot(equipment_id: str, intensity: str = "normal") -> dict[str, Any]:
    """Cheap risk snapshot for the dashboard cards — no LLM, just sensor +
    rule-engine. Used by the Next.js fleet view to populate non-active equipment."""
    try:
        equipment_catalog.get(equipment_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown equipment_id: {equipment_id}")
    if intensity not in equipment_catalog.INTENSITY_PROFILES:
        raise HTTPException(status_code=400, detail=f"Unknown intensity: {intensity}")
    df = equipment_catalog.cached_sensor_df(equipment_id, intensity)
    features = signal_analysis.analyze(df)
    risk = risk_engine.assess(features, equipment_id=equipment_id)
    return {
        "equipment_id": equipment_id,
        "intensity": intensity,
        "risk_level": risk.risk_level,
        "health_score": risk.health_score,
        "primary_concern": risk.primary_concern,
        "ambiguity_flag": risk.ambiguity_flag,
    }


# ───────────────────────────────────────────────────────────────────────
# Routes — analysis
# ───────────────────────────────────────────────────────────────────────

@app.post("/api/analyze")
def analyze(req: AnalyzeRequest) -> dict[str, Any]:
    preset = utils.DEMO_PRESETS.get(req.preset_key)
    if preset is None:
        raise HTTPException(status_code=404, detail=f"Unknown preset: {req.preset_key}")
    equipment_id = req.equipment_id or preset.equipment_id
    df = preset.sensor_loader()
    features = signal_analysis.analyze(df)
    risk = risk_engine.assess(features, equipment_id=equipment_id)
    # Reference photo (catalog "normal" state) — gives the Vision Agent a
    # baseline for `comparison_to_normal`. Skip if we're already analysing
    # the normal preset itself.
    reference_path: Path | None = None
    try:
        if preset.intensity != "normal":
            spec = equipment_catalog.get(equipment_id)
            ref = spec.image_paths.get("normal")
            if ref and Path(ref).exists():
                reference_path = Path(ref)
    except Exception:
        pass

    pipeline = agents.run_pipeline(
        features=features,
        risk=risk,
        image_path=preset.image_path,
        inspection_memo=preset.inspection_memo,
        history_summary=_history_summary(equipment_id),
        inventory_summary=_inventory_summary(),
        equipment_id=equipment_id,
        reference_image_path=reference_path,
    )
    payload = _pipeline_to_payload(pipeline, equipment_id)
    _persist_run(pipeline, equipment_id)
    payload["equipment_id"] = equipment_id
    payload["preset_key"] = preset.key
    return payload


@app.post("/api/analyze/with-uploads")
async def analyze_with_uploads(
    equipment_id: str = Form(...),
    inspection_memo: str = Form(""),
    sample_rate_hz: float | None = Form(None),
    sensor_csv: UploadFile | None = File(None),
    image: UploadFile | None = File(None),
    extra_images: list[UploadFile] | None = File(None),
    reference_image: UploadFile | None = File(None),
) -> dict[str, Any]:
    """Run the pipeline against caller-uploaded data instead of a preset.

    Either ``sensor_csv`` must be supplied, or the request is rejected.
    ``image`` is the primary inspection shot. ``extra_images`` is an optional
    list of detail / multi-angle photos (the Vision Agent gets all of them).
    ``reference_image`` is the "normal-state" baseline — when supplied, the
    Vision Agent fills in ``comparison_to_normal``.

    All images are persisted to Blob storage and cached locally so the
    Vision Agent always has a real ``Path`` to read.
    """
    if sensor_csv is None:
        raise HTTPException(status_code=400, detail="sensor_csv file is required")
    csv_bytes = await sensor_csv.read()
    try:
        df = pd.read_csv(io.BytesIO(csv_bytes))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"sensor_csv parse error: {exc}") from exc

    async def _persist_image(upload: UploadFile, subdir: str) -> Path:
        img_bytes = await upload.read()
        blob_store.upload_bytes(
            img_bytes, blob_name=f"images/{equipment_id}/{subdir}/{upload.filename}",
            content_type=upload.content_type or "image/jpeg",
        )
        cache = blob_store.LOCAL_FALLBACK_DIR / f"images/{equipment_id}/{subdir}/{upload.filename}"
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(img_bytes)
        return cache

    image_path: Path | None = None
    if image is not None and image.filename:
        image_path = await _persist_image(image, "primary")

    extra_paths: list[Path] = []
    for extra in (extra_images or []):
        if extra and extra.filename:
            extra_paths.append(await _persist_image(extra, "extra"))

    reference_path: Path | None = None
    if reference_image is not None and reference_image.filename:
        reference_path = await _persist_image(reference_image, "reference")

    # Normalise arbitrary real-world CSVs (non-canonical column names / sample
    # rate) into the canonical schema, auto-detecting channels and inferring fs
    # from a timestamp when present. Canonical demo CSVs round-trip unchanged.
    ingested = raw_ingest.ingest(df, sample_rate_hz=sample_rate_hz)
    features = signal_analysis.analyze(ingested.canonical_df, fs=ingested.sample_rate_hz)
    risk = risk_engine.assess(features, equipment_id=equipment_id)
    pipeline = agents.run_pipeline(
        features=features,
        risk=risk,
        image_path=image_path,
        inspection_memo=inspection_memo,
        history_summary=_history_summary(equipment_id),
        inventory_summary=_inventory_summary(),
        equipment_id=equipment_id,
        extra_image_paths=extra_paths or None,
        reference_image_path=reference_path,
    )
    payload = _pipeline_to_payload(pipeline, equipment_id)
    _persist_run(pipeline, equipment_id)
    payload["equipment_id"] = equipment_id
    payload["preset_key"] = "uploaded"
    return payload


# ───────────────────────────────────────────────────────────────────────
# Routes — uploads
# ───────────────────────────────────────────────────────────────────────

@app.post("/api/upload/pdf")
async def upload_pdf(file: UploadFile = File(...)) -> dict[str, Any]:
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only .pdf files are supported.")
    data = await file.read()
    blob_res = blob_store.upload_bytes(
        data, blob_name=f"manuals/{file.filename}", content_type="application/pdf")
    rag_res = rag.ingest_pdf_bytes(data, source_name=file.filename)
    return {"blob": blob_res.__dict__, "rag": rag_res}


@app.post("/api/upload/image")
async def upload_image(file: UploadFile = File(...),
                       equipment_id: str = Form("Pump-03")) -> dict[str, Any]:
    data = await file.read()
    blob_res = blob_store.upload_bytes(
        data, blob_name=f"images/{equipment_id}/{file.filename}",
        content_type=file.content_type or "image/jpeg")
    return blob_res.__dict__


# ───────────────────────────────────────────────────────────────────────
# Routes — Teams + approvals + audit log
# ───────────────────────────────────────────────────────────────────────

@app.post("/api/teams/notify")
def teams_send(req: TeamsNotifyRequest) -> dict[str, Any]:
    result = teams_notify.notify_alert(
        equipment_id=req.equipment_id,
        risk_level=req.risk_level,
        health_score=req.health_score,
        primary_concern=req.primary_concern,
        deadline_hours=req.deadline_hours,
        body_lines=req.body_lines,
        work_order_url=req.work_order_url,
    )
    try:
        cosmos_store.record_alert(
            req.equipment_id, risk_level=req.risk_level,
            channel="teams", ok=result.ok, detail=result.detail)
    except Exception as exc:
        logger.warning("alert persist failed: %s", exc)
    return result.__dict__


@app.post("/api/approval")
def submit_approval(req: ApprovalRequest) -> dict[str, Any]:
    doc = cosmos_store.record_approval(
        req.equipment_id, artifact=req.artifact, action=req.action,
        comment=req.comment, risk_level=req.risk_level)
    return {"saved": True, "doc": doc}


@app.get("/api/runs")
def recent_runs(limit: int = 10) -> dict[str, Any]:
    return {"runs": cosmos_store.latest_runs_across_equipment(limit=limit)}


@app.get("/api/runs/{equipment_id}")
def recent_runs_for_equipment(equipment_id: str, limit: int = 20) -> dict[str, Any]:
    return {"runs": cosmos_store.recent_for_equipment(
        equipment_id, doc_types=["run", "approval", "alert"], limit=limit)}


@app.get("/api/runs/{equipment_id}/export")
def export_audit_log(
    equipment_id: str,
    format: str = Query("json", pattern="^(json|csv)$"),
    doc_type: str | None = Query(None, pattern="^(run|approval|alert)$"),
    limit: int = 1000,
) -> StreamingResponse:
    """Audit-log export. Used by both the Streamlit and Next.js download buttons."""
    types = [doc_type] if doc_type else ["run", "approval", "alert"]
    events = cosmos_store.recent_for_equipment(equipment_id, doc_types=types, limit=limit)
    if format == "json":
        body = json.dumps(events, ensure_ascii=False, indent=2).encode("utf-8")
        return StreamingResponse(
            io.BytesIO(body),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="audit_{equipment_id}.json"'},
        )
    # CSV
    columns = [
        "timestamp", "doc_type", "equipment_id", "risk_level",
        "artifact", "action", "comment", "channel", "ok", "detail",
        "primary_concern", "health_score", "summary", "id",
    ]
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for e in events:
        writer.writerow({k: e.get(k, "") for k in columns})
    body = out.getvalue().encode("utf-8-sig")  # BOM for Excel-on-Windows friendliness
    return StreamingResponse(
        io.BytesIO(body),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="audit_{equipment_id}.csv"'},
    )


# ───────────────────────────────────────────────────────────────────────
# Routes — Azure AI Search
# ───────────────────────────────────────────────────────────────────────

@app.post("/api/search/seed-from-local")
def seed_search_index() -> dict[str, Any]:
    return rag.seed_azure_search_from_local_manual()


@app.get("/api/search/stats")
def search_stats() -> dict[str, Any]:
    return {
        "active_backend": rag.active_backend(),
        "uploaded_chunk_count": rag.uploaded_chunk_count(),
        "azure_search_configured": ai_search.is_configured(),
        "azure_doc_count": ai_search.count_docs() if ai_search.is_configured() else 0,
    }


# ───────────────────────────────────────────────────────────────────────
# Routes — Spresense / IoT bridge
# ───────────────────────────────────────────────────────────────────────

@app.post("/api/spresense/ingest")
def spresense_ingest(events: list[dict[str, Any]]) -> dict[str, Any]:
    return iot_ingest.send_events(events)


@app.get("/api/spresense/recent")
def spresense_recent(equipment_id: str | None = None, max_events: int = 4000) -> dict[str, Any]:
    res = iot_ingest.fetch_recent(equipment_id=equipment_id, max_events=max_events)
    payload: dict[str, Any] = {
        "source": res.source,
        "record_count": res.record_count,
        "equipment_id": res.equipment_id,
    }
    if not res.df.empty:
        features = signal_analysis.analyze(res.df)
        risk = risk_engine.assess(features, equipment_id=equipment_id)
        payload["features"] = features.to_dict()
        payload["risk"] = risk.to_dict()
    return payload


@app.get("/api/spresense/stream")
async def spresense_stream(
    equipment_id: str | None = None,
    poll_seconds: float = 2.0,
) -> StreamingResponse:
    """Server-Sent Events stream — re-fetches the latest Spresense frame on
    a cadence, re-runs signal+risk, and emits a JSON event per tick. Used by
    the Next.js "Live" tab and any operations dashboard."""
    poll_seconds = max(0.5, min(poll_seconds, 30.0))

    async def gen() -> AsyncIterator[bytes]:
        last_record_count = -1
        while True:
            res = iot_ingest.fetch_recent(equipment_id=equipment_id, max_events=4000)
            event: dict[str, Any] = {
                "ts": time.time(),
                "source": res.source,
                "record_count": res.record_count,
            }
            if not res.df.empty:
                features = signal_analysis.analyze(res.df)
                risk = risk_engine.assess(features, equipment_id=equipment_id)
                event["risk_level"] = risk.risk_level
                event["health_score"] = risk.health_score
                event["primary_concern"] = risk.primary_concern
                event["vibration_rms"] = features.vibration_rms
                event["temperature_max_c"] = features.temperature_max_c
                event["sound_max_db"] = features.sound_max_db
            # Always emit on the first tick, then only when something changed
            if event["record_count"] != last_record_count:
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8")
                last_record_count = event["record_count"]
            else:
                # heartbeat so the client knows we're alive
                yield b": keep-alive\n\n"
            await asyncio.sleep(poll_seconds)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
