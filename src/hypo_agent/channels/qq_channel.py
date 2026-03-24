from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
import re
from typing import Any

import structlog

from hypo_agent.channels.onebot11 import parse_onebot_private_message
from hypo_agent.channels.qq_adapter import QQAdapter
from hypo_agent.core.time_utils import unix_seconds_to_utc_datetime, utc_now
from hypo_agent.core.uploads import build_upload_path, get_uploads_dir, guess_mime_type, sanitize_upload_filename
from hypo_agent.models import Attachment, Message

logger = structlog.get_logger("hypo_agent.channels.qq")

_CQ_SEGMENT_PATTERN = re.compile(r"\[CQ:(?P<type>[a-zA-Z0-9_]+)(?:,(?P<params>[^\]]*))?\]")


class QQChannelService:
    def __init__(
        self,
        *,
        napcat_http_url: str,
        napcat_http_token: str | None = None,
        image_renderer: Any | None = None,
        bot_qq: str,
        allowed_users: set[str],
        default_session_id: str = "main",
        on_message_sent: Any | None = None,
        uploads_dir: Path | str | None = None,
    ) -> None:
        self.adapter = QQAdapter(
            napcat_http_url=napcat_http_url,
            napcat_http_token=napcat_http_token,
            image_renderer=image_renderer,
        )
        self.bot_qq = str(bot_qq).strip()
        self.allowed_users = {item.strip() for item in allowed_users if item and item.strip()}
        self.default_session_id = default_session_id
        self._on_message_sent = on_message_sent
        self.uploads_dir = get_uploads_dir(uploads_dir)

    def is_allowed_user(self, user_id: str) -> bool:
        return user_id in self.allowed_users

    async def handle_onebot_event(self, payload: dict[str, Any], *, pipeline: Any) -> bool:
        parsed = parse_onebot_private_message(payload, bot_qq=self.bot_qq)
        if parsed is None:
            return False

        user_id = parsed.user_id
        if not self.is_allowed_user(user_id):
            logger.warning("qq.message.rejected", user_id=user_id, reason="not_in_whitelist")
            return False

        attachments = await self._download_attachments(parsed.raw_event)
        inbound = Message(
            text=parsed.text,
            sender="user",
            session_id=self.default_session_id,
            channel="qq",
            sender_id=user_id,
            timestamp=self._resolve_inbound_timestamp(parsed.raw_event),
            attachments=attachments,
        )
        callback = getattr(pipeline, "on_proactive_message", None)
        if callable(callback):
            try:
                result = callback(inbound, exclude_channels={"qq"})
            except TypeError:
                result = callback(inbound)
            if inspect.isawaitable(result):
                await result
        await self._run_pipeline_for_user(user_id=user_id, inbound=inbound, pipeline=pipeline)
        return True

    async def push_proactive(self, message: Message) -> None:
        await self.send_message(message)

    async def send_message(self, message: Message) -> None:
        outbound = self._prefixed_message(message)
        sender_id = str(message.sender_id or "").strip()
        if sender_id and sender_id in self.allowed_users:
            target_users = [sender_id]
        else:
            target_users = sorted(self.allowed_users)
        for user_id in target_users:
            success = await self.adapter.send_message(user_id=user_id, message=outbound)
            if success and callable(self._on_message_sent):
                self._on_message_sent()
            if not success:
                logger.warning(
                    "qq.message.send_failed",
                    user_id=user_id,
                    session_id=message.session_id,
                    source_channel=message.channel,
                )

    def get_runtime_status(self) -> dict[str, Any]:
        payload = self.adapter.get_status()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            return {
                "online": None,
                "good": None,
            }
        online = data.get("online")
        good = data.get("good")
        return {
            "online": online if isinstance(online, bool) else None,
            "good": good if isinstance(good, bool) else None,
        }

    def is_runtime_online(self) -> bool | None:
        online = self.get_runtime_status().get("online")
        return online if isinstance(online, bool) else None

    async def _run_pipeline_for_user(self, *, user_id: str, inbound: Message, pipeline: Any) -> None:
        async def emit(event: dict[str, Any]) -> None:
            event_type = str(event.get("type") or "")
            if event_type == "error":
                error_message = str(event.get("message") or "处理失败，请稍后重试")
                await self.adapter.send_message(
                    user_id=user_id,
                    message=Message(
                        text=error_message,
                        sender="assistant",
                        session_id=inbound.session_id,
                        channel="qq",
                        sender_id=user_id,
                    ),
                )
                if callable(self._on_message_sent):
                    self._on_message_sent()

        enqueue_user_message = getattr(pipeline, "enqueue_user_message", None)
        if callable(enqueue_user_message):
            result = enqueue_user_message(inbound, emit=emit)
            if inspect.isawaitable(result):
                await result
            return

        async for event in pipeline.stream_reply(inbound):
            await emit(event)

    def _resolve_inbound_timestamp(self, payload: dict[str, Any]) -> object:
        if payload.get("timestamp"):
            return payload.get("timestamp")
        return unix_seconds_to_utc_datetime(payload.get("time")) or utc_now()

    async def _download_attachments(self, payload: dict[str, Any]) -> list[Attachment]:
        attachments: list[Attachment] = []
        for segment in self._iter_message_segments(payload):
            if str(segment.get("type") or "").strip().lower() != "image":
                continue
            data = segment.get("data")
            if not isinstance(data, dict):
                data = {}
            source_url = str(
                data.get("url")
                or data.get("file_url")
                or data.get("src")
                or ""
            ).strip()
            if not source_url:
                continue
            filename = sanitize_upload_filename(
                data.get("file") or Path(source_url).name or "image.bin"
            )
            target_path = build_upload_path(filename, uploads_dir=self.uploads_dir)
            downloaded = await asyncio.to_thread(
                self.adapter.download_remote_file,
                url=source_url,
                target_path=str(target_path),
            )
            mime_type = guess_mime_type(
                filename,
                str(downloaded.get("mime_type") or "").strip() or None,
            )
            size_bytes = downloaded.get("size_bytes")
            attachments.append(
                Attachment(
                    type="image",
                    url=str(target_path),
                    filename=filename,
                    mime_type=mime_type,
                    size_bytes=int(size_bytes) if size_bytes is not None else None,
                )
            )
        return attachments

    def _iter_message_segments(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        message_value = payload.get("message")
        if isinstance(message_value, list):
            return [segment for segment in message_value if isinstance(segment, dict)]
        if isinstance(message_value, dict):
            return [message_value]
        if not isinstance(message_value, str):
            message_value = payload.get("raw_message")
        if not isinstance(message_value, str):
            return []
        return self._parse_cq_segments(message_value)

    def _parse_cq_segments(self, raw_message: str) -> list[dict[str, Any]]:
        segments: list[dict[str, Any]] = []
        for match in _CQ_SEGMENT_PATTERN.finditer(raw_message):
            params = str(match.group("params") or "")
            data: dict[str, str] = {}
            for chunk in params.split(","):
                key, sep, value = chunk.partition("=")
                if not sep:
                    continue
                data[key.strip()] = value.strip()
            segments.append(
                {
                    "type": str(match.group("type") or "").strip().lower(),
                    "data": data,
                }
            )
        return segments

    def _prefixed_message(self, message: Message) -> Message:
        source = str(message.channel or "").strip().lower()
        if source in {"", "qq", "system"}:
            return message
        prefix_map = {
            "weixin": "[微信] ",
        }
        prefix = prefix_map.get(source, "")
        text = str(message.text or "")
        if not prefix or not text.strip() or text.startswith(prefix):
            return message
        return message.model_copy(update={"text": f"{prefix}{text}"})
