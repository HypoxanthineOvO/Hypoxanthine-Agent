from __future__ import annotations

import asyncio

from hypo_agent.channels.weixin.ilink_client import SessionExpiredError
from hypo_agent.channels.weixin.weixin_channel import WeixinChannel
from hypo_agent.models import Message


class QueueStub:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def put(self, event: dict) -> None:
        self.events.append(event)


class FakeClient:
    def __init__(
        self,
        *,
        bot_token: str | None = "bot-token",
        user_id: str = "user@im.wechat",
        bot_id: str = "bot-1",
        updates: list[object] | None = None,
    ) -> None:
        self.bot_token = bot_token
        self.user_id = user_id
        self.bot_id = bot_id
        self.last_context_token = ""
        self._updates = list(updates or [])
        self.closed = False
        self.send_typing_calls: list[tuple[str, int]] = []
        self.download_media_error: Exception | None = None

    async def get_updates(self) -> list[dict]:
        if self._updates:
            value = self._updates.pop(0)
            if isinstance(value, Exception):
                raise value
            return value
        await asyncio.sleep(0)
        return []

    async def send_typing(self, user_id: str, status: int = 1) -> None:
        self.send_typing_calls.append((user_id, status))

    async def download_media(self, url: str) -> bytes:
        del url
        if self.download_media_error is not None:
            raise self.download_media_error
        return b""

    async def close(self) -> None:
        self.closed = True

    def remember_user_id(self, user_id: str) -> None:
        self.user_id = user_id

    def remember_context_token(self, context_token: str) -> None:
        self.last_context_token = context_token


def test_weixin_channel_start_skips_when_token_missing() -> None:
    async def _run() -> None:
        queue = QueueStub()
        client = FakeClient(bot_token=None)
        channel = WeixinChannel(
            config={"token_path": "memory/weixin_auth.json", "allowed_users": []},
            message_queue=queue,
            build_message=Message,
            client_factory=lambda: client,
        )

        await channel.start()

        assert channel.client is client
        assert channel._task is None
        assert channel._running is False
        assert queue.events == []

        await channel.stop()

    asyncio.run(_run())


def test_weixin_channel_start_with_token_polls_and_enqueues_message() -> None:
    async def _run() -> None:
        queue = QueueStub()
        client = FakeClient(
            updates=[
                [
                    {
                        "from_user_id": "alice@im.wechat",
                        "item_list": [{"type": 1, "text_item": {"text": "你好"}}],
                    }
                ]
            ]
        )
        channel = WeixinChannel(
            config={"token_path": "memory/weixin_auth.json", "allowed_users": []},
            message_queue=queue,
            build_message=Message,
            client_factory=lambda: client,
        )

        await channel.start()
        await asyncio.sleep(0.05)
        await channel.stop()

        assert len(queue.events) == 1
        event = queue.events[0]
        assert event["event_type"] == "user_message"
        inbound = event["message"]
        assert inbound.text == "你好"
        assert inbound.channel == "weixin"
        assert inbound.session_id == "main"
        assert inbound.sender_id == "alice@im.wechat"

    asyncio.run(_run())


def test_weixin_channel_rejects_user_outside_allowlist() -> None:
    async def _run() -> None:
        queue = QueueStub()
        channel = WeixinChannel(
            config={
                "token_path": "memory/weixin_auth.json",
                "allowed_users": ["allowed@im.wechat"],
            },
            message_queue=queue,
            build_message=Message,
            client_factory=lambda: FakeClient(),
        )

        await channel._handle_message(  # type: ignore[attr-defined]
            {
                "from_user_id": "blocked@im.wechat",
                "item_list": [{"type": 1, "text_item": {"text": "hello"}}],
            }
        )

        assert queue.events == []

    asyncio.run(_run())


def test_weixin_channel_allows_all_users_when_allowlist_empty() -> None:
    async def _run() -> None:
        queue = QueueStub()
        channel = WeixinChannel(
            config={"token_path": "memory/weixin_auth.json", "allowed_users": []},
            message_queue=queue,
            build_message=Message,
            client_factory=lambda: FakeClient(),
        )

        await channel._handle_message(  # type: ignore[attr-defined]
            {
                "from_user_id": "anyone@im.wechat",
                "item_list": [{"type": 1, "text_item": {"text": "hello"}}],
            }
        )

        assert len(queue.events) == 1

    asyncio.run(_run())


def test_weixin_channel_persists_latest_context_token_from_inbound_message() -> None:
    async def _run() -> None:
        queue = QueueStub()
        client = FakeClient()
        channel = WeixinChannel(
            config={"token_path": "memory/weixin_auth.json", "allowed_users": []},
            message_queue=queue,
            build_message=Message,
            client_factory=lambda: client,
        )
        channel.client = client

        await channel._handle_message(  # type: ignore[attr-defined]
            {
                "from_user_id": "alice@im.wechat",
                "context_token": "ctx-123",
                "item_list": [{"type": 1, "text_item": {"text": "hello"}}],
            }
        )

        assert client.user_id == "alice@im.wechat"
        assert client.last_context_token == "ctx-123"
        assert queue.events[0]["message"].metadata["weixin"]["context_token"] == "ctx-123"

    asyncio.run(_run())


def test_weixin_channel_session_expired_stops_polling() -> None:
    async def _run() -> None:
        queue = QueueStub()
        client = FakeClient(updates=[SessionExpiredError("/ilink/bot/getupdates", {"errcode": -14})])
        channel = WeixinChannel(
            config={"token_path": "memory/weixin_auth.json", "allowed_users": []},
            message_queue=queue,
            build_message=Message,
            client_factory=lambda: client,
        )

        await channel.start()
        await asyncio.sleep(0.05)

        assert channel._running is False

        await channel.stop()

    asyncio.run(_run())


def test_weixin_channel_retries_after_poll_error_with_backoff() -> None:
    async def _run() -> None:
        queue = QueueStub()
        client = FakeClient(updates=[RuntimeError("boom"), RuntimeError("boom-again")])
        sleep_calls: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)
            if len(sleep_calls) >= 2:
                channel._running = False

        channel = WeixinChannel(
            config={"token_path": "memory/weixin_auth.json", "allowed_users": []},
            message_queue=queue,
            build_message=Message,
            client_factory=lambda: client,
            sleep_func=fake_sleep,
        )

        await channel.start()
        await asyncio.sleep(0.05)
        await channel.stop()

        assert sleep_calls[:2] == [1.0, 2.0]

    asyncio.run(_run())


def test_weixin_channel_stop_closes_client_and_cancels_task() -> None:
    async def _run() -> None:
        queue = QueueStub()
        client = FakeClient()
        channel = WeixinChannel(
            config={"token_path": "memory/weixin_auth.json", "allowed_users": []},
            message_queue=queue,
            build_message=Message,
            client_factory=lambda: client,
        )

        await channel.start()
        task = channel._task
        assert task is not None

        await channel.stop()

        assert client.closed is True
        assert channel._task is None
        assert channel._running is False

    asyncio.run(_run())


def test_weixin_channel_falls_back_when_image_download_fails() -> None:
    async def _run() -> None:
        queue = QueueStub()
        client = FakeClient(user_id="")
        client.download_media_error = RuntimeError("cdn timeout")
        channel = WeixinChannel(
            config={"token_path": "memory/weixin_auth.json", "allowed_users": []},
            message_queue=queue,
            build_message=Message,
            client_factory=lambda: client,
        )
        channel.client = client

        await channel._handle_message(  # type: ignore[attr-defined]
            {
                "from_user_id": "alice@im.wechat",
                "item_list": [
                    {
                        "type": 2,
                        "image_item": {
                            "url": "https://cdn.example/img",
                            "aes_key": "00112233445566778899aabbccddeeff",
                        },
                    }
                ],
            }
        )

        assert len(queue.events) == 1
        inbound = queue.events[0]["message"]
        assert inbound.text == "[用户发送了一张图片，但下载失败]"
        assert inbound.attachments == []
        assert client.user_id == "alice@im.wechat"

    asyncio.run(_run())
