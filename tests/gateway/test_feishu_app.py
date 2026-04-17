from __future__ import annotations

import asyncio
from pathlib import Path

from hypo_agent.gateway.app import AppDeps, create_app
from hypo_agent.gateway.settings import ChannelsConfig, FeishuChannelSettings
from hypo_agent.memory.session import SessionMemory
from hypo_agent.memory.structured_store import StructuredStore
from hypo_agent.models import Message
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


def test_create_app_restores_persisted_feishu_chat_binding(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_feishu_config(tmp_path, enabled=True)
    monkeypatch.chdir(tmp_path)

    store = StructuredStore(db_path=tmp_path / "hypo.db")
    asyncio.run(store.set_preference("feishu.last_chat_id.main", "oc_chat_123"))

    app = create_app(
        auth_token="test-token",
        pipeline=PassivePipeline(),
        deps=AppDeps(
            session_memory=SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20),
            structured_store=store,
        ),
        channels=ChannelsConfig(
            feishu=FeishuChannelSettings(enabled=True),
        ),
    )

    assert app.state.feishu_channel is not None
    assert app.state.feishu_channel.resolve_chat_id("main") == "oc_chat_123"


def test_on_proactive_message_persists_feishu_chat_binding(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_feishu_config(tmp_path, enabled=False)
    monkeypatch.chdir(tmp_path)

    store = StructuredStore(db_path=tmp_path / "hypo.db")
    app = create_app(
        auth_token="test-token",
        pipeline=PassivePipeline(),
        deps=AppDeps(
            session_memory=SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20),
            structured_store=store,
        ),
        channels=ChannelsConfig(
            feishu=FeishuChannelSettings(enabled=False),
        ),
    )

    asyncio.run(
        app.state.pipeline.on_proactive_message(
            Message(
                text="同步一下",
                sender="user",
                session_id="main",
                channel="feishu",
                metadata={"feishu": {"chat_id": "oc_chat_456"}},
            ),
            message_type="user_message",
            origin_channel="feishu",
        )
    )

    assert asyncio.run(store.get_preference("feishu.last_chat_id.main")) == "oc_chat_456"


def test_create_app_emit_narration_relays_only_to_origin_channel(tmp_path: Path, monkeypatch) -> None:
    _write_feishu_config(tmp_path, enabled=False)
    monkeypatch.chdir(tmp_path)

    delivered: list[Message] = []

    async def fake_feishu_sink(message: Message) -> None:
        delivered.append(message)

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
    app.state.channel_dispatcher.register("feishu", fake_feishu_sink, platform="feishu", is_external=True)

    asyncio.run(
        app.state.emit_narration(
            {
                "type": "narration",
                "text": "我先去查一下。",
                "session_id": "main",
            },
            origin_channel="feishu",
            sender_id="ou_123",
        )
    )

    assert len(delivered) == 1
    assert delivered[0].blocks[0].text == "我先去查一下。"
    assert delivered[0].metadata["target_channels"] == ["feishu"]
    assert delivered[0].channel == "feishu"
