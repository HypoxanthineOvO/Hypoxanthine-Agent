from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient
from starlette.datastructures import Headers, UploadFile

from hypo_agent.gateway.app import AppDeps, create_app
from hypo_agent.gateway.upload_api import MAX_UPLOAD_BYTES, _persist_upload
from hypo_agent.memory.session import SessionMemory
from hypo_agent.memory.structured_store import StructuredStore


class DummyPipeline:
    async def stream_reply(self, inbound):
        del inbound
        if False:  # pragma: no cover
            yield {}


def _build_client(tmp_path: Path) -> TestClient:
    app = create_app(
        auth_token="test-token",
        pipeline=DummyPipeline(),
        deps=AppDeps(
            session_memory=SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20),
            structured_store=StructuredStore(db_path=tmp_path / "hypo.db"),
        ),
    )
    return TestClient(app)


def test_upload_api_requires_token(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HYPO_MEMORY_DIR", str(tmp_path / "memory"))

    with _build_client(tmp_path) as client:
        response = client.post(
            "/api/upload",
            files={"file": ("note.txt", b"hello", "text/plain")},
        )

    assert response.status_code == 401


def test_upload_api_saves_uploaded_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HYPO_MEMORY_DIR", str(tmp_path / "memory"))

    with _build_client(tmp_path) as client:
        response = client.post(
            "/api/upload?token=test-token",
            files=[
                ("file", ("cat.png", b"fake-image", "image/png")),
                ("file", ("notes.txt", b"hello world", "text/plain")),
            ],
        )

    assert response.status_code == 200
    payload = response.json()
    attachments = payload["attachments"]
    assert len(attachments) == 2
    assert attachments[0]["filename"] == "cat.png"
    assert attachments[0]["mime_type"] == "image/png"
    assert attachments[0]["size_bytes"] == len(b"fake-image")
    assert Path(attachments[0]["url"]).exists()
    assert "memory/uploads/" in attachments[0]["url"].replace("\\", "/")
    assert Path(attachments[1]["url"]).read_bytes() == b"hello world"


def test_upload_api_rejects_oversized_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HYPO_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setattr("hypo_agent.gateway.upload_api.MAX_UPLOAD_BYTES", 4)

    with _build_client(tmp_path) as client:
        response = client.post(
            "/api/upload?token=test-token",
            files={"file": ("big.txt", b"12345", "text/plain")},
        )

    assert response.status_code == 413


def test_upload_api_accepts_2_5mb_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HYPO_MEMORY_DIR", str(tmp_path / "memory"))
    payload = b"a" * (int(2.5 * 1024 * 1024))

    with _build_client(tmp_path) as client:
        response = client.post(
            "/api/upload?token=test-token",
            files={"file": ("medium.bin", payload, "application/octet-stream")},
        )

    assert response.status_code == 200
    attachment = response.json()["attachments"][0]
    assert attachment["filename"] == "medium.bin"
    assert attachment["size_bytes"] == len(payload)


def test_upload_api_accepts_file_exactly_at_size_limit(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HYPO_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setattr("hypo_agent.gateway.upload_api.MAX_UPLOAD_BYTES", 5)

    with _build_client(tmp_path) as client:
        response = client.post(
            "/api/upload?token=test-token",
            files={"file": ("exact.bin", b"12345", "application/octet-stream")},
        )

    assert response.status_code == 200
    assert response.json()["attachments"][0]["size_bytes"] == 5


def test_upload_limit_constant_is_100mb_in_bytes() -> None:
    assert MAX_UPLOAD_BYTES == 100 * 1024 * 1024


def test_persist_upload_falls_back_to_stream_size_when_reported_size_missing(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"abcdef")

    with source.open("rb") as handle:
        upload = UploadFile(
            file=handle,
            size=None,
            filename="source.bin",
            headers=Headers({"content-type": "application/octet-stream"}),
        )
        attachment = asyncio.run(
            _persist_upload(upload, uploads_dir=tmp_path / "uploads")
        )

    assert attachment.size_bytes == 6
