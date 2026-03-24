from __future__ import annotations

import asyncio
from pathlib import Path
from urllib import request as urllib_request
from urllib.parse import urlparse

import structlog

from hypo_agent.channels.qq_adapter import QQAdapter
from hypo_agent.channels.weixin.crypto import encrypt_media, generate_aes_key
from hypo_agent.channels.weixin.ilink_client import ILinkClient
from hypo_agent.core.platform_message_preparation import prepare_message_for_platform
from hypo_agent.models import Attachment, Message

logger = structlog.get_logger("hypo_agent.channels.weixin.adapter")


class WeixinAdapter:
    """Channel sink for pushing proactive messages to Weixin."""

    def __init__(
        self,
        *,
        client: ILinkClient,
        target_user_id: str,
        image_renderer=None,
        message_limit: int = 2000,
        send_delay_seconds: float = 0.3,
        on_message_sent=None,
        sleep_func=asyncio.sleep,
    ) -> None:
        self.client = client
        self.target_user_id = str(target_user_id or "").strip()
        self.message_limit = max(1, int(message_limit))
        self.send_delay_seconds = max(0.0, float(send_delay_seconds))
        self._sleep = sleep_func
        self._on_message_sent = on_message_sent
        self._formatter = QQAdapter(
            napcat_http_url="http://localhost",
            image_renderer=image_renderer,
            message_limit=self.message_limit,
        )

    async def __call__(self, message: Message) -> None:
        await self.push(message)

    async def push(self, message: Message) -> None:
        if not str(getattr(self.client, "bot_token", "") or "").strip():
            logger.error("weixin.adapter.no_token", skip=True, message_tag=message.message_tag)
            return
        target_user_id = self._resolve_target_user_id(message)
        if not target_user_id:
            logger.error(
                "weixin.adapter.no_target_user",
                skip=True,
                message_tag=message.message_tag,
                channel=message.channel,
                sender_id=message.sender_id,
                client_user_id=str(getattr(self.client, "user_id", "") or ""),
            )
            return
        prepared_messages = prepare_message_for_platform(message, platform="weixin")
        if not prepared_messages:
            return

        for message_index, prepared_message in enumerate(prepared_messages):
            if message_index > 0 and self.send_delay_seconds > 0:
                await self._sleep(self.send_delay_seconds)
            try:
                if self._is_single_image_message(prepared_message):
                    await self._send_attachment_image(
                        prepared_message.attachments[0],
                        target_user_id=target_user_id,
                    )
                    continue

                rendered_message = prepared_message.model_copy(
                    update={
                        "text": self._prepend_source_prefix(
                            prepared_message,
                            str(prepared_message.text or ""),
                        )
                    }
                )
                segments = await self._formatter.format(rendered_message)
                if not segments:
                    continue
                for segment_index, segment in enumerate(segments):
                    if segment_index > 0 and self.send_delay_seconds > 0:
                        await self._sleep(self.send_delay_seconds)
                    await self._send_segment(segment, target_user_id=target_user_id)
            except Exception as exc:
                logger.exception(
                    "weixin.adapter.message_failed",
                    message_text=prepared_message.text,
                    attachment_types=[attachment.type for attachment in prepared_message.attachments],
                    target_user_id=target_user_id,
                    error=str(exc),
                )

    def _format_text(self, message: Message) -> str:
        rendered = message.model_copy(
            update={"text": self._prepend_source_prefix(message, str(message.text or ""))}
        )
        return self._formatter.render_message_text(rendered)

    def _split_message(self, text: str, *, limit: int | None = None) -> list[str]:
        return self._formatter.split_message(text, limit=limit)

    async def _send_segment(self, segment: dict, *, target_user_id: str) -> None:
        segment_type = str(segment.get("type") or "").strip().lower()
        if segment_type == "text":
            text = str(segment.get("data", {}).get("text") or "")
            chunks = [chunk for chunk in self._split_message(text, limit=self.message_limit) if chunk.strip()]
            for index, chunk in enumerate(chunks):
                if index > 0 and self.send_delay_seconds > 0:
                    await self._sleep(self.send_delay_seconds)
                if not chunk.strip():
                    continue
                await self.client.send_message(
                    to_user_id=target_user_id,
                    text=chunk,
                    context_token="",
                )
                self._record_message_sent()
            return

        if segment_type == "image":
            raw = str(segment.get("data", {}).get("file") or "").strip()
            if not raw:
                return
            await self._send_image_reference(raw, target_user_id=target_user_id)
            return

        if segment_type == "file":
            data = dict(segment.get("data") or {})
            label = str(data.get("name") or Path(str(data.get("file") or "")).name or "file").strip()
            await self.client.send_message(
                to_user_id=target_user_id,
                text=f"[文件] {label}",
                context_token="",
            )
            self._record_message_sent()

    async def _send_attachment_image(self, attachment: Attachment, *, target_user_id: str) -> None:
        await self._send_image_reference(str(attachment.url or "").strip(), target_user_id=target_user_id)

    async def _send_image_reference(self, image_ref: str, *, target_user_id: str) -> None:
        raw = await self._load_image_bytes(image_ref)
        aes_key = generate_aes_key()
        encrypted = encrypt_media(raw, aes_key)
        upload_payload = await self.client.get_upload_url("image", len(encrypted))
        upload_url = str(upload_payload.get("upload_url") or "").strip()
        file_id = str(upload_payload.get("file_id") or "").strip()
        if not upload_url or not file_id:
            raise RuntimeError("weixin upload response missing upload_url or file_id")
        await self.client.upload_media(upload_url, encrypted)
        width, height = self._read_image_size(raw)
        await self.client.send_image(
            to_user_id=target_user_id,
            file_id=file_id,
            aes_key_hex=aes_key.hex(),
            width=width,
            height=height,
            file_size=len(raw),
            context_token="",
        )
        self._record_message_sent()

    async def _load_image_bytes(self, image_ref: str) -> bytes:
        raw_ref = str(image_ref or "").strip()
        if raw_ref.startswith("data:image/"):
            return self._decode_data_image(raw_ref)
        if raw_ref.startswith("base64://"):
            return self._decode_base64_image(raw_ref.removeprefix("base64://"))
        if raw_ref.startswith(("http://", "https://")):
            return await asyncio.to_thread(self._download_remote_image, raw_ref)
        return self._resolve_local_path(raw_ref).read_bytes()

    def _resolve_local_path(self, raw_path: str) -> Path:
        if raw_path.startswith("file://"):
            parsed = urlparse(raw_path)
            return Path(parsed.path).expanduser().resolve(strict=False)
        return Path(raw_path).expanduser().resolve(strict=False)

    def _is_single_image_message(self, message: Message) -> bool:
        return (
            not str(message.text or "").strip()
            and len(message.attachments) == 1
            and message.attachments[0].type == "image"
            and not str(message.file or "").strip()
            and not str(message.audio or "").strip()
        )

    def _decode_data_image(self, image_ref: str) -> bytes:
        _, _, payload = image_ref.partition(",")
        return self._decode_base64_image(payload)

    def _decode_base64_image(self, payload: str) -> bytes:
        import base64

        return base64.b64decode(payload.encode("utf-8"), validate=False)

    def _download_remote_image(self, image_url: str) -> bytes:
        request = urllib_request.Request(url=image_url, method="GET")
        with urllib_request.urlopen(request, timeout=10.0) as response:
            return response.read()

    def _prepend_source_prefix(self, message: Message, text: str) -> str:
        source = str(message.channel or "").strip().lower()
        if not text.strip():
            return text
        if source in {"", "weixin", "system"}:
            return text
        prefix_map = {
            "webui": "[WebUI] ",
            "qq": "[QQ] ",
        }
        prefix = prefix_map.get(source, f"[{source.upper()}] ")
        if text.startswith(prefix):
            return text
        return f"{prefix}{text}"

    def _resolve_target_user_id(self, message: Message) -> str:
        explicit_target = self.target_user_id.strip()
        if explicit_target:
            return explicit_target
        client_user_id = str(getattr(self.client, "user_id", "") or "").strip()
        if client_user_id:
            return client_user_id
        sender_id = str(message.sender_id or "").strip()
        if str(message.channel or "").strip().lower() == "weixin" and sender_id:
            return sender_id
        return ""

    def _record_message_sent(self) -> None:
        if callable(self._on_message_sent):
            self._on_message_sent()

    def _read_image_size(self, payload: bytes) -> tuple[int, int]:
        if payload.startswith(b"\x89PNG\r\n\x1a\n") and len(payload) >= 24:
            width = int.from_bytes(payload[16:20], "big")
            height = int.from_bytes(payload[20:24], "big")
            return max(1, width), max(1, height)
        if payload.startswith((b"GIF87a", b"GIF89a")) and len(payload) >= 10:
            width = int.from_bytes(payload[6:8], "little")
            height = int.from_bytes(payload[8:10], "little")
            return max(1, width), max(1, height)
        if payload.startswith(b"\xff\xd8"):
            width, height = self._read_jpeg_size(payload)
            return max(1, width), max(1, height)
        return (1, 1)

    def _read_jpeg_size(self, payload: bytes) -> tuple[int, int]:
        index = 2
        length = len(payload)
        while index + 9 < length:
            if payload[index] != 0xFF:
                index += 1
                continue
            marker = payload[index + 1]
            index += 2
            if marker in {0xD8, 0xD9}:
                continue
            if index + 2 > length:
                break
            block_len = int.from_bytes(payload[index : index + 2], "big")
            if block_len < 2 or index + block_len > length:
                break
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                if index + 7 >= length:
                    break
                height = int.from_bytes(payload[index + 3 : index + 5], "big")
                width = int.from_bytes(payload[index + 5 : index + 7], "big")
                return width, height
            index += block_len
        return (1, 1)
