from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from hypo_agent.gateway.app import AppDeps, create_app
from hypo_agent.memory.session import SessionMemory
from hypo_agent.memory.structured_store import StructuredStore
from hypo_agent.models import DirectoryWhitelist
from hypo_agent.security.permission_manager import PermissionManager


class DummyPipeline:
    async def stream_reply(self, inbound):
        del inbound
        if False:  # pragma: no cover
            yield {}


def _build_client(tmp_path: Path) -> TestClient:
    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir(parents=True, exist_ok=True)

    deps = AppDeps(
        session_memory=SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20),
        structured_store=StructuredStore(db_path=tmp_path / "hypo.db"),
        permission_manager=PermissionManager(
            DirectoryWhitelist.model_validate(
                {
                    "rules": [
                        {
                            "path": str(allowed_dir),
                            "permissions": ["read"],
                        }
                    ],
                    "default_policy": "readonly",
                }
            )
        ),
    )
    app = create_app(auth_token="test-token", pipeline=DummyPipeline(), deps=deps)
    return TestClient(app)


def test_files_api_requires_token(tmp_path) -> None:
    client = _build_client(tmp_path)

    with client:
        response = client.get("/api/files", params={"path": str(tmp_path / "allowed" / "a.txt")})

    assert response.status_code == 401


def test_files_api_denies_non_whitelisted_path(tmp_path) -> None:
    denied_file = tmp_path / "denied.txt"
    denied_file.write_text("secret", encoding="utf-8")

    client = _build_client(tmp_path)
    with client:
        response = client.get(
            "/api/files",
            params={"path": str(denied_file), "token": "test-token"},
        )

    assert response.status_code == 403


def test_files_api_returns_404_for_missing_file(tmp_path) -> None:
    client = _build_client(tmp_path)
    missing_path = tmp_path / "allowed" / "missing.txt"

    with client:
        response = client.get(
            "/api/files",
            params={"path": str(missing_path), "token": "test-token"},
        )

    assert response.status_code == 404


def test_files_api_serves_file_content_when_authorized(tmp_path) -> None:
    file_path = tmp_path / "allowed" / "hello.txt"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text("hello-world", encoding="utf-8")

    client = _build_client(tmp_path)
    with client:
        response = client.get(
            "/api/files",
            params={"path": str(file_path)},
            headers={"Authorization": "Bearer test-token"},
        )

    assert response.status_code == 200
    assert response.text == "hello-world"
