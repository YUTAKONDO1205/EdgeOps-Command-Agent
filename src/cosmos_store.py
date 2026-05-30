"""
Azure Cosmos DB（SQL API）への監査ログ・解析履歴の永続化。

ストアする3種類のドキュメント
----------------------------
1. ``approval``   — 承認/修正依頼/却下 のイベント
2. ``run``        — Multi-Agent 実行の結果サマリー（リスク、原因仮説、対応期限）
3. ``alert``      — Teams 通知の送信履歴

Cosmos が未設定なら、すべてプロセス内メモリ + ``_local_cosmos.jsonl`` に
追記して同じ API を返します。実装：
- 1コンテナ ``events``、PK は ``/equipment_id``、id は uuid4
- ``Equipment`` ごとに最近のイベントを引く ``recent_for_equipment()``
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .utils import PROJECT_ROOT, load_env


_LOCAL_LOG = PROJECT_ROOT / "_local_cosmos.jsonl"
DEFAULT_DATABASE = "edgeops"
DEFAULT_CONTAINER = "events"
JST = timezone(timedelta(hours=9), name="JST")


@dataclass
class CosmosConfig:
    endpoint: str | None
    key: str | None
    database: str
    container: str

    @classmethod
    def from_env(cls) -> "CosmosConfig":
        load_env()
        return cls(
            endpoint=os.getenv("COSMOS_ENDPOINT") or None,
            key=os.getenv("COSMOS_KEY") or None,
            database=os.getenv("COSMOS_DATABASE", DEFAULT_DATABASE),
            container=os.getenv("COSMOS_CONTAINER", DEFAULT_CONTAINER),
        )

    def is_configured(self) -> bool:
        return bool(self.endpoint and self.key)


def is_configured() -> bool:
    return CosmosConfig.from_env().is_configured()


_CLIENT_CACHE: dict[str, Any] = {}


def _client_container(cfg: CosmosConfig):
    cache_key = f"{cfg.endpoint}/{cfg.database}/{cfg.container}"
    if cache_key in _CLIENT_CACHE:
        return _CLIENT_CACHE[cache_key]
    from azure.cosmos import CosmosClient, PartitionKey  # lazy
    client = CosmosClient(cfg.endpoint, credential=cfg.key)
    db = client.create_database_if_not_exists(id=cfg.database)
    container = db.create_container_if_not_exists(
        id=cfg.container,
        partition_key=PartitionKey(path="/equipment_id"),
    )
    _CLIENT_CACHE[cache_key] = container
    return container


def _now_iso() -> str:
    """ISO 8601 with microseconds so events recorded in the same second
    still have a stable, sortable ordering."""
    return datetime.now(JST).strftime("%Y-%m-%dT%H:%M:%S.%f%z")


def _append_local(doc: dict[str, Any]) -> None:
    _LOCAL_LOG.parent.mkdir(parents=True, exist_ok=True)
    with _LOCAL_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(doc, ensure_ascii=False) + "\n")


def _read_local() -> list[dict[str, Any]]:
    if not _LOCAL_LOG.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in _LOCAL_LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def save_event(doc_type: str, equipment_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Save one event. Adds id, timestamp, doc_type, equipment_id. Returns the
    persisted document so the caller can echo it back to the UI."""
    doc = {
        "id": str(uuid.uuid4()),
        "doc_type": doc_type,
        "equipment_id": equipment_id,
        "timestamp": _now_iso(),
        **payload,
    }
    cfg = CosmosConfig.from_env()
    if cfg.is_configured():
        try:
            container = _client_container(cfg)
            container.create_item(body=doc)
            doc["_backend"] = "cosmos"
            return doc
        except Exception as exc:
            doc["_backend"] = "local"
            doc["_cosmos_error"] = str(exc)[:200]
            _append_local(doc)
            return doc
    doc["_backend"] = "local"
    _append_local(doc)
    return doc


def recent_for_equipment(equipment_id: str, *, doc_types: list[str] | None = None, limit: int = 20) -> list[dict[str, Any]]:
    """Return latest events for the given equipment, newest first."""
    cfg = CosmosConfig.from_env()
    if cfg.is_configured():
        try:
            container = _client_container(cfg)
            doc_type_filter = ""
            params: list[dict[str, Any]] = [{"name": "@eq", "value": equipment_id}]
            if doc_types:
                placeholders = ", ".join(f"@dt{i}" for i in range(len(doc_types)))
                doc_type_filter = f" AND c.doc_type IN ({placeholders})"
                for i, dt in enumerate(doc_types):
                    params.append({"name": f"@dt{i}", "value": dt})
            query = (
                f"SELECT TOP {int(limit)} * FROM c WHERE c.equipment_id = @eq"
                f"{doc_type_filter} ORDER BY c.timestamp DESC"
            )
            items = list(container.query_items(
                query=query, parameters=params, partition_key=equipment_id))
            return items
        except Exception:
            pass
    # Local fallback
    all_docs = _read_local()
    filtered = [d for d in all_docs if d.get("equipment_id") == equipment_id]
    if doc_types:
        filtered = [d for d in filtered if d.get("doc_type") in doc_types]
    filtered.sort(key=lambda d: d.get("timestamp", ""), reverse=True)
    return filtered[:limit]


def latest_runs_across_equipment(limit: int = 10) -> list[dict[str, Any]]:
    """Cross-equipment view used by the Command Center for the past-cases panel."""
    cfg = CosmosConfig.from_env()
    if cfg.is_configured():
        try:
            container = _client_container(cfg)
            query = (
                f"SELECT TOP {int(limit)} c.id, c.equipment_id, c.timestamp, c.risk_level, "
                f"c.primary_concern, c.health_score, c.summary "
                f"FROM c WHERE c.doc_type = 'run' ORDER BY c.timestamp DESC"
            )
            return list(container.query_items(query=query, enable_cross_partition_query=True))
        except Exception:
            pass
    runs = [d for d in _read_local() if d.get("doc_type") == "run"]
    runs.sort(key=lambda d: d.get("timestamp", ""), reverse=True)
    return runs[:limit]


# ───────────────────────────────────────────────────────────────────────────
# Convenience helpers — keyed by the artifact the UI deals with
# ───────────────────────────────────────────────────────────────────────────


def record_run(equipment_id: str, *, risk_level: str, health_score: int,
               primary_concern: str, summary: str, action_plan: dict[str, Any] | None,
               root_cause: dict[str, Any] | None) -> dict[str, Any]:
    return save_event("run", equipment_id, {
        "risk_level": risk_level,
        "health_score": health_score,
        "primary_concern": primary_concern,
        "summary": summary,
        "action_plan": action_plan,
        "root_cause": root_cause,
    })


def record_approval(equipment_id: str, *, artifact: str, action: str, comment: str,
                    risk_level: str | None) -> dict[str, Any]:
    return save_event("approval", equipment_id, {
        "artifact": artifact,
        "action": action,
        "comment": comment,
        "risk_level": risk_level,
    })


def record_alert(equipment_id: str, *, risk_level: str, channel: str, ok: bool,
                 detail: str) -> dict[str, Any]:
    return save_event("alert", equipment_id, {
        "risk_level": risk_level,
        "channel": channel,
        "ok": ok,
        "detail": detail,
    })
