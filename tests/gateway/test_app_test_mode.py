from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hypo_agent.core.event_queue import EventQueue
from hypo_agent.core.pipeline import ChatPipeline
from hypo_agent.gateway.app import AppDeps, create_app
from hypo_agent.memory.session import SessionMemory
from hypo_agent.memory.structured_store import StructuredStore


class RecordingLogger:
    def __init__(self) -> None:
        self.info_calls: list[tuple[str, dict]] = []
        self.warning_calls: list[tuple[str, dict]] = []

    def info(self, event: str, **kwargs) -> None:
        self.info_calls.append((event, kwargs))

    def warning(self, event: str, **kwargs) -> None:
        self.warning_calls.append((event, kwargs))

    def exception(self, event: str, **kwargs) -> None:  # pragma: no cover - defensive
        self.info_calls.append((event, kwargs))


class NoopScheduler:
    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


class NoopRouter:
    async def call(self, model_name, messages, *, session_id=None, tools=None):
        del model_name, messages, session_id, tools
        return "ok"

    async def call_with_tools(self, model_name, messages, *, tools=None, session_id=None):
        del model_name, messages, tools, session_id
        return {"text": "ok", "tool_calls": []}

    async def stream(self, model_name, messages, *, session_id=None, tools=None):
        del model_name, messages, session_id, tools
        yield "ok"


def _test_mode_deps() -> tuple[AppDeps, ChatPipeline]:
    queue = EventQueue()
    session_memory = SessionMemory(buffer_limit=20)
    structured_store = StructuredStore()
    pipeline = ChatPipeline(
        router=NoopRouter(),
        chat_model="Gemini3Pro",
        session_memory=session_memory,
        structured_store=structured_store,
        event_queue=queue,
    )
    deps = AppDeps(
        session_memory=session_memory,
        structured_store=structured_store,
        event_queue=queue,
        scheduler=NoopScheduler(),
    )
    return deps, pipeline


def _write_enabled_qq_config(config_dir: Path) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "skills.yaml").write_text(
        """
default_timeout_seconds: 30
skills:
  qq:
    enabled: true
""".strip(),
        encoding="utf-8",
    )
    (config_dir / "secrets.yaml").write_text(
        """
providers: {}
services:
  qq:
    napcat_ws_url: ws://127.0.0.1:6099
    napcat_http_url: http://127.0.0.1:3000
    bot_qq: "123456789"
    allowed_users:
      - "10001"
""".strip(),
        encoding="utf-8",
    )


def test_create_app_in_test_mode_skips_qq_registration(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HYPO_TEST_MODE", "1")
    _write_enabled_qq_config(tmp_path / "config")
    deps, pipeline = _test_mode_deps()
    recorder = RecordingLogger()
    monkeypatch.setattr("hypo_agent.gateway.app.logger", recorder)

    app = create_app(
        auth_token="test-token",
        pipeline=pipeline,
        deps=deps,
    )

    assert app.state.qq_channel_service is None
    assert "qq" not in app.state.channel_dispatcher.channels
    assert any(event == "qq_adapter.skip" for event, _ in recorder.info_calls)
    assert any(
        event == "test_mode.enabled"
        and "test/sandbox" in str(kwargs.get("banner") or "")
        for event, kwargs in recorder.warning_calls
    )


def test_test_mode_websocket_writes_session_and_db_to_sandbox(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HYPO_TEST_MODE", "1")
    deps, pipeline = _test_mode_deps()
    app = create_app(
        auth_token="test-token",
        pipeline=pipeline,
        deps=deps,
    )

    sandbox_root = tmp_path / "test" / "sandbox"
    sandbox_session_file = sandbox_root / "memory" / "sessions" / "main.jsonl"
    sandbox_db_path = sandbox_root / "hypo.db"
    production_session_file = tmp_path / "memory" / "sessions" / "main.jsonl"
    production_db_path = tmp_path / "hypo.db"

    with TestClient(app) as client:
        assert app.state.knowledge_dir == sandbox_root / "memory" / "knowledge"
        assert app.state.knowledge_dir.exists()
        with client.websocket_connect("/ws?token=test-token") as ws:
            ws.send_json({"text": "你好", "sender": "user", "session_id": "main"})
            first = ws.receive_json()
            while first["type"] == "pipeline_stage":
                first = ws.receive_json()
            second = ws.receive_json()

    assert first["type"] == "assistant_chunk"
    assert second["type"] == "assistant_done"
    assert sandbox_session_file.exists()
    assert sandbox_db_path.exists()
    assert not production_session_file.exists()
    assert not production_db_path.exists()


def test_create_app_in_test_mode_rejects_non_sandbox_storage(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HYPO_TEST_MODE", "1")
    queue = EventQueue()
    deps = AppDeps(
        session_memory=SessionMemory(
            sessions_dir=tmp_path / "memory" / "sessions",
            buffer_limit=20,
        ),
        structured_store=StructuredStore(db_path=tmp_path / "memory" / "hypo.db"),
        event_queue=queue,
        scheduler=NoopScheduler(),
    )
    pipeline = ChatPipeline(
        router=NoopRouter(),
        chat_model="Gemini3Pro",
        session_memory=deps.session_memory,
        structured_store=deps.structured_store,
        event_queue=queue,
    )

    with pytest.raises(RuntimeError, match="HYPO_TEST_MODE requires sandbox-isolated storage"):
        create_app(
            auth_token="test-token",
            pipeline=pipeline,
            deps=deps,
        )
