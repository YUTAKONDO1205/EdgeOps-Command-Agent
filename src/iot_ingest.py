"""
Spresense / Event Hubs ingestion.

Spresense ボード（または任意のエッジデバイス）から AMQP / HTTPS で
Event Hubs に送られた点検データを受け取り、既存の
``signal_analysis.analyze()`` パイプラインに流し込めるよう整形します。

設計
----
- 送信側ペイロードは下記の JSON 1 件 = 1 サンプル想定:
    {
      "device_id": "spresense-01",
      "equipment_id": "Pump-03",
      "timestamp": 1716700000.123,         # epoch seconds
      "vibration_x": 0.123,
      "vibration_y": 0.045,
      "vibration_z": 0.789,
      "sound_level": 53.2,
      "temperature": 46.7,
      "current": 2.3
    }
- 受信側はオンライン取得は重いので、``fetch_recent()`` で最新 N 件を
  バッチで取得して pandas.DataFrame に成形します。
- Event Hubs が未設定なら、``data/spresense_simulator.py`` で生成した
  ローカル JSONL を読み込みます（ハードウェア不在のデモ運用）。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from .utils import PROJECT_ROOT, load_env


LOCAL_STREAM_JSONL = PROJECT_ROOT / "_spresense_stream.jsonl"


@dataclass
class EventHubConfig:
    connection_string: str | None
    name: str | None
    consumer_group: str

    @classmethod
    def from_env(cls) -> "EventHubConfig":
        load_env()
        return cls(
            connection_string=os.getenv("EVENT_HUB_CONNECTION_STRING") or None,
            name=os.getenv("EVENT_HUB_NAME") or None,
            consumer_group=os.getenv("EVENT_HUB_CONSUMER_GROUP", "$Default"),
        )

    def is_configured(self) -> bool:
        return bool(self.connection_string and self.name)


def is_configured() -> bool:
    return EventHubConfig.from_env().is_configured()


def active_source() -> str:
    return "event_hubs" if is_configured() else ("local_jsonl" if LOCAL_STREAM_JSONL.exists() else "none")


# ───────────────────────────────────────────────────────────────────────
# Local JSONL fallback — reads what the simulator writes
# ───────────────────────────────────────────────────────────────────────

def _read_local_jsonl(limit: int = 5000) -> list[dict[str, Any]]:
    if not LOCAL_STREAM_JSONL.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in LOCAL_STREAM_JSONL.read_text(encoding="utf-8").splitlines()[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


# ───────────────────────────────────────────────────────────────────────
# Event Hubs receiver
# ───────────────────────────────────────────────────────────────────────

def _read_event_hubs(*, max_events: int, timeout_seconds: float) -> list[dict[str, Any]]:
    cfg = EventHubConfig.from_env()
    if not cfg.is_configured():
        return []
    try:
        from azure.eventhub import EventHubConsumerClient
    except ImportError:
        return []

    received: list[dict[str, Any]] = []

    def on_event(partition_context, event):
        if event is None:
            return
        try:
            body = event.body_as_str(encoding="UTF-8")
            doc = json.loads(body)
            if isinstance(doc, dict):
                received.append(doc)
        except Exception:
            pass
        finally:
            if len(received) >= max_events:
                # Stop receiving by closing inside the callback
                partition_context.update_checkpoint(event)

    client = EventHubConsumerClient.from_connection_string(
        conn_str=cfg.connection_string,
        consumer_group=cfg.consumer_group,
        eventhub_name=cfg.name,
    )
    try:
        # Read from "earliest" so the demo always sees something on a fresh hub.
        with client:
            from threading import Thread, Event
            stop = Event()

            def receiver():
                try:
                    client.receive(
                        on_event=on_event,
                        starting_position="-1",  # = beginning of stream
                        max_wait_time=timeout_seconds,
                    )
                except Exception:
                    pass
                finally:
                    stop.set()

            t = Thread(target=receiver, daemon=True)
            t.start()
            t.join(timeout=timeout_seconds + 1)
    except Exception:
        return received
    return received


# ───────────────────────────────────────────────────────────────────────
# Public surface
# ───────────────────────────────────────────────────────────────────────

@dataclass
class FetchResult:
    df: pd.DataFrame
    source: str            # "event_hubs" | "local_jsonl" | "empty"
    record_count: int
    equipment_id: str | None


def _records_to_frame(records: list[dict[str, Any]], *, equipment_id_filter: str | None) -> pd.DataFrame:
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame.from_records(records)
    if equipment_id_filter and "equipment_id" in df.columns:
        df = df[df["equipment_id"] == equipment_id_filter]
    if df.empty:
        return df
    # Normalize timestamp to seconds-from-start so the signal analysis works.
    if "timestamp" in df.columns:
        df = df.sort_values("timestamp").reset_index(drop=True)
        t0 = float(df["timestamp"].iloc[0])
        df["timestamp"] = df["timestamp"].astype(float) - t0
    else:
        df["timestamp"] = (df.index / 1000.0).astype(float)
    # Ensure required columns exist (gracefully fill with zeros)
    for col in ("vibration_x", "vibration_y", "vibration_z",
                "sound_level", "temperature", "current"):
        if col not in df.columns:
            df[col] = 0.0
    return df


def fetch_recent(
    *,
    equipment_id: str | None = None,
    max_events: int = 4000,
    timeout_seconds: float = 6.0,
) -> FetchResult:
    """Return the most recent ingestion frame. Picks the configured backend
    automatically (Event Hubs → local JSONL → empty)."""
    if is_configured():
        records = _read_event_hubs(max_events=max_events, timeout_seconds=timeout_seconds)
        if records:
            df = _records_to_frame(records, equipment_id_filter=equipment_id)
            return FetchResult(df=df, source="event_hubs", record_count=len(df), equipment_id=equipment_id)
    records = _read_local_jsonl(limit=max_events)
    df = _records_to_frame(records, equipment_id_filter=equipment_id)
    return FetchResult(
        df=df,
        source="local_jsonl" if not df.empty else "empty",
        record_count=len(df),
        equipment_id=equipment_id,
    )


def send_events(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Producer-side helper — used by the Spresense simulator. Sends to Event
    Hubs when configured, otherwise appends to the local JSONL stream."""
    cfg = EventHubConfig.from_env()
    payload = list(events)
    if not payload:
        return {"sent": 0, "backend": "noop"}
    if cfg.is_configured():
        try:
            from azure.eventhub import EventHubProducerClient, EventData
            client = EventHubProducerClient.from_connection_string(
                conn_str=cfg.connection_string, eventhub_name=cfg.name)
            with client:
                batch = client.create_batch()
                for ev in payload:
                    batch.add(EventData(json.dumps(ev, ensure_ascii=False)))
                client.send_batch(batch)
            return {"sent": len(payload), "backend": "event_hubs"}
        except Exception as exc:
            # Hard-fail to local stream
            with LOCAL_STREAM_JSONL.open("a", encoding="utf-8") as f:
                for ev in payload:
                    f.write(json.dumps(ev, ensure_ascii=False) + "\n")
            return {"sent": len(payload), "backend": "local_jsonl", "fallback_reason": str(exc)}
    LOCAL_STREAM_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with LOCAL_STREAM_JSONL.open("a", encoding="utf-8") as f:
        for ev in payload:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    return {"sent": len(payload), "backend": "local_jsonl"}


def reset_local_stream() -> int:
    """Wipe the local JSONL stream. Used by the UI's reset button."""
    if not LOCAL_STREAM_JSONL.exists():
        return 0
    n = sum(1 for _ in LOCAL_STREAM_JSONL.open("r", encoding="utf-8"))
    LOCAL_STREAM_JSONL.unlink()
    return n
