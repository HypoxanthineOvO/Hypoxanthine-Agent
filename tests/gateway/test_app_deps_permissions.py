from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from hypo_agent.gateway.app import AppDeps, _build_default_deps, create_app
from hypo_agent.memory.session import SessionMemory
from hypo_agent.memory.structured_store import StructuredStore
from hypo_agent.models import SecurityConfig


class PassivePipeline:
    async def stream_reply(self, inbound):
        del inbound
        if False:  # pragma: no cover
            yield {}


def test_build_default_deps_injects_permission_manager_into_skills(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "skills.yaml").write_text(
        """
default_timeout_seconds: 30
skills:
  tmux:
    enabled: false
  code_run:
    enabled: true
  filesystem:
    enabled: true
  reminder:
    enabled: true
  email_scanner:
    enabled: true
""".strip(),
        encoding="utf-8",
    )

    security = SecurityConfig.model_validate(
        {
            "directory_whitelist": {
                "rules": [
                    {
                        "path": str(tmp_path),
                        "permissions": ["read", "write", "execute"],
                    }
                ],
                "default_policy": "readonly",
            },
            "circuit_breaker": {},
        }
    )

    monkeypatch.chdir(tmp_path)
    deps = _build_default_deps(security)

    assert deps.permission_manager is not None
    assert deps.skill_manager is not None
    assert deps.skill_manager._permission_manager is deps.permission_manager
    assert deps.skill_manager._structured_store is deps.structured_store
    assert deps.skill_manager._skills["code_run"].permission_manager is deps.permission_manager
    assert deps.skill_manager._skills["filesystem"].permission_manager is deps.permission_manager
    assert deps.skill_manager._skills["reminder"].structured_store is deps.structured_store
    assert deps.skill_manager._skills["reminder"].scheduler is deps.scheduler
    assert deps.skill_manager._skills["reminder"].auto_confirm is True
    assert deps.skill_manager._skills["email_scanner"].structured_store is deps.structured_store
    assert deps.event_queue is not None
    assert deps.scheduler is not None
    assert getattr(deps.scheduler, "_email_scan_executor", None) is not None


def test_create_app_exposes_output_compressor_from_deps(tmp_path: Path) -> None:
    class DummyPipeline:
        async def stream_reply(self, inbound):
            del inbound
            if False:  # pragma: no cover
                yield {}

    deps = AppDeps(
        session_memory=SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20),
        structured_store=StructuredStore(db_path=tmp_path / "hypo.db"),
        output_compressor=object(),
    )
    app = create_app(auth_token="test-token", pipeline=DummyPipeline(), deps=deps)
    assert app.state.output_compressor is deps.output_compressor
    assert app.state.event_queue is deps.event_queue
    assert app.state.scheduler is deps.scheduler


def test_reload_config_keeps_pipeline_proactive_callback(tmp_path: Path, monkeypatch) -> None:
    class DummyPipeline:
        def __init__(self) -> None:
            self.started = 0
            self.stopped = 0

        async def start_event_consumer(self) -> None:
            self.started += 1

        async def stop_event_consumer(self) -> None:
            self.stopped += 1

        async def stream_reply(self, inbound):
            del inbound
            if False:  # pragma: no cover
                yield {}

    class ReloadedPipeline:
        def __init__(self) -> None:
            self.started = 0
            self.stopped = 0

        async def start_event_consumer(self) -> None:
            self.started += 1

        async def stop_event_consumer(self) -> None:
            self.stopped += 1

        async def stream_reply(self, inbound):
            del inbound
            if False:  # pragma: no cover
                yield {}

    deps = AppDeps(
        session_memory=SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20),
        structured_store=StructuredStore(db_path=tmp_path / "hypo.db"),
    )
    app = create_app(auth_token="test-token", pipeline=DummyPipeline(), deps=deps)

    security = SecurityConfig.model_validate(
        {
            "directory_whitelist": {"rules": [], "default_policy": "readonly"},
            "circuit_breaker": {},
        }
    )
    monkeypatch.setattr(
        "hypo_agent.gateway.app.load_gateway_settings",
        lambda: SimpleNamespace(auth_token="reloaded-token", security=security),
    )
    monkeypatch.setattr(
        "hypo_agent.gateway.app._build_default_pipeline",
        lambda deps: ReloadedPipeline(),
    )
    monkeypatch.setattr(
        "hypo_agent.gateway.app._register_enabled_skills",
        lambda **kwargs: None,
    )

    previous_pipeline = app.state.pipeline
    asyncio.run(app.state.reload_config())
    assert getattr(app.state.pipeline, "on_proactive_message", None) is not None
    assert previous_pipeline.stopped == 1
    assert app.state.pipeline.started == 1


def _write_qq_config(tmp_path: Path, *, enabled: bool) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "skills.yaml").write_text(
        f"""
default_timeout_seconds: 30
skills:
  qq:
    enabled: {"true" if enabled else "false"}
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


def test_create_app_registers_qq_channel_only_when_enabled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_qq_config(tmp_path, enabled=True)
    monkeypatch.chdir(tmp_path)

    enabled_app = create_app(
        auth_token="test-token",
        pipeline=PassivePipeline(),
        deps=AppDeps(
            session_memory=SessionMemory(sessions_dir=tmp_path / "sessions-enabled", buffer_limit=20),
            structured_store=StructuredStore(db_path=tmp_path / "hypo-enabled.db"),
        ),
    )

    assert enabled_app.state.qq_channel_service is not None
    assert "qq" in enabled_app.state.channel_dispatcher.channels

    _write_qq_config(tmp_path, enabled=False)
    disabled_app = create_app(
        auth_token="test-token",
        pipeline=PassivePipeline(),
        deps=AppDeps(
            session_memory=SessionMemory(sessions_dir=tmp_path / "sessions-disabled", buffer_limit=20),
            structured_store=StructuredStore(db_path=tmp_path / "hypo-disabled.db"),
        ),
    )

    assert disabled_app.state.qq_channel_service is None
    assert "qq" not in disabled_app.state.channel_dispatcher.channels
