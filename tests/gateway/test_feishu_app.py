from __future__ import annotations

from pathlib import Path

from hypo_agent.gateway.app import AppDeps, create_app
from hypo_agent.gateway.settings import ChannelsConfig, FeishuChannelSettings
from hypo_agent.memory.session import SessionMemory
from hypo_agent.memory.structured_store import StructuredStore
from tests.fixtures import write_config_tree
from tests.shared import PassivePipeline


def _write_feishu_config(tmp_path: Path, *, enabled: bool) -> None:
    write_config_tree(
        tmp_path / "config",
        overrides={
            "secrets": {
                "services": {
                    "feishu": {
                        "app_id": "cli_test",
                        "app_secret": "secret_test",
                    }
                }
            },
            "config": {
                "channels": {
                    "feishu": {
                        "enabled": enabled,
                    }
                }
            },
        },
    )


def test_create_app_registers_feishu_channel_when_enabled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_feishu_config(tmp_path, enabled=True)
    monkeypatch.chdir(tmp_path)

    app = create_app(
        auth_token="test-token",
        pipeline=PassivePipeline(),
        deps=AppDeps(
            session_memory=SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20),
            structured_store=StructuredStore(db_path=tmp_path / "hypo.db"),
        ),
        channels=ChannelsConfig(
            feishu=FeishuChannelSettings(enabled=True),
        ),
    )

    assert app.state.feishu_channel is not None
    assert "feishu" in app.state.channel_dispatcher.channels


def test_create_app_skips_feishu_channel_when_disabled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_feishu_config(tmp_path, enabled=False)
    monkeypatch.chdir(tmp_path)

    app = create_app(
        auth_token="test-token",
        pipeline=PassivePipeline(),
        deps=AppDeps(
            session_memory=SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20),
            structured_store=StructuredStore(db_path=tmp_path / "hypo.db"),
        ),
        channels=ChannelsConfig(
            feishu=FeishuChannelSettings(enabled=False),
        ),
    )

    assert app.state.feishu_channel is None
    assert "feishu" not in app.state.channel_dispatcher.channels
