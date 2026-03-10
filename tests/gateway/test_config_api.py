from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from hypo_agent.gateway.app import AppDeps, create_app
from hypo_agent.memory.session import SessionMemory
from hypo_agent.memory.structured_store import StructuredStore


class DummyPipeline:
    async def stream_reply(self, inbound):
        del inbound
        if False:  # pragma: no cover
            yield {}


def _seed_config_dir(config_dir: Path) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "models.yaml").write_text(
        """
default_model: Gemini3Pro
task_routing:
  chat: Gemini3Pro
models:
  Gemini3Pro:
    provider: Hiapi
    litellm_model: openai/gemini-2.5-pro
    fallback: null
""".strip(),
        encoding="utf-8",
    )
    (config_dir / "skills.yaml").write_text(
        """
default_timeout_seconds: 30
skills:
  tmux:
    enabled: true
""".strip(),
        encoding="utf-8",
    )
    (config_dir / "security.yaml").write_text(
        """
auth_token: test-token
directory_whitelist:
  rules: []
  default_policy: readonly
circuit_breaker: {}
""".strip(),
        encoding="utf-8",
    )
    (config_dir / "persona.yaml").write_text(
        """
name: Hypo
aliases: []
personality: []
speaking_style: {}
""".strip(),
        encoding="utf-8",
    )
    (config_dir / "tasks.yaml").write_text(
        """
scheduler:
  tick_interval_seconds: 10
""".strip(),
        encoding="utf-8",
    )


def _build_client(tmp_path: Path) -> TestClient:
    deps = AppDeps(
        session_memory=SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20),
        structured_store=StructuredStore(db_path=tmp_path / "hypo.db"),
    )
    app = create_app(auth_token="test-token", pipeline=DummyPipeline(), deps=deps)
    config_dir = tmp_path / "config"
    _seed_config_dir(config_dir)
    app.state.config_dir = config_dir
    app.state.deps.reload_config = lambda: None
    app.state.reload_config = lambda: None
    return TestClient(app)


def test_config_files_require_token(tmp_path) -> None:
    client = _build_client(tmp_path)
    with client:
        response = client.get("/api/config/files")
    assert response.status_code == 401


def test_config_files_and_get_endpoints(tmp_path) -> None:
    client = _build_client(tmp_path)
    with client:
        files_response = client.get("/api/config/files", params={"token": "test-token"})
        get_response = client.get(
            "/api/config/models.yaml",
            params={"token": "test-token"},
        )

    assert files_response.status_code == 200
    files_payload = files_response.json()
    names = [item["filename"] for item in files_payload["files"]]
    assert names == [
        "models.yaml",
        "skills.yaml",
        "security.yaml",
        "persona.yaml",
        "tasks.yaml",
    ]
    assert get_response.status_code == 200
    assert "default_model" in get_response.json()["content"]


def test_config_put_validates_yaml_before_write(tmp_path) -> None:
    client = _build_client(tmp_path)
    with client:
        response = client.put(
            "/api/config/models.yaml",
            params={"token": "test-token"},
            json={"content": "default_model: [bad"},
        )
    assert response.status_code == 422


def test_config_put_updates_content_when_valid(tmp_path) -> None:
    client = _build_client(tmp_path)
    with client:
        response = client.put(
            "/api/config/skills.yaml",
            params={"token": "test-token"},
            json={
                "content": """
default_timeout_seconds: 45
skills:
  tmux:
    enabled: false
""".strip()
            },
        )
        read_back = client.get(
            "/api/config/skills.yaml",
            params={"token": "test-token"},
        )

    assert response.status_code == 200
    assert response.json()["reloaded"] is True
    assert read_back.status_code == 200
    assert "default_timeout_seconds: 45" in read_back.json()["content"]
