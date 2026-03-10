from __future__ import annotations

import asyncio

from hypo_agent.channels.qq_channel import QQChannelService
from hypo_agent.models import Message


class PipelineStub:
    def __init__(self) -> None:
        self.inbounds: list[Message] = []
        self.on_proactive_message = None

    async def enqueue_user_message(self, inbound: Message, *, emit):
        self.inbounds.append(inbound)
        await emit(
            {
                "type": "assistant_chunk",
                "text": "**收到**",
                "sender": "assistant",
                "session_id": inbound.session_id,
            }
        )
        await emit(
            {
                "type": "assistant_done",
                "sender": "assistant",
                "session_id": inbound.session_id,
            }
        )
        if self.on_proactive_message is not None:
            await self.on_proactive_message(
                Message(
                    text="收到",
                    sender="assistant",
                    session_id=inbound.session_id,
                    channel=inbound.channel,
                    sender_id=inbound.sender_id,
                )
            )


def test_qq_whitelist_rejects_non_allowed_user() -> None:
    async def _run() -> None:
        service = QQChannelService(
            napcat_http_url="http://localhost:3000",
            bot_qq="123456789",
            allowed_users={"10001"},
        )
        pipeline = PipelineStub()
        sent: list[tuple[str, str]] = []

        async def fake_send_private_text(*, user_id: str, text: str) -> bool:
            sent.append((user_id, text))
            return True

        service.adapter.send_private_text = fake_send_private_text  # type: ignore[method-assign]

        ok = await service.handle_onebot_event(
            {
                "post_type": "message",
                "message_type": "private",
                "user_id": "10002",
                "message": "hello",
            },
            pipeline=pipeline,
        )

        assert ok is False
        assert pipeline.inbounds == []
        assert sent == []

    asyncio.run(_run())


def test_qq_whitelist_allows_user_and_sends_reply() -> None:
    async def _run() -> None:
        service = QQChannelService(
            napcat_http_url="http://localhost:3000",
            bot_qq="123456789",
            allowed_users={"10001"},
        )
        pipeline = PipelineStub()
        broadcasted: list[Message] = []

        async def capture_broadcast(message: Message) -> None:
            broadcasted.append(message)

        pipeline.on_proactive_message = capture_broadcast

        ok = await service.handle_onebot_event(
            {
                "post_type": "message",
                "message_type": "private",
                "user_id": "10001",
                "message": "你好",
            },
            pipeline=pipeline,
        )

        assert ok is True
        assert len(pipeline.inbounds) == 1
        assert pipeline.inbounds[0].channel == "qq"
        assert pipeline.inbounds[0].sender_id == "10001"
        assert len(broadcasted) == 1
        assert broadcasted[0].text == "收到"
        assert broadcasted[0].channel == "qq"
        assert broadcasted[0].sender_id == "10001"

    asyncio.run(_run())


def test_qq_channel_push_proactive_to_allowed_users() -> None:
    async def _run() -> None:
        service = QQChannelService(
            napcat_http_url="http://localhost:3000",
            bot_qq="123456789",
            allowed_users={"10001", "10002"},
        )
        pushed: list[str] = []

        async def fake_send_message(*, user_id: str, message: Message) -> bool:
            pushed.append(user_id)
            return True

        service.adapter.send_message = fake_send_message  # type: ignore[method-assign]

        await service.push_proactive(
            Message(
                text="测试主动消息",
                sender="assistant",
                session_id="main",
                message_tag="reminder",
            )
        )

        assert set(pushed) == {"10001", "10002"}

    asyncio.run(_run())
