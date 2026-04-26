from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from hypo_agent.channels.feishu_channel import FeishuChannel
from hypo_agent.core.channel_dispatcher import ChannelDispatcher, ChannelRelayPolicy
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
        self.upload_image_calls: list[bytes] = []
        self.upload_file_calls: list[tuple[bytes, str, str]] = []

    def reply(self, payload: dict) -> None:
        self.reply_calls.append(payload)

    def create(self, payload: dict) -> None:
        self.create_calls.append(payload)

    def upload_image(self, payload: bytes) -> str:
        self.upload_image_calls.append(payload)
        return f"img_{len(self.upload_image_calls)}"

    def upload_file(self, payload: bytes, filename: str, file_type: str = "stream") -> str:
        self.upload_file_calls.append((payload, filename, file_type))
        return f"file_{len(self.upload_file_calls)}"


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


def test_feishu_channel_pushes_image_attachments_for_proactive_message(tmp_path) -> None:
    async def _run() -> None:
        queue = QueueStub()
        api_client = ApiClientStub()
        image_path = tmp_path / "wewe.png"
        image_path.write_bytes(b"fake-png")
        channel = FeishuChannel(
            app_id="app-id",
            app_secret="app-secret",
            message_queue=queue,
            api_client=api_client,
        )
        channel.bind_chat_session(chat_id="oc_chat_123", session_id="main")

        result = await channel.push_proactive(
            Message(
                text="请扫码登录 WeWe RSS。",
                sender="assistant",
                session_id="main",
                channel="feishu",
                attachments=[{"type": "image", "url": str(image_path), "filename": "wewe.png"}],
                message_tag="tool_status",
                metadata={"feishu": {"chat_id": "oc_chat_123"}},
            )
        )

        assert result.success is True
        assert api_client.upload_image_calls == [b"fake-png"]
        assert len(api_client.create_calls) == 2
        assert api_client.create_calls[0]["msg_type"] == "interactive"
        assert api_client.create_calls[1]["msg_type"] == "image"
        assert json.loads(api_client.create_calls[1]["content"]) == {"image_key": "img_1"}

    asyncio.run(_run())


def test_feishu_channel_pushes_file_attachments_for_proactive_message(tmp_path) -> None:
    async def _run() -> None:
        queue = QueueStub()
        api_client = ApiClientStub()
        export_path = tmp_path / "notion-export.md"
        export_path.write_text("# Notion Export\n", encoding="utf-8")
        channel = FeishuChannel(
            app_id="app-id",
            app_secret="app-secret",
            message_queue=queue,
            api_client=api_client,
        )
        channel.bind_chat_session(chat_id="oc_chat_123", session_id="main")

        result = await channel.push_proactive(
            Message(
                text="已导出 Notion 文件。",
                sender="assistant",
                session_id="main",
                channel="feishu",
                attachments=[
                    {
                        "type": "file",
                        "url": str(export_path),
                        "filename": "notion-export.md",
                        "mime_type": "text/markdown",
                    }
                ],
                metadata={"feishu": {"chat_id": "oc_chat_123"}},
            )
        )

        assert result.success is True
        assert api_client.upload_file_calls == [
            (b"# Notion Export\n", "notion-export.md", "stream")
        ]
        assert len(api_client.create_calls) == 2
        assert api_client.create_calls[0]["msg_type"] == "interactive"
        assert api_client.create_calls[1]["msg_type"] == "file"
        assert json.loads(api_client.create_calls[1]["content"]) == {"file_key": "file_1"}

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
                text="临时状态消息",
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


def test_feishu_channel_inbound_message_relays_through_channel_policy() -> None:
    async def _run() -> None:
        queue = QueueStub()
        api_client = ApiClientStub()
        dispatcher = ChannelDispatcher()
        relay = ChannelRelayPolicy(dispatcher)
        qq_deliveries: list[str] = []
        weixin_deliveries: list[str] = []

        async def qq_sink(message) -> None:
            qq_deliveries.append(str(message.raw_text or message.plain_text()))

        async def weixin_sink(message) -> None:
            weixin_deliveries.append(str(message.raw_text or message.plain_text()))

        dispatcher.register("qq", qq_sink, platform="qq", is_external=True)
        dispatcher.register("weixin", weixin_sink, platform="weixin", is_external=True)

        async def on_message(message: Message, *, message_type: str | None = None) -> None:
            await relay.relay_message(message, message_type=message_type)

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
            message_id="om_relay_1",
            message_type="text",
            content='{"text":"同步一下"}',
            open_id="ou_user_1",
        )

        await channel._handle_message_receive(event)  # type: ignore[attr-defined]

        assert qq_deliveries == ["[飞书] 同步一下"]
        assert weixin_deliveries == ["[飞书] 同步一下"]

    asyncio.run(_run())



def test_feishu_channel_emit_suppresses_mechanical_progress_for_heavy_tool() -> None:
    async def _run() -> None:
        queue = QueueStub()
        api_client = ApiClientStub()
        channel = FeishuChannel(
            app_id="app-id",
            app_secret="app-secret",
            message_queue=queue,
            api_client=api_client,
        )

        emit = channel._make_emit_callback("oc_chat_123")
        await emit({"type": "pipeline_stage", "stage": "preprocessing", "detail": "正在分析你的消息..."})
        await emit({"type": "tool_call_start", "tool_name": "search_web", "tool_call_id": "call-1"})

        assert api_client.create_calls == []

    asyncio.run(_run())
