from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.fixtures import write_config_tree
from tests.shared import (
    FakeIMAPClient,
    FakeQQBotWSClient,
    FakeQQWSClient,
    FakeWeixinChannel,
    NoopScheduler,
    PassivePipeline,
    RecordingExternalSink,
)


@pytest.fixture
def fixed_timestamp() -> datetime:
    return datetime(2026, 3, 3, 10, 0, 0, tzinfo=UTC)


@pytest.fixture
def fake_pipeline() -> PassivePipeline:
    """Shared no-op pipeline for API and gateway tests that must not call real models."""

    return PassivePipeline()


@pytest.fixture
def fake_external_sinks() -> dict[str, RecordingExternalSink]:
    """Fake outbound sinks that record deliveries and guarantee no external messages are sent."""

    return {
        "qq": RecordingExternalSink("qq"),
        "weixin": RecordingExternalSink("weixin"),
        "feishu": RecordingExternalSink("feishu"),
        "email": RecordingExternalSink("email"),
    }


@pytest.fixture
def fake_channel_clients(monkeypatch, request) -> dict[str, object]:
    """Fake Weixin/QQ/IMAP clients and monkeypatches that disable outbound network traffic."""

    from hypo_agent.channels.weixin.weixin_channel import WeixinChannel
    from hypo_agent.gateway.qq_ws_client import NapCatWebSocketClient
    from hypo_agent.gateway.qqbot_ws_client import QQBotWebSocketClient
    from hypo_agent.skills import email_scanner_skill

    created_weixin: list[FakeWeixinChannel] = []
    created_napcat: list[FakeQQWSClient] = []
    created_qqbot: list[FakeQQBotWSClient] = []
    created_imap: list[FakeIMAPClient] = []

    def weixin_factory(**kwargs) -> FakeWeixinChannel:
        channel = FakeWeixinChannel(**kwargs)
        created_weixin.append(channel)
        return channel

    def napcat_factory(**kwargs) -> FakeQQWSClient:
        client = FakeQQWSClient(**kwargs)
        created_napcat.append(client)
        return client

    def qqbot_factory(**kwargs) -> FakeQQBotWSClient:
        client = FakeQQBotWSClient(**kwargs)
        created_qqbot.append(client)
        return client

    def imap_factory(host: str, port: int = 993) -> FakeIMAPClient:
        client = FakeIMAPClient(host, port)
        created_imap.append(client)
        return client

    async def fake_weixin_start(self) -> None:
        if getattr(self, "client", None) is None:
            client_factory = getattr(self, "client_factory", None)
            self.client = client_factory() if callable(client_factory) else SimpleNamespace(
                bot_token="fake-weixin-token",
                user_id="",
                bot_id="fake-weixin-bot",
            )
        self._running = True

    async def fake_weixin_stop(self) -> None:
        self._running = False

    async def fake_napcat_start(self) -> None:
        self.status = "connected"

    async def fake_napcat_stop(self) -> None:
        self.status = "disconnected"

    async def fake_qqbot_start(self) -> None:
        self.status = "connected"
        self.ws_connected = True

    async def fake_qqbot_stop(self) -> None:
        self.status = "disconnected"
        self.ws_connected = False

    path = str(Path(str(request.node.fspath)).as_posix())
    if any(
        segment in path
        for segment in ("/tests/gateway/", "/tests/integration/", "/tests/scripts/")
    ):
        monkeypatch.setattr(email_scanner_skill.imaplib, "IMAP4_SSL", imap_factory)
        monkeypatch.setattr(WeixinChannel, "start", fake_weixin_start)
        monkeypatch.setattr(WeixinChannel, "stop", fake_weixin_stop)
        monkeypatch.setattr(NapCatWebSocketClient, "start", fake_napcat_start)
        monkeypatch.setattr(NapCatWebSocketClient, "stop", fake_napcat_stop)
        monkeypatch.setattr(QQBotWebSocketClient, "start", fake_qqbot_start)
        monkeypatch.setattr(QQBotWebSocketClient, "stop", fake_qqbot_stop)

    return {
        "weixin": created_weixin,
        "napcat": created_napcat,
        "qqbot": created_qqbot,
        "imap": created_imap,
        "weixin_factory": weixin_factory,
        "napcat_factory": napcat_factory,
        "qqbot_factory": qqbot_factory,
        "imap_factory": imap_factory,
    }


@pytest.fixture(autouse=True)
def _disable_real_external_channels(fake_channel_clients) -> None:
    """Autouse safety net so no test can open outbound Weixin/QQ/IMAP sockets by accident."""

    del fake_channel_clients


@pytest.fixture
def app_factory(tmp_path: Path, fake_channel_clients):
    """Create a gateway app wired with fake channel clients and sandboxed storage for integration tests."""

    from hypo_agent.core.event_queue import EventQueue
    from hypo_agent.gateway.app import AppDeps, AppTestOverrides, create_app
    from hypo_agent.gateway.settings import load_channel_settings
    from hypo_agent.memory.session import SessionMemory
    from hypo_agent.memory.structured_store import StructuredStore

    counter = {"value": 0}

    def factory(
        *,
        pipeline=None,
        deps=None,
        auth_token: str = "test-token",
        config_overrides: dict | None = None,
        channels=None,
        disable_external_channels: bool = True,
        skip_email_cache_warmup: bool = True,
    ):
        counter["value"] += 1
        root = tmp_path / f"app-{counter['value']}"
        config_dir = root / "config"
        write_config_tree(config_dir, overrides=config_overrides)

        resolved_deps = deps or AppDeps(
            session_memory=SessionMemory(sessions_dir=root / "sessions", buffer_limit=20),
            structured_store=StructuredStore(db_path=root / "hypo.db"),
            event_queue=EventQueue(),
            scheduler=NoopScheduler(),
        )
        resolved_channels = channels or load_channel_settings(config_dir / "config.yaml")
        resolved_pipeline = pipeline or PassivePipeline()
        overrides = AppTestOverrides(
            disable_external_channels=disable_external_channels,
            weixin_channel_factory=fake_channel_clients["weixin_factory"],
            napcat_ws_client_factory=fake_channel_clients["napcat_factory"],
            qqbot_ws_client_factory=fake_channel_clients["qqbot_factory"],
            skip_email_cache_warmup=skip_email_cache_warmup,
        )
        app = create_app(
            auth_token=auth_token,
            pipeline=resolved_pipeline,
            deps=resolved_deps,
            channels=resolved_channels,
            test_overrides=overrides,
        )
        app.state.config_dir = config_dir
        return app

    return factory


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    integration_files = {
        "tests/channels/test_coder_webhook.py",
        "tests/channels/test_probe_server.py",
    }
    slow_files = {
        "tests/scripts/test_start_sh.py",
    }

    for item in items:
        path = str(Path(str(item.fspath)).as_posix())
        if path.endswith(tuple(slow_files)):
            item.add_marker(pytest.mark.slow)
        if "/tests/scripts/" in path:
            item.add_marker(pytest.mark.cli)
            continue
        if "/tests/gateway/" in path or "/tests/integration/" in path or path.endswith(tuple(integration_files)):
            item.add_marker(pytest.mark.integration)
            continue
        item.add_marker(pytest.mark.unit)
