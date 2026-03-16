from __future__ import annotations

from pathlib import Path

import yaml
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
  reminder:
    enabled: true
    auto_confirm: true
  qq:
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
system_prompt_template: |
  你是 Hypo。
""".strip(),
        encoding="utf-8",
    )
    (config_dir / "tasks.yaml").write_text(
        """
heartbeat:
  enabled: true
  interval_minutes: 30
email_store:
  enabled: true
  max_entries: 5000
  retention_days: 90
  warmup_hours: 168
""".strip(),
        encoding="utf-8",
    )
    (config_dir / "narration.yaml").write_text(
        """
enabled: true
model: DeepseekV3_2
tool_levels:
  heavy:
    - scan_emails
  medium:
    - write_file
debounce_seconds: 2
max_narration_length: 80
""".strip(),
        encoding="utf-8",
    )
    (config_dir / "email_rules.yaml").write_text(
        """
user_preferences: |
  只看重要邮件
rules:
  - name: keep-important
    subject_contains: 重要
    category: important
    skip_llm: true
""".strip(),
        encoding="utf-8",
    )
    (config_dir / "secrets.yaml").write_text(
        """
providers:
  Hiapi:
    api_base: https://hiapi.example/v1
    api_key: sk-live-123
services:
  email:
    accounts:
      - name: 主邮箱
        host: imap.example.com
        port: 993
        username: user@example.com
        password: email-password
        folder: INBOX
        use_ssl: true
  qq:
    napcat_ws_url: ws://127.0.0.1:3009/onebot/v11/ws
    napcat_ws_token: ws-secret
    napcat_http_url: http://127.0.0.1:3000
    napcat_http_token: http-secret
    bot_qq: "123456789"
    allowed_users:
      - "10001"
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


def test_config_list_requires_token(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    with client:
        response = client.get("/api/config")
    assert response.status_code == 401


def test_config_list_returns_metadata_and_secrets_get_is_masked(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    with client:
        list_response = client.get("/api/config", params={"token": "test-token"})
        get_response = client.get("/api/config/secrets.yaml", params={"token": "test-token"})

    assert list_response.status_code == 200
    payload = list_response.json()
    assert [item["filename"] for item in payload] == [
        "persona.yaml",
        "skills.yaml",
        "tasks.yaml",
        "narration.yaml",
        "email_rules.yaml",
        "secrets.yaml",
        "security.yaml",
    ]
    assert payload[0]["label"] == "人设配置"
    assert payload[5]["icon"] == "🔐"

    assert get_response.status_code == 200
    body = get_response.json()
    assert body["filename"] == "secrets.yaml"
    assert "masked_fields" in body
    assert "providers.Hiapi.api_key" in body["masked_fields"]
    assert "services.email.accounts[0].password" in body["masked_fields"]
    assert "services.qq.napcat_ws_token" in body["masked_fields"]
    assert body["data"]["providers"]["Hiapi"]["api_key"] == "••••••••"
    assert body["data"]["services"]["qq"]["napcat_http_token"] == "••••••••"
    assert "sk-live-123" not in body["content"]
    assert "http-secret" not in body["content"]


def test_config_put_preserves_masked_secret_values_and_updates_new_values(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    payload = {
        "providers": {
            "Hiapi": {
                "api_base": "https://hiapi.example/v2",
                "api_key": "••••••••",
            }
        },
        "services": {
            "email": {
                "accounts": [
                    {
                        "name": "主邮箱",
                        "host": "imap.example.com",
                        "port": 993,
                        "username": "user@example.com",
                        "password": "••••••••",
                        "folder": "INBOX",
                        "use_ssl": True,
                    }
                ]
            },
            "qq": {
                "napcat_ws_url": "ws://127.0.0.1:3009/onebot/v11/ws",
                "napcat_ws_token": "updated-ws-token",
                "napcat_http_url": "http://127.0.0.1:3000",
                "napcat_http_token": "••••••••",
                "bot_qq": "123456789",
                "allowed_users": ["10001"],
            },
        },
    }

    with client:
        response = client.put(
            "/api/config/secrets.yaml",
            params={"token": "test-token"},
            json={"data": payload},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["reloaded"] is True
    assert body["data"]["services"]["qq"]["napcat_ws_token"] == "••••••••"

    stored = yaml.safe_load((tmp_path / "config" / "secrets.yaml").read_text(encoding="utf-8"))
    assert stored["providers"]["Hiapi"]["api_base"] == "https://hiapi.example/v2"
    assert stored["providers"]["Hiapi"]["api_key"] == "sk-live-123"
    assert stored["services"]["email"]["accounts"][0]["password"] == "email-password"
    assert stored["services"]["qq"]["napcat_http_token"] == "http-secret"
    assert stored["services"]["qq"]["napcat_ws_token"] == "updated-ws-token"


def test_config_put_validates_yaml_before_write(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    with client:
        response = client.put(
            "/api/config/tasks.yaml",
            params={"token": "test-token"},
            json={"content": "heartbeat: [bad"},
        )
    assert response.status_code == 422


def test_config_put_validates_new_tasks_fields(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    with client:
        invalid = client.put(
            "/api/config/tasks.yaml",
            params={"token": "test-token"},
            json={
                "data": {
                    "heartbeat": {
                        "enabled": True,
                        "interval_minutes": 0,
                    },
                    "email_store": {
                        "enabled": True,
                        "max_entries": 5000,
                        "retention_days": 90,
                        "warmup_hours": 168,
                    },
                }
            },
        )
        valid = client.put(
            "/api/config/tasks.yaml",
            params={"token": "test-token"},
            json={
                "data": {
                    "heartbeat": {
                        "enabled": True,
                        "interval_minutes": 1,
                    },
                    "email_store": {
                        "enabled": True,
                        "max_entries": 5000,
                        "retention_days": 90,
                        "warmup_hours": 168,
                    },
                }
            },
        )

    assert invalid.status_code == 422
    assert valid.status_code == 200


def test_config_put_accepts_valid_narration_yaml(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    with client:
        response = client.put(
            "/api/config/narration.yaml",
            params={"token": "test-token"},
            json={
                "content": """
enabled: true
model: lightweight
tool_levels:
  heavy:
    - scan_emails
    - run_command
  medium:
    - write_file
debounce_seconds: 2
max_narration_length: 80
""".strip()
            },
        )

    assert response.status_code == 200
    assert response.json()["reloaded"] is True
