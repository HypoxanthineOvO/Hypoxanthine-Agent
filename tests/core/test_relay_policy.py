from __future__ import annotations

import asyncio

import pytest

from hypo_agent.core.channel_dispatcher import ChannelDispatcher, ChannelRelayPolicy
from hypo_agent.core.delivery import DeliveryResult
from hypo_agent.core.unified_message import TextBlock, UnifiedMessage
from hypo_agent.models import Message


class RecordingLogger:
    def __init__(self) -> None:
        self.info_calls: list[tuple[str, dict]] = []
        self.warning_calls: list[tuple[str, dict]] = []
        self.error_calls: list[tuple[str, dict]] = []

    def info(self, event: str, **kwargs) -> None:
        self.info_calls.append((event, kwargs))

    def warning(self, event: str, **kwargs) -> None:
        self.warning_calls.append((event, kwargs))

    def error(self, event: str, **kwargs) -> None:
        self.error_calls.append((event, kwargs))

    def exception(self, event: str, **kwargs) -> None:
        self.error_calls.append((event, kwargs))


def test_relay_policy_injects_source_prefix_for_cross_channel_user_messages() -> None:
    async def _run() -> None:
        dispatcher = ChannelDispatcher()
        policy = ChannelRelayPolicy(dispatcher)
        delivered: list[UnifiedMessage] = []

        async def qq_sink(message: UnifiedMessage) -> None:
            delivered.append(message)

        dispatcher.register("qq", qq_sink, platform="qq", is_external=True)

        await policy.relay_message(
            Message(
                text="你好",
                sender="user",
                session_id="main",
                channel="weixin",
                sender_id="wx-user",
            ),
            message_type="user_message",
            origin_channel="weixin",
        )

        assert len(delivered) == 1
        first_block = delivered[0].blocks[0]
        assert isinstance(first_block, TextBlock)
        assert first_block.text.startswith("[微信] ")
        assert delivered[0].raw_text == "[微信] 你好"

    asyncio.run(_run())


def test_relay_policy_excludes_origin_platform_for_user_messages() -> None:
    async def _run() -> None:
        dispatcher = ChannelDispatcher()
        policy = ChannelRelayPolicy(dispatcher)
        qq_received: list[UnifiedMessage] = []
        webui_received: list[Message] = []

        async def qq_sink(message: UnifiedMessage) -> None:
            qq_received.append(message)

        async def webui_sink(message: Message, *, exclude_client_ids=None) -> None:
            del exclude_client_ids
            webui_received.append(message)

        dispatcher.register("qq", qq_sink, platform="qq", is_external=True)
        dispatcher.register("webui", webui_sink, platform="webui", is_external=False)

        await policy.relay_message(
            Message(
                text="来自 QQ",
                sender="user",
                session_id="main",
                channel="qq",
                sender_id="qq-user",
            ),
            message_type="user_message",
            origin_channel="qq",
        )

        assert qq_received == []
        assert len(webui_received) == 1
        assert webui_received[0].text == "来自 QQ"
        assert webui_received[0].channel == "qq"

    asyncio.run(_run())


def test_relay_policy_preserves_feishu_source_for_webui_without_text_prefix() -> None:
    async def _run() -> None:
        dispatcher = ChannelDispatcher()
        policy = ChannelRelayPolicy(dispatcher)
        webui_received: list[Message] = []

        async def webui_sink(message: Message, *, exclude_client_ids=None) -> None:
            del exclude_client_ids
            webui_received.append(message)

        dispatcher.register("webui", webui_sink, platform="webui", is_external=False)

        await policy.relay_message(
            Message(
                text="同步一下",
                sender="user",
                session_id="main",
                channel="feishu",
                sender_id="ou_user_1",
            ),
            message_type="user_message",
            origin_channel="feishu",
        )

        assert len(webui_received) == 1
        assert webui_received[0].text == "同步一下"
        assert webui_received[0].channel == "feishu"

    asyncio.run(_run())


def test_relay_policy_injects_feishu_source_prefix_for_cross_channel_user_messages() -> None:
    async def _run() -> None:
        dispatcher = ChannelDispatcher()
        policy = ChannelRelayPolicy(dispatcher)
        delivered: list[UnifiedMessage] = []

        async def qq_sink(message: UnifiedMessage) -> None:
            delivered.append(message)

        dispatcher.register("qq", qq_sink, platform="qq", is_external=True)

        await policy.relay_message(
            Message(
                text="同步一下",
                sender="user",
                session_id="main",
                channel="feishu",
                sender_id="ou_user_1",
            ),
            message_type="user_message",
            origin_channel="feishu",
        )

        assert len(delivered) == 1
        first_block = delivered[0].blocks[0]
        assert isinstance(first_block, TextBlock)
        assert first_block.text.startswith("[飞书] ")
        assert delivered[0].raw_text == "[飞书] 同步一下"

    asyncio.run(_run())


def test_relay_policy_filters_non_main_sessions_from_external_channels() -> None:
    async def _run() -> None:
        dispatcher = ChannelDispatcher()
        policy = ChannelRelayPolicy(dispatcher)
        qq_received: list[UnifiedMessage] = []
        webui_received: list[Message] = []

        async def qq_sink(message: UnifiedMessage) -> None:
            qq_received.append(message)

        async def webui_sink(message: Message, *, exclude_client_ids=None) -> None:
            del exclude_client_ids
            webui_received.append(message)

        dispatcher.register("qq", qq_sink, platform="qq", is_external=True)
        dispatcher.register("webui", webui_sink, platform="webui", is_external=False)

        await policy.relay_message(
            Message(
                text="debug only",
                sender="user",
                session_id="debug-session",
                channel="webui",
            ),
            message_type="user_message",
            origin_channel="webui",
            origin_client_id="client-1",
        )

        assert qq_received == []
        assert len(webui_received) == 1
        assert webui_received[0].text == "debug only"

    asyncio.run(_run())


def test_relay_policy_filters_non_main_sessions_from_feishu_like_other_external_channels() -> None:
    async def _run() -> None:
        dispatcher = ChannelDispatcher()
        policy = ChannelRelayPolicy(dispatcher)
        feishu_received: list[UnifiedMessage] = []
        qq_received: list[UnifiedMessage] = []

        async def feishu_sink(message: UnifiedMessage) -> None:
            feishu_received.append(message)

        async def qq_sink(message: UnifiedMessage) -> None:
            qq_received.append(message)

        dispatcher.register("feishu", feishu_sink, platform="feishu", is_external=True)
        dispatcher.register("qq", qq_sink, platform="qq", is_external=True)

        await policy.relay_message(
            Message(
                text="提醒：会议开始",
                sender="assistant",
                session_id="feishu_oc_chat_123",
                channel="system",
            ),
            message_type="ai_reply",
            origin_channel="system",
        )

        assert feishu_received == []
        assert qq_received == []

    asyncio.run(_run())


def test_relay_policy_passes_excluded_webui_client_id() -> None:
    async def _run() -> None:
        dispatcher = ChannelDispatcher()
        policy = ChannelRelayPolicy(dispatcher)
        excluded: list[set[str] | None] = []

        async def webui_sink(message: Message, *, exclude_client_ids=None) -> None:
            del message
            excluded.append(set(exclude_client_ids or set()) or None)

        dispatcher.register("webui", webui_sink, platform="webui", is_external=False)

        await policy.relay_message(
            Message(
                text="hello",
                sender="user",
                session_id="main",
                channel="webui",
                metadata={"webui_client_id": "client-1"},
            ),
            message_type="user_message",
            origin_channel="webui",
            origin_client_id="client-1",
        )

        assert excluded == [{"client-1"}]

    asyncio.run(_run())


def test_relay_policy_deduplicates_by_source_message_id() -> None:
    async def _run() -> None:
        dispatcher = ChannelDispatcher()
        policy = ChannelRelayPolicy(dispatcher)
        delivered: list[UnifiedMessage] = []

        async def qq_sink(message: UnifiedMessage) -> None:
            delivered.append(message)

        dispatcher.register("qq", qq_sink, platform="qq", is_external=True)
        message = Message(
            text="same message",
            sender="assistant",
            session_id="main",
            channel="system",
            metadata={"msg_id": "dup-1"},
        )

        await policy.relay_message(message, message_type="ai_reply", origin_channel="system")
        await policy.relay_message(message, message_type="ai_reply", origin_channel="system")

        assert len(delivered) == 1

    asyncio.run(_run())


def test_relay_policy_logs_aggregated_delivery_results(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _run() -> None:
        dispatcher = ChannelDispatcher()
        policy = ChannelRelayPolicy(dispatcher)
        logger = RecordingLogger()
        monkeypatch.setattr("hypo_agent.core.channel_dispatcher.logger", logger)

        async def qq_sink(message: UnifiedMessage) -> DeliveryResult:
            del message
            return DeliveryResult.ok("qq_bot", segment_count=3)

        async def weixin_sink(message: UnifiedMessage) -> DeliveryResult:
            del message
            return DeliveryResult.failed(
                "weixin",
                segment_count=3,
                failed_segments=2,
                error="iLink send timeout after 10s",
            )

        dispatcher.register("qq", qq_sink, platform="qq", is_external=True)
        dispatcher.register("weixin", weixin_sink, platform="weixin", is_external=True)

        await policy.relay_message(
            Message(
                text="hello",
                sender="assistant",
                session_id="main",
                channel="webui",
                metadata={"msg_id": "abc123"},
            ),
            message_type="ai_reply",
            origin_channel="webui",
        )

        assert logger.warning_calls[-1][0] == "channel_relay.broadcast"
        payload = logger.warning_calls[-1][1]
        assert payload["msg_id"] == "abc123"
        assert payload["source"] == "webui"
        assert payload["targets"] == 2
        assert "→ qq_bot: ✅ 3/3 segments" in payload["summary"]
        assert "→ weixin: ❌ 1/3 segments (error: iLink send timeout after 10s)" in payload["summary"]

    asyncio.run(_run())
