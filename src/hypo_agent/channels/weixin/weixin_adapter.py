from __future__ import annotations

import asyncio
import re
from pathlib import Path
from urllib import request as urllib_request
from urllib.parse import urlparse

import httpx
import structlog

from hypo_agent.channels.qq_adapter import QQAdapter
from hypo_agent.channels.weixin.crypto import (
    encode_aes_key_base64hex,
    encode_aes_key_hex,
    encrypt_media,
    generate_aes_key,
)
from hypo_agent.channels.weixin.ilink_client import ILinkAPIError, ILinkClient
from hypo_agent.core.platform_message_preparation import prepare_message_for_platform
from hypo_agent.models import Attachment, Message

logger = structlog.get_logger("hypo_agent.channels.weixin.adapter")
_WEIXIN_RETRY_SPLIT_BYTES = 96
_WEIXIN_IMAGE_RETRY_DELAYS = (0.5, 1.0)
_WEIXIN_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WEIXIN_NON_BMP_RE = re.compile(r"[\U00010000-\U0010FFFF]")


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
        self._last_context_token = ""
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
        context_token = await self._resolve_context_token(message)
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
        allow_image_upload = bool(context_token) or len(prepared_messages) <= 1

        for message_index, prepared_message in enumerate(prepared_messages):
            if message_index > 0 and self.send_delay_seconds > 0:
                await self._sleep(self.send_delay_seconds)
            try:
                if self._is_single_image_message(prepared_message):
                    attachment = prepared_message.attachments[0]
                    if not allow_image_upload:
                        fallback_text = self._image_fallback_text(str(attachment.url or ""))
                        logger.info(
                            "weixin.adapter.image_degraded_without_context",
                            target_user_id=target_user_id,
                            image_ref=str(attachment.url or ""),
                            fallback_text=fallback_text,
                        )
                        await self._send_text_chunk(
                            target_user_id=target_user_id,
                            text=fallback_text,
                            context_token=context_token,
                        )
                        continue
                    try:
                        await self._send_attachment_image(
                            attachment,
                            target_user_id=target_user_id,
                            context_token=context_token,
                        )
                    except Exception as exc:
                        fallback_text = self._image_fallback_text(str(attachment.url or ""))
                        logger.warning(
                            "weixin.adapter.image_fallback",
                            target_user_id=target_user_id,
                            image_ref=str(attachment.url or ""),
                            fallback_text=fallback_text,
                            error=str(exc),
                        )
                        await self._send_text_chunk(
                            target_user_id=target_user_id,
                            text=fallback_text,
                            context_token=context_token,
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
                    await self._send_segment(
                        segment,
                        target_user_id=target_user_id,
                        context_token=context_token,
                    )
                await self._remember_context_token(context_token)
            except Exception as exc:
                logger.exception(
                    "weixin.adapter.message_failed",
                    message_text=prepared_message.text,
                    text_length=len(str(prepared_message.text or "")),
                    text_bytes=len(str(prepared_message.text or "").encode("utf-8")),
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

    async def _send_segment(self, segment: dict, *, target_user_id: str, context_token: str) -> None:
        segment_type = str(segment.get("type") or "").strip().lower()
        if segment_type == "text":
            text = str(segment.get("data", {}).get("text") or "")
            chunks = [chunk for chunk in self._split_message(text, limit=self.message_limit) if chunk.strip()]
            for index, chunk in enumerate(chunks):
                if index > 0 and self.send_delay_seconds > 0:
                    await self._sleep(self.send_delay_seconds)
                if not chunk.strip():
                    continue
                await self._send_text_chunk(
                    target_user_id=target_user_id,
                    text=chunk,
                    context_token=context_token,
                )
            return

        if segment_type == "image":
            raw = str(segment.get("data", {}).get("file") or "").strip()
            if not raw:
                return
            try:
                await self._send_image_reference(
                    raw,
                    target_user_id=target_user_id,
                    context_token=context_token,
                )
            except Exception as exc:
                fallback_text = self._image_fallback_text(raw)
                logger.warning(
                    "weixin.adapter.image_fallback",
                    target_user_id=target_user_id,
                    image_ref=raw,
                    fallback_text=fallback_text,
                    error=str(exc),
                )
                await self._send_text_chunk(
                    target_user_id=target_user_id,
                    text=fallback_text,
                    context_token=context_token,
                )
            return

        if segment_type == "file":
            data = dict(segment.get("data") or {})
            label = str(data.get("name") or Path(str(data.get("file") or "")).name or "file").strip()
            await self._send_text_chunk(
                target_user_id=target_user_id,
                text=f"[文件] {label}",
                context_token=context_token,
            )

    async def _send_attachment_image(
        self,
        attachment: Attachment,
        *,
        target_user_id: str,
        context_token: str,
    ) -> None:
        await self._send_image_reference(
            str(attachment.url or "").strip(),
            target_user_id=target_user_id,
            context_token=context_token,
        )

    async def _send_image_reference(
        self,
        image_ref: str,
        *,
        target_user_id: str,
        context_token: str,
    ) -> None:
        raw = await self._load_image_bytes(image_ref)
        aes_key = generate_aes_key()
        encrypted = encrypt_media(raw, aes_key)
        aes_key_hex = encode_aes_key_hex(aes_key)
        upload_request = self.client.build_media_upload_payload(
            to_user_id=target_user_id,
            media_type=1,
            plaintext=raw,
            encrypted_size=len(encrypted),
            aes_key_hex=aes_key_hex,
        )
        upload_payload = await self._retry_image_operation(
            stage="get_upload_url",
            target_user_id=target_user_id,
            image_ref=image_ref,
            operation=lambda: self.client.get_upload_url(**upload_request),
        )
        upload_param = str(upload_payload.get("upload_param") or "").strip()
        filekey = str(upload_request.get("filekey") or "").strip()
        if not upload_param or not filekey:
            raise RuntimeError("weixin upload response missing upload_param or filekey")
        aes_key_base64 = encode_aes_key_base64hex(aes_key)
        encrypt_query_param = await self._retry_image_operation(
            stage="upload_media",
            target_user_id=target_user_id,
            image_ref=image_ref,
            operation=lambda: self.client.upload_media(
                upload_param=upload_param,
                filekey=filekey,
                encrypted_data=encrypted,
            ),
        )
        await self._retry_image_operation(
            stage="send_image",
            target_user_id=target_user_id,
            image_ref=image_ref,
            operation=lambda: self.client.send_image(
                to_user_id=target_user_id,
                encrypt_query_param=encrypt_query_param,
                aes_key=aes_key_base64,
                encrypted_file_size=len(encrypted),
                context_token=context_token or None,
            ),
        )
        await self._remember_context_token(context_token)
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

    def _image_fallback_text(self, image_ref: str) -> str:
        raw_ref = str(image_ref or "").strip()
        if not raw_ref:
            return "[图片]"
        if raw_ref.startswith(("data:image/", "base64://")):
            return "[图片]"
        if raw_ref.startswith(("http://", "https://")):
            parsed = urlparse(raw_ref)
            name = Path(parsed.path).name
            return f"[图片] {name}" if name else "[图片]"
        return f"[图片] {self._resolve_local_path(raw_ref).name or 'image'}"

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

    async def _resolve_context_token(self, message: Message) -> str:
        metadata = message.metadata if isinstance(message.metadata, dict) else {}
        weixin_meta = metadata.get("weixin")
        if isinstance(weixin_meta, dict):
            explicit = str(weixin_meta.get("context_token") or "").strip()
            if explicit:
                await self._remember_context_token(explicit)
                return explicit
        client_context_token = str(getattr(self.client, "last_context_token", "") or "").strip()
        if client_context_token:
            self._last_context_token = client_context_token
            return client_context_token
        return self._last_context_token

    async def _remember_context_token(self, context_token: str) -> None:
        normalized = str(context_token or "").strip()
        if normalized:
            self._last_context_token = normalized
            remember_context_token = getattr(self.client, "remember_context_token", None)
            if callable(remember_context_token):
                remember_context_token(normalized)

    def _record_message_sent(self) -> None:
        if callable(self._on_message_sent):
            self._on_message_sent()

    async def _retry_image_operation(
        self,
        *,
        stage: str,
        target_user_id: str,
        image_ref: str,
        operation,
    ):
        last_error: Exception | None = None
        for attempt in range(len(_WEIXIN_IMAGE_RETRY_DELAYS) + 1):
            try:
                return await operation()
            except Exception as exc:
                last_error = exc
                if attempt >= len(_WEIXIN_IMAGE_RETRY_DELAYS) or not self._is_retryable_image_error(exc):
                    raise
                delay = _WEIXIN_IMAGE_RETRY_DELAYS[attempt]
                logger.warning(
                    "weixin.adapter.image_retry",
                    stage=stage,
                    attempt=attempt + 1,
                    backoff_seconds=delay,
                    target_user_id=target_user_id,
                    image_ref=image_ref,
                    error=str(exc),
                )
                await self._sleep(delay)
        if last_error is not None:
            raise last_error
        raise AssertionError("unreachable")

    async def _send_text_chunk(self, *, target_user_id: str, text: str, context_token: str) -> None:
        normalized = str(text or "").strip()
        if not normalized:
            return

        try:
            await self.client.send_message(
                to_user_id=target_user_id,
                text=normalized,
                context_token=context_token or None,
            )
        except ILinkAPIError as exc:
            await self._handle_retryable_text_failure(
                target_user_id=target_user_id,
                text=normalized,
                context_token=context_token,
                error=exc,
            )
            return

        self._record_message_sent()

    async def _handle_retryable_text_failure(
        self,
        *,
        target_user_id: str,
        text: str,
        context_token: str,
        error: ILinkAPIError,
    ) -> None:
        if not self._is_retryable_send_error(error):
            raise error

        text_bytes = len(text.encode("utf-8"))
        logger.warning(
            "weixin.adapter.send_retry",
            strategy="omit_context_token",
            target_user_id=target_user_id,
            ret=error.response.get("ret"),
            errcode=error.response.get("errcode"),
            text_length=len(text),
            text_bytes=text_bytes,
        )
        try:
            await self.client.send_message(
                to_user_id=target_user_id,
                text=text,
                context_token=None,
            )
        except ILinkAPIError as retry_error:
            if not self._is_retryable_send_error(retry_error):
                raise retry_error
            fallback_text = self._sanitize_text_for_retry(text)
            if fallback_text != text:
                logger.warning(
                    "weixin.adapter.send_retry",
                    strategy="sanitize_text",
                    target_user_id=target_user_id,
                    ret=retry_error.response.get("ret"),
                    errcode=retry_error.response.get("errcode"),
                    text_length=len(fallback_text),
                    text_bytes=len(fallback_text.encode("utf-8")),
                )
                try:
                    await self.client.send_message(
                        to_user_id=target_user_id,
                        text=fallback_text,
                        context_token=None,
                    )
                except ILinkAPIError as sanitized_error:
                    if not self._is_retryable_send_error(sanitized_error):
                        raise sanitized_error
                    await self._send_split_text_fallback(
                        target_user_id=target_user_id,
                        text=fallback_text,
                        error=sanitized_error,
                    )
                else:
                    self._record_message_sent()
                return

            await self._send_split_text_fallback(
                target_user_id=target_user_id,
                text=fallback_text,
                error=retry_error,
            )
            return

        self._record_message_sent()

    async def _send_split_text_fallback(
        self,
        *,
        target_user_id: str,
        text: str,
        error: ILinkAPIError,
    ) -> None:
        normalized = str(text or "").strip()
        parts = self._split_text_by_utf8_bytes(normalized, limit_bytes=_WEIXIN_RETRY_SPLIT_BYTES)
        if len(parts) <= 1:
            raise error

        logger.warning(
            "weixin.adapter.send_split_fallback",
            target_user_id=target_user_id,
            ret=error.response.get("ret"),
            errcode=error.response.get("errcode"),
            part_count=len(parts),
            source_text_bytes=len(normalized.encode("utf-8")),
            limit_bytes=_WEIXIN_RETRY_SPLIT_BYTES,
        )
        for index, part in enumerate(parts):
            if index > 0 and self.send_delay_seconds > 0:
                await self._sleep(self.send_delay_seconds)
            await self.client.send_message(
                to_user_id=target_user_id,
                text=part,
                context_token=None,
            )
            self._record_message_sent()

    def _sanitize_text_for_retry(self, text: str) -> str:
        normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
        normalized = normalized.replace("`", "")
        normalized = normalized.replace("【", "").replace("】", "")
        normalized = normalized.replace("『", "").replace("』", "")
        normalized = _WEIXIN_CONTROL_CHAR_RE.sub("", normalized)
        normalized = _WEIXIN_NON_BMP_RE.sub("", normalized)
        normalized = re.sub(r"\n{3,}", "\n\n", normalized)
        normalized = re.sub(r"[ \t]+\n", "\n", normalized)
        return normalized.strip()

    def _is_retryable_send_error(self, error: ILinkAPIError) -> bool:
        ret = error.response.get("ret")
        errcode = error.response.get("errcode")
        return ret == -2 and errcode is None

    def _is_retryable_image_error(self, error: Exception) -> bool:
        if isinstance(error, ILinkAPIError):
            return self._is_retryable_send_error(error)
        if isinstance(error, (httpx.NetworkError, httpx.TimeoutException, httpx.HTTPStatusError)):
            return True
        return False

    def _split_text_by_utf8_bytes(self, text: str, *, limit_bytes: int) -> list[str]:
        normalized = str(text or "")
        if not normalized:
            return []
        safe_limit = max(16, int(limit_bytes))
        chunks: list[str] = []
        current = ""

        def flush() -> None:
            nonlocal current
            if current:
                chunks.append(current)
                current = ""

        for line in normalized.splitlines(keepends=True):
            if len(line.encode("utf-8")) > safe_limit:
                flush()
                chunks.extend(self._split_overflow_line(line, limit_bytes=safe_limit))
                continue
            if current and len((current + line).encode("utf-8")) > safe_limit:
                flush()
            current += line
        flush()
        return [chunk.strip() for chunk in chunks if chunk.strip()]

    def _split_overflow_line(self, text: str, *, limit_bytes: int) -> list[str]:
        parts: list[str] = []
        current = ""
        for char in text:
            if current and len((current + char).encode("utf-8")) > limit_bytes:
                parts.append(current)
                current = char
                continue
            current += char
        if current:
            parts.append(current)
        return parts

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
