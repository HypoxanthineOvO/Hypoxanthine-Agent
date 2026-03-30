from __future__ import annotations

from pathlib import Path

from hypo_agent.gateway.app import AppDeps, create_app
from hypo_agent.gateway.settings import ChannelsConfig, FeishuChannelSettings
from hypo_agent.memory.session import SessionMemory
from hypo_agent.memory.structured_store import StructuredStore


class PassivePipeline:
    async def stream_reply(self, inbound):
        del inbound
        if False:  # pragma: no cover
            yield {}


def _write_feishu_config(tmp_path: Path, *, enabled: bool) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "secrets.yaml").write_text(
        """
providers: {}
services:
  feishu:
    app_id: "cli_test"
    app_secret: "secret_test"
""".strip(),
        encoding="utf-8",
    )
    (config_dir / "config.yaml").write_text(
        f"""
channels:
  feishu:
    enabled: {"true" if enabled else "false"}
""".strip(),
        encoding="utf-8",
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
