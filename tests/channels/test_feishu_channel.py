from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from hypo_agent.channels.feishu_channel import FeishuChannel
from hypo_agent.models import Message


class QueueStub:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def put(self, event: dict) -> None:
        self.events.append(event)


class ApiClientStub:
    def __init__(self) -> None:
        self.reply_calls: list[dict] = []
        self.create_calls: list[dict] = []

    def reply(self, payload: dict) -> None:
        self.reply_calls.append(payload)

    def create(self, payload: dict) -> None:
        self.create_calls.append(payload)


def _message_event(
    *,
    chat_id: str,
    message_id: str,
    message_type: str,
    content: str,
    open_id: str = "ou_xxx",
):
    sender_id = SimpleNamespace(open_id=open_id, user_id="", union_id="")
    sender = SimpleNamespace(sender_id=sender_id)
    message = SimpleNamespace(
        chat_id=chat_id,
        message_id=message_id,
        message_type=message_type,
        content=content,
    )
    return SimpleNamespace(event=SimpleNamespace(message=message, sender=sender))


def test_feishu_channel_enqueues_text_message_and_tracks_session() -> None:
    async def _run() -> None:
        queue = QueueStub()
        api_client = ApiClientStub()
        broadcasted: list[tuple[Message, str | None]] = []

        async def on_message(message: Message, *, message_type: str | None = None) -> None:
            broadcasted.append((message, message_type))

        channel = FeishuChannel(
            app_id="app-id",
            app_secret="app-secret",
            message_queue=queue,
            api_client=api_client,
            inbound_callback_getter=lambda: on_message,
        )
        channel._loop = asyncio.get_running_loop()  # type: ignore[attr-defined]

        event = _message_event(
            chat_id="oc_chat_123",
            message_id="om_123",
            message_type="text",
            content='{"text":"你好"}',
            open_id="ou_user_1",
        )

        await channel._handle_message_receive(event)  # type: ignore[attr-defined]

        assert channel.resolve_session_id("oc_chat_123") == "main"
        assert channel.resolve_chat_id("main") == "oc_chat_123"
        assert len(queue.events) == 1
        inbound = queue.events[0]["message"]
        assert inbound.text == "你好"
        assert inbound.channel == "feishu"
        assert inbound.session_id == "main"
        assert inbound.sender_id == "ou_user_1"
        assert callable(queue.events[0]["emit"])
        assert len(broadcasted) == 1
        assert broadcasted[0][0].session_id == "main"
        assert broadcasted[0][1] == "user_message"

    asyncio.run(_run())


def test_feishu_channel_replies_unsupported_for_non_text_message() -> None:
    async def _run() -> None:
        queue = QueueStub()
        api_client = ApiClientStub()
        channel = FeishuChannel(
            app_id="app-id",
            app_secret="app-secret",
            message_queue=queue,
            api_client=api_client,
        )
        channel._loop = asyncio.get_running_loop()  # type: ignore[attr-defined]

        event = _message_event(
            chat_id="oc_chat_123",
            message_id="om_unsupported",
            message_type="image",
            content="{}",
        )

        await channel._handle_message_receive(event)  # type: ignore[attr-defined]

        assert queue.events == []
        assert api_client.reply_calls == []
        assert len(api_client.create_calls) == 1
        assert api_client.create_calls[0]["receive_id_type"] == "chat_id"
        assert api_client.create_calls[0]["receive_id"] == "oc_chat_123"
        assert "暂不支持该消息类型" in api_client.create_calls[0]["content"]

    asyncio.run(_run())


def test_feishu_channel_pushes_proactive_message_to_mapped_chat() -> None:
    async def _run() -> None:
        queue = QueueStub()
        api_client = ApiClientStub()
        channel = FeishuChannel(
            app_id="app-id",
            app_secret="app-secret",
            message_queue=queue,
            api_client=api_client,
        )
        channel.bind_chat_session(chat_id="oc_chat_123", session_id="main")

        result = await channel.push_proactive(
            Message(
                text="提醒：开会",
                sender="assistant",
                session_id="main",
                channel="system",
                message_tag="reminder",
            )
        )

        assert result.success is True
        assert len(api_client.create_calls) == 1
        call = api_client.create_calls[0]
        assert call["receive_id_type"] == "chat_id"
        assert call["receive_id"] == "oc_chat_123"
        assert call["msg_type"] == "interactive"
        card = json.loads(call["content"])
        assert card["schema"] == "2.0"
        assert card["body"]["elements"][0] == {"tag": "markdown", "content": "提醒：开会"}

    asyncio.run(_run())


def test_feishu_channel_skips_ephemeral_tool_status_pushes() -> None:
    async def _run() -> None:
        queue = QueueStub()
        api_client = ApiClientStub()
        channel = FeishuChannel(
            app_id="app-id",
            app_secret="app-secret",
            message_queue=queue,
            api_client=api_client,
        )
        channel.bind_chat_session(chat_id="oc_chat_123", session_id="main")

        result = await channel.push_proactive(
            Message(
                text="⏳ 正在处理...",
                sender="assistant",
                session_id="main",
                channel="system",
                message_tag="tool_status",
                metadata={"ephemeral": True},
            )
        )

        assert result.success is True
        assert result.segment_count == 0
        assert api_client.create_calls == []

    asyncio.run(_run())


def test_feishu_channel_emit_does_not_send_normal_assistant_reply_directly() -> None:
    async def _run() -> None:
        queue = QueueStub()
        api_client = ApiClientStub()
        channel = FeishuChannel(
            app_id="app-id",
            app_secret="app-secret",
            message_queue=queue,
            api_client=api_client,
        )

        emit = channel._make_emit_callback("oc_chat_123")  # type: ignore[attr-defined]
        await emit({"type": "assistant_chunk", "text": "hello"})
        await emit({"type": "assistant_done"})

        assert api_client.reply_calls == []
        assert api_client.create_calls == []

    asyncio.run(_run())
