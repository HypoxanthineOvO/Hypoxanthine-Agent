from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import inspect
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

import structlog

from hypo_agent.channels.weixin.crypto import decrypt_media
from hypo_agent.channels.weixin.ilink_client import ILinkAPIError, ILinkClient, SessionExpiredError
from hypo_agent.core.time_utils import utc_isoformat, utc_now
from hypo_agent.core.uploads import build_upload_path, get_uploads_dir, guess_mime_type
from hypo_agent.models import Attachment, Message, WeixinServiceConfig

logger = structlog.get_logger("hypo_agent.channels.weixin.channel")
_WEIXIN_CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"
_WEIXIN_CHANNEL_ERRORS = (ILinkAPIError, OSError, RuntimeError, TypeError, ValueError)


class WeixinChannel:
    def __init__(
        self,
        *,
        config: WeixinServiceConfig | dict[str, Any],
        message_queue: Any,
        build_message: Callable[..., Message],
        client_factory: Callable[[], ILinkClient] | None = None,
        inbound_callback_getter: Callable[[], Any | None] | None = None,
        sleep_func=asyncio.sleep,
        uploads_dir: Path | str | None = None,
    ) -> None:
        self.config = (
            config
            if isinstance(config, WeixinServiceConfig)
            else WeixinServiceConfig.model_validate(config)
        )
        self.queue = message_queue
        self.build_message = build_message
        self.client_factory = client_factory or (
            lambda: ILinkClient(
                base_url="https://ilinkai.weixin.qq.com",
                token_path=self.config.token_path,
            )
        )
        self._get_inbound_callback = inbound_callback_getter or (lambda: None)
        self._sleep = sleep_func
        self.uploads_dir = get_uploads_dir(uploads_dir)
        self.client: ILinkClient | None = None
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._last_message_at: str | None = None
        self._messages_received = 0
        self._messages_sent = 0
        self._session_expired = False

    async def start(self) -> None:
        if self.client is None:
            self.client = self.client_factory()
        if not str(getattr(self.client, "bot_token", "") or "").strip():
            logger.warning(
                "weixin.channel.no_token",
                hint="Run scripts/demo_weixin.py to scan QR first",
                token_path=self.config.token_path,
            )
            return
        if self._task is not None and not self._task.done():
            return
        self._session_expired = False
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info(
            "weixin.channel.started",
            bot_id=str(getattr(self.client, "bot_id", "") or ""),
            user_id=str(getattr(self.client, "user_id", "") or ""),
        )

    async def stop(self) -> None:
        self._running = False
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if self.client is not None:
            await self.client.close()
        logger.info("weixin.channel.stopped")

    async def _poll_loop(self) -> None:
        backoff = [1.0, 2.0, 4.0, 8.0, 16.0, 30.0]
        retry_count = 0
        assert self.client is not None
        while self._running:
            try:
                messages = await self.client.get_updates()
                retry_count = 0
                for message in messages:
                    await self._handle_message(message)
            except asyncio.CancelledError:
                raise
            except SessionExpiredError:
                logger.error(
                    "weixin.channel.session_expired",
                    hint="Token expired, need re-login via demo_weixin.py",
                )
                self._session_expired = True
                self._running = False
                break
            except _WEIXIN_CHANNEL_ERRORS as exc:
                delay = backoff[min(retry_count, len(backoff) - 1)]
                logger.warning(
                    "weixin.channel.poll_error",
                    error=str(exc),
                    retry_in=delay,
                )
                await self._sleep(delay)
                retry_count += 1

    async def _handle_message(self, raw_msg: dict[str, Any]) -> None:
        from_user = str(raw_msg.get("from_user_id") or "").strip()
        allowed = [item.strip() for item in self.config.allowed_users if item and item.strip()]
        if allowed and from_user not in allowed:
            logger.warning(
                "weixin.channel.rejected",
                user=from_user,
                reason="not_in_allowed_users",
            )
            return
        self._sync_target_user(from_user)

        context_token = str(raw_msg.get("context_token") or "").strip()
        if context_token:
            self._sync_context_token(context_token)

        text_parts: list[str] = []
        attachments: list[Attachment] = []
        for item in raw_msg.get("item_list") or []:
            if not isinstance(item, dict):
                continue
            item_type = int(item.get("type") or 0)
            if item_type == 1:
                text_item = item.get("text_item")
                if not isinstance(text_item, dict):
                    continue
                text = str(text_item.get("text") or "")
                if text:
                    text_parts.append(text)
                continue
            if item_type == 2:
                attachment = await self._download_attachment(
                    item.get("image_item"),
                    fallback_name="image.png",
                    media_kind="image",
                )
                if attachment is not None:
                    attachments.append(attachment.model_copy(update={"type": "image"}))
                else:
                    text_parts.append("[用户发送了一张图片，但下载失败]")
                continue
            if item_type == 3:
                transcript = self._extract_voice_transcript(item)
                if transcript:
                    text_parts.append(transcript)
                else:
                    logger.error("weixin.channel.voice_without_text", user=from_user)
                    text_parts.append("[用户发送了一条语音，但转写失败]")
                continue
            if item_type == 4:
                attachment = await self._download_attachment(
                    item.get("file_item"),
                    fallback_name="file.bin",
                    media_kind="file",
                )
                if attachment is not None:
                    attachments.append(attachment)
                else:
                    text_parts.append("[用户发送了一个文件，但下载失败]")
                continue
            if item_type == 5:
                attachment = await self._download_attachment(
                    item.get("video_item"),
                    fallback_name="video.mp4",
                    media_kind="video",
                )
                if attachment is not None:
                    attachments.append(attachment.model_copy(update={"type": "video"}))
                else:
                    text_parts.append("[用户发送了一段视频，但下载失败]")
                continue

        text = "\n".join(part.strip() for part in text_parts if part and part.strip()).strip()
        if not text and not attachments:
            logger.info("weixin.channel.empty_message_skipped", user=from_user)
            return

        message = self.build_message(
            text=text or None,
            sender="user",
            session_id="main",
            channel="weixin",
            sender_id=from_user,
            attachments=attachments,
            metadata={
                "weixin": {
                    "context_token": context_token,
                }
            },
        )

        callback = self._get_inbound_callback()
        if callable(callback):
            try:
                result = callback(message, message_type="user_message")
            except TypeError:
                result = callback(message)
            if inspect.isawaitable(result):
                await result

        await self.queue.put(
            {
                "event_type": "user_message",
                "message": message,
                "emit": self._make_emit_callback(from_user, context_token=context_token),
            }
        )
        self._messages_received += 1
        self._last_message_at = utc_isoformat(utc_now())
        logger.info("weixin.channel.enqueued", user=from_user, text_len=len(text))

    def _make_emit_callback(self, user_id: str, *, context_token: str):
        typing_started = False

        async def emit(event: dict[str, Any]) -> None:
            nonlocal typing_started
            if self.client is None:
                return

            event_type = str(event.get("type") or "").strip().lower()
            if event_type == "assistant_chunk" and not typing_started:
                typing_started = True
                try:
                    await self.client.send_typing(user_id, status=1)
                except _WEIXIN_CHANNEL_ERRORS:
                    logger.warning("weixin.channel.typing_failed", user_id=user_id, exc_info=True)
                return

            if event_type == "assistant_done" and typing_started:
                try:
                    await self.client.send_typing(user_id, status=2)
                except _WEIXIN_CHANNEL_ERRORS:
                    logger.warning(
                        "weixin.channel.typing_stop_failed",
                        user_id=user_id,
                        exc_info=True,
                    )
                return

            if event_type == "error":
                if typing_started:
                    try:
                        await self.client.send_typing(user_id, status=2)
                    except _WEIXIN_CHANNEL_ERRORS:
                        logger.warning(
                            "weixin.channel.typing_stop_failed",
                            user_id=user_id,
                            exc_info=True,
                        )
                text = str(event.get("message") or "处理失败，请稍后重试").strip()
                if text:
                    await self.client.send_message(
                        to_user_id=user_id,
                        text=text,
                        context_token=context_token or None,
                    )
                    self.record_message_sent()

        return emit

    def record_message_sent(self) -> None:
        self._messages_sent += 1
        self._last_message_at = utc_isoformat(utc_now())

    def get_status(self) -> dict[str, Any]:
        client = self.client
        token = str(getattr(client, "bot_token", "") or "").strip()
        status = "disabled"
        if self._session_expired:
            status = "error"
        elif not token:
            status = "no_token"
        elif self._running:
            status = "connected"
        else:
            status = "disconnected"
        return {
            "status": status,
            "bot_id": str(getattr(client, "bot_id", "") or ""),
            "user_id": str(getattr(client, "user_id", "") or ""),
            "last_message_at": self._last_message_at,
            "messages_received": self._messages_received,
            "messages_sent": self._messages_sent,
        }

    def _extract_voice_transcript(self, item: dict[str, Any]) -> str:
        voice_item = item.get("voice_item")
        if isinstance(voice_item, dict):
            text = str(voice_item.get("text") or "").strip()
            if text:
                return text
        text_item = item.get("text_item")
        if isinstance(text_item, dict):
            return str(text_item.get("text") or "").strip()
        return ""

    async def _download_attachment(
        self,
        item_payload: Any,
        *,
        fallback_name: str,
        media_kind: str,
    ) -> Attachment | None:
        if self.client is None or not isinstance(item_payload, dict):
            logger.error(
                "weixin.channel.attachment_payload_invalid",
                media_kind=media_kind,
                payload_type=type(item_payload).__name__,
            )
            return None
        source_url, aes_key, metadata = self._resolve_attachment_source(item_payload)
        if not source_url:
            logger.error(
                "weixin.channel.attachment_metadata_missing",
                media_kind=media_kind,
                url=str(item_payload.get("url") or "").strip(),
                aes_key_length=len(str(item_payload.get("aes_key") or "").strip()),
                has_media=metadata["has_media"],
                has_encrypt_query_param=metadata["has_encrypt_query_param"],
                has_aeskey=metadata["has_aeskey"],
                has_media_aes_key=metadata["has_media_aes_key"],
            )
            return None

        try:
            encrypted = await self.client.download_media(source_url)
            raw = self._decode_downloaded_media(
                encrypted=encrypted,
                aes_key=aes_key,
                source_url=source_url,
                media_kind=media_kind,
            )
        except _WEIXIN_CHANNEL_ERRORS as exc:
            logger.exception(
                "weixin.channel.attachment_download_failed",
                media_kind=media_kind,
                url=source_url,
                aes_key_length=len(aes_key or b""),
                error=str(exc),
            )
            return None

        candidate_name = str(item_payload.get("file_name") or Path(source_url).name or "").strip()
        filename = candidate_name if Path(candidate_name).suffix else fallback_name
        target_path = build_upload_path(filename or fallback_name, uploads_dir=self.uploads_dir)
        target_path.write_bytes(raw)
        mime_type = guess_mime_type(target_path.name, str(item_payload.get("mime_type") or "").strip() or None)
        attachment_type = self._attachment_type_for_name(target_path.name, mime_type)
        size_bytes = item_payload.get("file_size")
        return Attachment(
            type=attachment_type,
            url=str(target_path),
            filename=filename or target_path.name,
            mime_type=mime_type,
            size_bytes=int(size_bytes) if size_bytes is not None else len(raw),
        )

    def _decode_downloaded_media(
        self,
        *,
        encrypted: bytes,
        aes_key: bytes | None,
        source_url: str,
        media_kind: str,
    ) -> bytes:
        payload = bytes(encrypted)
        if not payload:
            return b""
        if aes_key is None:
            logger.warning(
                "weixin.channel.attachment_missing_aes_key_fallback",
                media_kind=media_kind,
                url=source_url,
                size_bytes=len(payload),
            )
            return payload
        if len(payload) % 16 != 0:
            logger.warning(
                "weixin.channel.attachment_plain_payload_fallback",
                media_kind=media_kind,
                url=source_url,
                size_bytes=len(payload),
                reason="payload_not_block_aligned",
            )
            return payload
        return decrypt_media(payload, aes_key)

    def _resolve_attachment_source(self, item_payload: dict[str, Any]) -> tuple[str, bytes | None, dict[str, bool]]:
        media = item_payload.get("media")
        media_payload = media if isinstance(media, dict) else {}

        encrypt_query_param = str(media_payload.get("encrypt_query_param") or "").strip()
        legacy_url = str(item_payload.get("url") or "").strip()
        source_ref = encrypt_query_param or legacy_url
        if source_ref and not self._is_absolute_url(source_ref):
            source_url = self._build_cdn_download_url(source_ref)
        else:
            source_url = source_ref

        aes_key: bytes | None = None
        for raw_value in (
            item_payload.get("aes_key"),
            item_payload.get("aeskey"),
            media_payload.get("aes_key"),
        ):
            if raw_value in (None, ""):
                continue
            aes_key = self._parse_aes_key(raw_value)
            break

        metadata = {
            "has_media": bool(media_payload),
            "has_encrypt_query_param": bool(encrypt_query_param),
            "has_aeskey": bool(str(item_payload.get("aeskey") or "").strip()),
            "has_media_aes_key": bool(str(media_payload.get("aes_key") or "").strip()),
        }
        return source_url, aes_key, metadata

    def _parse_aes_key(self, raw_value: Any) -> bytes:
        text = str(raw_value or "").strip()
        if not text:
            raise ValueError("empty aes key")
        if len(text) == 32:
            try:
                return bytes.fromhex(text)
            except ValueError:
                pass
        try:
            decoded = base64.b64decode(text, validate=True)
        except binascii.Error as exc:
            raise ValueError(f"invalid aes key encoding: {text[:16]}...") from exc
        if len(decoded) == 16:
            return decoded
        if len(decoded) == 32:
            try:
                return bytes.fromhex(decoded.decode("ascii"))
            except ValueError as exc:
                raise ValueError("base64 aes key did not contain hex bytes") from exc
        raise ValueError(f"aes key decoded to unexpected length: {len(decoded)}")

    def _build_cdn_download_url(self, encrypted_query_param: str) -> str:
        return (
            f"{_WEIXIN_CDN_BASE_URL}/download"
            f"?encrypted_query_param={quote(encrypted_query_param, safe='')}"
        )

    def _is_absolute_url(self, value: str) -> bool:
        lowered = value.lower()
        return lowered.startswith("https://") or lowered.startswith("http://")

    def _sync_target_user(self, from_user: str) -> None:
        if self.client is None or not from_user:
            return
        current_user = str(getattr(self.client, "user_id", "") or "").strip()
        if current_user == from_user:
            return
        remember_user_id = getattr(self.client, "remember_user_id", None)
        if callable(remember_user_id):
            remember_user_id(from_user)
        else:
            self.client.user_id = from_user
        if current_user:
            logger.info(
                "weixin.channel.target_user_updated",
                previous_user=current_user,
                new_user=from_user,
            )
            return
        logger.info("weixin.channel.target_user_learned", user_id=from_user)

    def _sync_context_token(self, context_token: str) -> None:
        if self.client is None or not context_token:
            return
        remember_context_token = getattr(self.client, "remember_context_token", None)
        if callable(remember_context_token):
            remember_context_token(context_token)
            return
        self.client.last_context_token = context_token  # type: ignore[attr-defined]

    def _attachment_type_for_name(self, filename: str, mime_type: str) -> str:
        lowered = mime_type.lower()
        if lowered.startswith("image/"):
            return "image"
        if lowered.startswith("audio/"):
            return "audio"
        if lowered.startswith("video/"):
            return "video"
        suffix = Path(filename).suffix.lower()
        if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
            return "image"
        if suffix in {".mp4", ".mov", ".mkv", ".avi", ".webm"}:
            return "video"
        if suffix in {".mp3", ".wav", ".m4a", ".ogg", ".aac"}:
            return "audio"
        return "file"
