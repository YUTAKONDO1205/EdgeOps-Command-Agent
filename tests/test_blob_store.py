"""Tests for src/blob_store.py — only the local fallback path runs without
Azure. The Azure code path is exercised via mocks in test_integration."""
from __future__ import annotations

from src import blob_store


def test_local_fallback_round_trip(tmp_side_files):
    r = blob_store.upload_bytes(b"hello", blob_name="x/y.txt", content_type="text/plain")
    assert r.backend == "local"
    assert r.error is None
    assert r.url is not None
    assert blob_store.download_bytes("x/y.txt") == b"hello"


def test_local_fallback_overwrites_same_blob(tmp_side_files):
    blob_store.upload_bytes(b"first", blob_name="dup.txt")
    blob_store.upload_bytes(b"second", blob_name="dup.txt")
    assert blob_store.download_bytes("dup.txt") == b"second"


def test_download_missing_returns_none(tmp_side_files):
    assert blob_store.download_bytes("nope/missing.bin") is None


def test_is_configured_reflects_env(monkeypatch):
    assert blob_store.is_configured() is False
    monkeypatch.setenv("AZURE_STORAGE_CONNECTION_STRING",
                       "DefaultEndpointsProtocol=https;AccountName=x;AccountKey=y;EndpointSuffix=core.windows.net")
    assert blob_store.is_configured() is True


def test_upload_file_uses_filename_by_default(tmp_side_files, tmp_path):
    p = tmp_path / "sample.bin"
    p.write_bytes(b"\x00\x01\x02")
    r = blob_store.upload_file(p)
    assert r.blob_name == "sample.bin"
    assert blob_store.download_bytes("sample.bin") == b"\x00\x01\x02"
