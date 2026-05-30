"""
Azure Blob Storage ラッパ。

写真・PDF・解析履歴 JSON を Blob に永続化するための薄いラッパ。
接続文字列が未設定の場合は、ローカルファイルシステムにフォールバックします。

設計判断
--------
- このアプリは Streamlit 単体でも動かしたいので、Blob は「あれば使う」前提。
- 接続情報なし → ``_uploaded/`` 配下に保存（既存挙動と同じ位置感）。
- 接続情報あり → ``edgeops`` コンテナに保存し、SAS URL を返す。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import BinaryIO

from .utils import PROJECT_ROOT, load_env


DEFAULT_CONTAINER = "edgeops"
LOCAL_FALLBACK_DIR = PROJECT_ROOT / "_uploaded"


@dataclass(frozen=True)
class BlobUploadResult:
    blob_name: str
    backend: str           # "azure" | "local"
    url: str | None        # SAS URL (azure) or file:// path (local)
    container: str | None
    error: str | None = None


@dataclass
class BlobConfig:
    connection_string: str | None
    container: str

    @classmethod
    def from_env(cls) -> "BlobConfig":
        load_env()
        return cls(
            connection_string=os.getenv("AZURE_STORAGE_CONNECTION_STRING") or None,
            container=os.getenv("AZURE_STORAGE_CONTAINER", DEFAULT_CONTAINER),
        )


def is_configured() -> bool:
    return bool(BlobConfig.from_env().connection_string)


def _ensure_local_dir() -> Path:
    LOCAL_FALLBACK_DIR.mkdir(parents=True, exist_ok=True)
    return LOCAL_FALLBACK_DIR


def _save_local(data: bytes, blob_name: str) -> BlobUploadResult:
    target = _ensure_local_dir() / blob_name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return BlobUploadResult(
        blob_name=blob_name,
        backend="local",
        url=str(target),
        container=None,
    )


def _build_sas_url(blob_client, container: str, blob_name: str, hours: int = 24) -> str | None:
    """Generate a read-only SAS URL valid for `hours` so the UI can preview it."""
    try:
        from azure.storage.blob import generate_blob_sas, BlobSasPermissions
        # Need the account name + key from connection string
        from azure.storage.blob._shared.base_client import parse_connection_str
        conn = BlobConfig.from_env().connection_string
        if not conn:
            return None
        parsed = parse_connection_str(conn, credential=None, service="blob")
        account_name = parsed[0].split("AccountName=")[-1].split(";")[0] if "AccountName=" in str(parsed) else None
        # Fallback: parse manually
        if not account_name:
            for part in conn.split(";"):
                if part.startswith("AccountName="):
                    account_name = part.split("=", 1)[1]
                    break
        account_key = None
        for part in conn.split(";"):
            if part.startswith("AccountKey="):
                account_key = part.split("=", 1)[1]
                break
        if not (account_name and account_key):
            return None
        sas = generate_blob_sas(
            account_name=account_name,
            container_name=container,
            blob_name=blob_name,
            account_key=account_key,
            permission=BlobSasPermissions(read=True),
            expiry=datetime.now(timezone.utc) + timedelta(hours=hours),
        )
        return f"{blob_client.url}?{sas}"
    except Exception:
        return blob_client.url  # at minimum return the blob URL (may need auth)


def upload_bytes(
    data: bytes,
    blob_name: str,
    *,
    content_type: str | None = None,
    config: BlobConfig | None = None,
) -> BlobUploadResult:
    """Upload raw bytes. Falls back to local FS if Azure is not configured."""
    cfg = config or BlobConfig.from_env()
    if not cfg.connection_string:
        return _save_local(data, blob_name)

    try:
        from azure.storage.blob import BlobServiceClient, ContentSettings
        service = BlobServiceClient.from_connection_string(cfg.connection_string)
        container = service.get_container_client(cfg.container)
        try:
            container.create_container()
        except Exception:
            pass  # already exists
        blob = container.get_blob_client(blob_name)
        kwargs = {}
        if content_type:
            kwargs["content_settings"] = ContentSettings(content_type=content_type)
        blob.upload_blob(data, overwrite=True, **kwargs)
        return BlobUploadResult(
            blob_name=blob_name,
            backend="azure",
            url=_build_sas_url(blob, cfg.container, blob_name),
            container=cfg.container,
        )
    except Exception as exc:
        # Hard-fail to local so the demo never blocks on Azure being flaky.
        result = _save_local(data, blob_name)
        return BlobUploadResult(
            blob_name=result.blob_name,
            backend="local",
            url=result.url,
            container=None,
            error=f"Azure upload failed ({exc.__class__.__name__}): {exc}",
        )


def upload_file(path: str | Path, *, blob_name: str | None = None, content_type: str | None = None) -> BlobUploadResult:
    p = Path(path)
    return upload_bytes(p.read_bytes(), blob_name or p.name, content_type=content_type)


def download_bytes(blob_name: str, *, config: BlobConfig | None = None) -> bytes | None:
    """Return blob bytes or None if not found. Reads from local fallback when
    Azure is not configured."""
    cfg = config or BlobConfig.from_env()
    if not cfg.connection_string:
        local = LOCAL_FALLBACK_DIR / blob_name
        if local.exists():
            return local.read_bytes()
        return None
    try:
        from azure.storage.blob import BlobServiceClient
        service = BlobServiceClient.from_connection_string(cfg.connection_string)
        blob = service.get_container_client(cfg.container).get_blob_client(blob_name)
        return blob.download_blob().readall()
    except Exception:
        return None
