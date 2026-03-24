from __future__ import annotations

import asyncio
import base64
from pathlib import Path

from hypo_agent.channels.weixin.weixin_adapter import WeixinAdapter
from hypo_agent.models import Message


_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO5kZxQAAAAASUVORK5CYII="
)


class DummyClient:
    def __init__(self, *, bot_token: str | None = "bot-token") -> None:
        self.bot_token = bot_token
        self.sent: list[tuple[str, str]] = []
        self.sent_images: list[dict] = []

    async def send_message(self, to_user_id: str, text: str, context_token: str = "", **kwargs) -> str:
        self.sent.append((to_user_id, text))
        return f"wcb-{len(self.sent)}"

    async def get_upload_url(self, file_type: str, file_size: int) -> dict:
        return {
            "upload_url": f"https://upload.example/{file_type}",
            "file_id": f"{file_type}-1",
        }

    async def upload_media(self, upload_url: str, encrypted_data: bytes) -> None:
        del upload_url, encrypted_data

    async def send_image(self, to_user_id: str, file_id: str, aes_key_hex: str, width: int, height: int, file_size: int, **kwargs) -> dict:
        payload = {
            "to_user_id": to_user_id,
            "file_id": file_id,
            "aes_key_hex": aes_key_hex,
            "width": width,
            "height": height,
            "file_size": file_size,
            **kwargs,
        }
        self.sent_images.append(payload)
        return payload


def test_weixin_adapter_pushes_message_via_ilink_client() -> None:
    async def _run() -> None:
        client = DummyClient()
        adapter = WeixinAdapter(client=client, target_user_id="user@im.wechat")

        await adapter.push(
            Message(
                text="hello",
                sender="assistant",
                session_id="main",
                channel="system",
            )
        )

        assert client.sent == [("user@im.wechat", "hello")]

    asyncio.run(_run())


def test_weixin_adapter_splits_long_messages_and_waits_between_segments() -> None:
    async def _run() -> None:
        client = DummyClient()
        sleep_calls: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        adapter = WeixinAdapter(
            client=client,
            target_user_id="user@im.wechat",
            message_limit=10,
            send_delay_seconds=0.3,
            sleep_func=fake_sleep,
        )

        await adapter.push(
            Message(
                text="1234567890ABCDEFGHIJ",
                sender="assistant",
                session_id="main",
                channel="system",
            )
        )

        assert client.sent == [
            ("user@im.wechat", "1234567890"),
            ("user@im.wechat", "ABCDEFGHIJ"),
        ]
        assert sleep_calls == [0.3]

    asyncio.run(_run())


def test_weixin_adapter_formats_message_tag_and_downgrades_markdown() -> None:
    adapter = WeixinAdapter(client=DummyClient(), target_user_id="user@im.wechat")

    text = adapter._format_text(
        Message(
            text="**提醒** `喝水`",
            sender="assistant",
            session_id="main",
            message_tag="reminder",
        )
    )

    assert text.startswith("🔔 ")
    assert "**" not in text
    assert "`" not in text
    assert "提醒" in text
    assert "喝水" in text


def test_weixin_adapter_skips_push_when_token_missing() -> None:
    async def _run() -> None:
        client = DummyClient(bot_token=None)
        adapter = WeixinAdapter(client=client, target_user_id="user@im.wechat")

        await adapter.push(
            Message(
                text="hello",
                sender="assistant",
                session_id="main",
            )
        )

        assert client.sent == []

    asyncio.run(_run())


def test_weixin_adapter_uses_client_user_id_when_target_missing() -> None:
    async def _run() -> None:
        client = DummyClient()
        client.user_id = "learned@im.wechat"  # type: ignore[attr-defined]
        adapter = WeixinAdapter(client=client, target_user_id="")

        await adapter.push(
            Message(
                text="heartbeat",
                sender="assistant",
                session_id="main",
                channel="system",
                message_tag="heartbeat",
            )
        )

        assert client.sent == [("learned@im.wechat", "💓 heartbeat")]

    asyncio.run(_run())


def test_weixin_adapter_tracks_latest_client_user_id_when_target_is_dynamic() -> None:
    async def _run() -> None:
        client = DummyClient()
        client.user_id = "first@im.wechat"  # type: ignore[attr-defined]
        adapter = WeixinAdapter(client=client, target_user_id="")

        await adapter.push(
            Message(
                text="first push",
                sender="assistant",
                session_id="main",
                channel="system",
            )
        )

        client.user_id = "second@im.wechat"  # type: ignore[attr-defined]
        await adapter.push(
            Message(
                text="second push",
                sender="assistant",
                session_id="main",
                channel="system",
            )
        )

        assert client.sent == [
            ("first@im.wechat", "first push"),
            ("second@im.wechat", "second push"),
        ]

    asyncio.run(_run())


def test_weixin_adapter_splits_inline_images_for_weixin_delivery(tmp_path: Path) -> None:
    async def _run() -> None:
        image_path = tmp_path / "cat.png"
        image_path.write_bytes(_PNG_1X1)
        client = DummyClient()
        adapter = WeixinAdapter(
            client=client,
            target_user_id="user@im.wechat",
            send_delay_seconds=0,
        )

        await adapter.push(
            Message(
                text=f"请看截图 ![cat]({image_path})",
                sender="assistant",
                session_id="main",
                channel="system",
            )
        )

        assert client.sent == [("user@im.wechat", "请看截图 【见下方图片】")]
        assert len(client.sent_images) == 1
        assert client.sent_images[0]["to_user_id"] == "user@im.wechat"
        assert client.sent_images[0]["width"] == 1
        assert client.sent_images[0]["height"] == 1

    asyncio.run(_run())
