from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from hypo_agent.gateway.app import AppDeps, _build_default_deps, create_app
from hypo_agent.memory.session import SessionMemory
from hypo_agent.memory.structured_store import StructuredStore
from hypo_agent.models import SecurityConfig


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
    assert deps.event_queue is not None
    assert deps.scheduler is not None


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
        async def stream_reply(self, inbound):
            del inbound
            if False:  # pragma: no cover
                yield {}

    class ReloadedPipeline:
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

    app.state.reload_config()
    assert getattr(app.state.pipeline, "on_proactive_message", None) is not None
