from __future__ import annotations

import asyncio
import base64
from pathlib import Path

from hypo_agent.channels.weixin.ilink_client import ILinkAPIError
from hypo_agent.channels.weixin.weixin_adapter import WeixinAdapter
from hypo_agent.models import Message


_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO5kZxQAAAAASUVORK5CYII="
)


class DummyClient:
    def __init__(self, *, bot_token: str | None = "bot-token") -> None:
        self.bot_token = bot_token
        self.user_id = ""
        self.last_context_token = ""
        self.sent: list[tuple[str, str]] = []
        self.sent_context_tokens: list[str | None] = []
        self.sent_images: list[dict] = []
        self.sent_files: list[dict] = []
        self.raw_messages: list[dict] = []
        self.upload_requests: list[dict] = []
        self.uploaded_payloads: list[dict] = []
        self.fail_texts: dict[str, int] = {}
        self.fail_uploads = 0

    async def send_message(self, to_user_id: str, text: str, context_token: str = "", **kwargs) -> str:
        remaining_failures = self.fail_texts.get(text, 0)
        if remaining_failures > 0:
            self.fail_texts[text] = remaining_failures - 1
            raise ILinkAPIError("/ilink/bot/sendmessage", {"ret": -2})
        self.sent.append((to_user_id, text))
        self.sent_context_tokens.append(context_token)
        return f"wcb-{len(self.sent)}"

    async def send_message_raw(
        self,
        *,
        to_user_id: str,
        text: str | None = None,
        item_list: list[dict] | None = None,
        context_token: str | None = "",
        **kwargs,
    ) -> dict:
        del kwargs
        resolved_item_list = list(item_list or [])
        if not resolved_item_list:
            resolved_item_list = [{"type": 1, "text_item": {"text": str(text or "")}}]

        combined_text = "\n".join(
            str(item.get("text_item", {}).get("text") or "")
            for item in resolved_item_list
            if int(item.get("type") or 0) == 1
        )
        remaining_failures = self.fail_texts.get(combined_text, 0)
        if remaining_failures > 0:
            self.fail_texts[combined_text] = remaining_failures - 1
            raise ILinkAPIError("/ilink/bot/sendmessage", {"ret": -2})

        self.raw_messages.append(
            {
                "to_user_id": to_user_id,
                "item_list": resolved_item_list,
                "context_token": context_token,
            }
        )
        for item in resolved_item_list:
            item_type = int(item.get("type") or 0)
            if item_type == 1:
                payload = str(item.get("text_item", {}).get("text") or "")
                self.sent.append((to_user_id, payload))
                self.sent_context_tokens.append(context_token)
                continue
            if item_type == 2:
                image_item = item.get("image_item", {})
                media = image_item.get("media", {}) if isinstance(image_item, dict) else {}
                self.sent_images.append(
                    {
                        "to_user_id": to_user_id,
                        "encrypt_query_param": media.get("encrypt_query_param"),
                        "aes_key": media.get("aes_key"),
                        "encrypted_file_size": image_item.get("mid_size"),
                        "context_token": context_token,
                    }
                )
                continue
            if item_type == 4:
                file_item = item.get("file_item", {})
                media = file_item.get("media", {}) if isinstance(file_item, dict) else {}
                self.sent_files.append(
                    {
                        "to_user_id": to_user_id,
                        "encrypt_query_param": media.get("encrypt_query_param"),
                        "aes_key": media.get("aes_key"),
                        "encrypted_file_size": file_item.get("file_size"),
                        "file_name": file_item.get("file_name"),
                        "context_token": context_token,
                    }
                )
        return {"ret": 0}

    def remember_user_id(self, user_id: str) -> None:
        self.user_id = user_id

    def remember_context_token(self, context_token: str) -> None:
        self.last_context_token = context_token

    @staticmethod
    def build_media_upload_payload(
        *,
        to_user_id: str,
        media_type: int,
        plaintext: bytes,
        encrypted_size: int,
        aes_key_hex: str,
    ) -> dict:
        return {
            "filekey": "filekey-1",
            "media_type": media_type,
            "to_user_id": to_user_id,
            "rawsize": len(plaintext),
            "rawfilemd5": "md5-1",
            "filesize": encrypted_size,
            "no_need_thumb": True,
            "aeskey": aes_key_hex,
        }

    async def get_upload_url(self, **payload) -> dict:
        if self.fail_uploads > 0:
            self.fail_uploads -= 1
            raise ILinkAPIError("/ilink/bot/getuploadurl", {"ret": -2})
        self.upload_requests.append(payload)
        return {
            "upload_param": "upload-param-1",
        }

    async def upload_media(self, *, upload_param: str, filekey: str, encrypted_data: bytes) -> str:
        self.uploaded_payloads.append(
            {
                "upload_param": upload_param,
                "filekey": filekey,
                "encrypted_size": len(encrypted_data),
            }
        )
        return "download-param-1"

    async def send_image(
        self,
        to_user_id: str,
        encrypt_query_param: str,
        aes_key: str,
        encrypted_file_size: int,
        **kwargs,
    ) -> dict:
        payload = {
            "to_user_id": to_user_id,
            "encrypt_query_param": encrypt_query_param,
            "aes_key": aes_key,
            "encrypted_file_size": encrypted_file_size,
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
        assert sleep_calls == []

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
        client.last_context_token = "ctx-inline-image"
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
        assert client.sent_images[0]["encrypt_query_param"] == "download-param-1"
        assert client.sent_images[0]["encrypted_file_size"] >= len(_PNG_1X1)

    asyncio.run(_run())


def test_weixin_adapter_sends_text_plus_image_attachment_for_qr_handoff(tmp_path: Path) -> None:
    async def _run() -> None:
        image_path = tmp_path / "zhihu-qr.png"
        image_path.write_bytes(_PNG_1X1)
        client = DummyClient()
        client.last_context_token = "ctx-qr-handoff"
        adapter = WeixinAdapter(
            client=client,
            target_user_id="user@im.wechat",
            send_delay_seconds=0,
        )

        await adapter.push(
            Message(
                text="已自动切换到浏览器二维码，请扫描新二维码。",
                sender="assistant",
                session_id="main",
                channel="system",
                attachments=[
                    {
                        "type": "image",
                        "url": str(image_path),
                        "filename": "zhihu-qr.png",
                        "mime_type": "image/png",
                    }
                ],
            )
        )

        assert client.sent == [("user@im.wechat", "已自动切换到浏览器二维码，请扫描新二维码。")]
        assert len(client.sent_images) == 1
        assert client.sent_images[0]["to_user_id"] == "user@im.wechat"
        assert client.sent_images[0]["encrypt_query_param"] == "download-param-1"

    asyncio.run(_run())


def test_weixin_adapter_sends_file_attachment_as_file_item(tmp_path: Path) -> None:
    async def _run() -> None:
        export_path = tmp_path / "notion-export.md"
        export_path.write_text("# Notion Export\n", encoding="utf-8")
        client = DummyClient()
        client.last_context_token = "ctx-file"
        adapter = WeixinAdapter(
            client=client,
            target_user_id="user@im.wechat",
            send_delay_seconds=0,
        )

        await adapter.push(
            Message(
                text="已导出 Notion 文件。",
                sender="assistant",
                session_id="main",
                channel="system",
                attachments=[
                    {
                        "type": "file",
                        "url": str(export_path),
                        "filename": "notion-export.md",
                        "mime_type": "text/markdown",
                    }
                ],
            )
        )

        assert client.sent == [("user@im.wechat", "已导出 Notion 文件。")]
        assert len(client.sent_files) == 1
        assert client.sent_files[0]["to_user_id"] == "user@im.wechat"
        assert client.sent_files[0]["encrypt_query_param"] == "download-param-1"
        assert client.sent_files[0]["file_name"] == "notion-export.md"
        assert client.upload_requests[0]["media_type"] == 4

    asyncio.run(_run())


def test_weixin_adapter_falls_back_to_text_when_image_upload_fails(tmp_path: Path) -> None:
    async def _run() -> None:
        image_path = tmp_path / "cat.png"
        image_path.write_bytes(_PNG_1X1)
        client = DummyClient()
        client.fail_uploads = 3
        client.last_context_token = "ctx-image-fail"
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

        assert client.sent == [
            ("user@im.wechat", "请看截图 【见下方图片】"),
            ("user@im.wechat", "[图片] cat.png"),
        ]
        assert client.sent_images == []

    asyncio.run(_run())


def test_weixin_adapter_retries_image_upload_before_success(tmp_path: Path) -> None:
    async def _run() -> None:
        image_path = tmp_path / "cat.png"
        image_path.write_bytes(_PNG_1X1)
        client = DummyClient()
        client.fail_uploads = 1
        client.last_context_token = "ctx-image-retry"
        sleep_calls: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        adapter = WeixinAdapter(
            client=client,
            target_user_id="user@im.wechat",
            send_delay_seconds=0,
            sleep_func=fake_sleep,
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
        assert sleep_calls == [0.5]

    asyncio.run(_run())


def test_weixin_adapter_retries_without_context_token_on_ret_minus_2() -> None:
    async def _run() -> None:
        client = DummyClient()
        client.fail_texts["heartbeat"] = 1
        adapter = WeixinAdapter(client=client, target_user_id="user@im.wechat")

        await adapter.push(
            Message(
                text="heartbeat",
                sender="assistant",
                session_id="main",
                channel="system",
            )
        )

        assert client.sent == [("user@im.wechat", "heartbeat")]
        assert client.sent_context_tokens == [None]

    asyncio.run(_run())


def test_weixin_adapter_splits_text_after_ret_minus_2_when_payload_too_large() -> None:
    async def _run() -> None:
        client = DummyClient()
        text = "你" * 80
        client.fail_texts[text] = 2
        adapter = WeixinAdapter(client=client, target_user_id="user@im.wechat")

        await adapter.push(
            Message(
                text=text,
                sender="assistant",
                session_id="main",
                channel="system",
            )
        )

        assert len(client.sent) >= 2
        assert "".join(chunk for _, chunk in client.sent) == text
        assert all(token is None for token in client.sent_context_tokens)

    asyncio.run(_run())


def test_weixin_adapter_uses_persisted_context_token_for_proactive_message() -> None:
    async def _run() -> None:
        client = DummyClient()
        client.last_context_token = "ctx-persisted"
        adapter = WeixinAdapter(client=client, target_user_id="user@im.wechat")

        await adapter.push(
            Message(
                text="heartbeat",
                sender="assistant",
                session_id="main",
                channel="system",
            )
        )

        assert client.sent == [("user@im.wechat", "heartbeat")]
        assert client.sent_context_tokens == ["ctx-persisted"]

    asyncio.run(_run())


def test_weixin_adapter_degrades_mixed_image_message_without_context_token(tmp_path: Path) -> None:
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

        assert client.sent == [
            ("user@im.wechat", "请看截图 【见下方图片】"),
            ("user@im.wechat", "[图片] cat.png"),
        ]
        assert client.sent_images == []

    asyncio.run(_run())


def test_weixin_adapter_flushes_deferred_text_after_failed_mixed_batch(tmp_path: Path, monkeypatch) -> None:
    async def _run() -> None:
        image_path = tmp_path / "table.png"
        image_path.write_bytes(_PNG_1X1)
        client = DummyClient()
        client.last_context_token = "ctx-first"
        adapter = WeixinAdapter(
            client=client,
            target_user_id="user@im.wechat",
            send_delay_seconds=0,
        )

        segments_queue = [
            [
                {"type": "text", "text": "文本A"},
                {"type": "image", "source": str(image_path), "fallback_text": "[表格渲染失败，原始内容如下]\nA | B"},
                {"type": "text", "text": "文本B"},
            ],
            [
                {"type": "text", "text": "新的消息"},
            ],
        ]

        async def fake_prepare_segments(_message, *, allow_image_upload: bool):
            del allow_image_upload
            return segments_queue.pop(0)

        async def fake_send_segments_batch(segments, *, target_user_id: str, context_token: str):
            if any(str(segment.get("text") or "") == "文本B" for segment in segments):
                raise ILinkAPIError("/ilink/bot/sendmessage", {"ret": -2})
            item_list = [{"type": 1, "text_item": {"text": str(segment.get("text") or "")}} for segment in segments]
            return await client.send_message_raw(
                to_user_id=target_user_id,
                item_list=item_list,
                context_token=context_token,
            )

        monkeypatch.setattr(adapter, "_prepare_segments", fake_prepare_segments)
        monkeypatch.setattr(adapter, "_send_segments_batch", fake_send_segments_batch)

        first_result = await adapter.push(
            Message(
                text="第一次发送",
                sender="assistant",
                session_id="main",
                channel="system",
            )
        )

        assert first_result.success is False
        assert first_result.segment_count == 3
        assert first_result.failed_segments == 3
        assert client.sent == []
        assert client.sent_images == []

        second_result = await adapter.push(
            Message(
                text="第二次发送",
                sender="assistant",
                session_id="main",
                channel="system",
                metadata={"weixin": {"context_token": "ctx-second"}},
            )
        )

        assert second_result.success is True
        assert client.sent == [
            ("user@im.wechat", "文本A\n[表格渲染失败，原始内容如下]\nA | B\n文本B"),
            ("user@im.wechat", "新的消息"),
        ]

    asyncio.run(_run())
