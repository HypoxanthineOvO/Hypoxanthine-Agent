from __future__ import annotations

# DEPRECATED: 原 QQ Bot Webhook 接入已被 WebSocket 长连接模式取代（MQ 迁移，2026-03-26）
# 保留此文件中的共享 REST / 事件适配逻辑用于兼容与 fallback，不再主动维护 webhook 模式。

import asyncio
import base64
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import inspect
import json
import mimetypes
from pathlib import Path
import random
import re
from typing import Any
from urllib.parse import urlencode

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import httpx
import structlog

from hypo_agent.core.delivery import DeliveryResult
from hypo_agent.core.qq_renderer import QQRenderer
from hypo_agent.core.uploads import build_upload_path
from hypo_agent.core.time_utils import normalize_utc_datetime, utc_now
from hypo_agent.core.unified_message import UnifiedMessage, message_from_unified
from hypo_agent.models import Message

logger = structlog.get_logger("hypo_agent.channels.qq_bot")
_QQ_BOT_DELIVERY_ERRORS = (
    httpx.HTTPStatusError,
    httpx.TransportError,
    httpx.TimeoutException,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)

_TOKEN_CACHE: dict[str, tuple[str, datetime]] = {}
_TOKEN_LOCKS: dict[str, asyncio.Lock] = {}
_MENTION_PREFIX_PATTERN = re.compile(r"^(?:<@!?[^>]+>\s*)+")
_HTTP_URL_PATTERN = re.compile(r"^https?://", re.IGNORECASE)
_DATA_URL_PATTERN = re.compile(r"^data:[^;]+;base64,", re.IGNORECASE)


def clear_qqbot_token_cache() -> None:
    _TOKEN_CACHE.clear()


def _token_lock_for(app_id: str) -> asyncio.Lock:
    lock = _TOKEN_LOCKS.get(app_id)
    if lock is None:
        lock = asyncio.Lock()
        _TOKEN_LOCKS[app_id] = lock
    return lock


def _derive_secret_seed(secret: str) -> bytes:
    raw = secret.encode("utf-8")
    if not raw:
        raise ValueError("qq bot secret is empty")
    repeated = (raw * ((32 // len(raw)) + 1))[:32]
    return repeated


def _derive_private_key(secret: str) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(_derive_secret_seed(secret))


def _parse_timestamp(value: str | None) -> datetime:
    if not value:
        return utc_now()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return utc_now()
    return normalize_utc_datetime(parsed) or utc_now()


def _next_msg_seq(msg_id: str | None) -> int:
    if not msg_id:
        return 1
    return ((int(datetime.now(UTC).timestamp() * 1000) % 100000000) ^ random.randint(0, 65535)) % 65536


def _normalize_content(content: str) -> str:
    normalized = _MENTION_PREFIX_PATTERN.sub("", content or "")
    return normalized.strip()


@dataclass(slots=True)
class QQBotInboundEvent:
    event_type: str
    openid: str
    content: str
    msg_id: str
    timestamp: datetime
    guild_id: str | None = None
    raw_event: dict[str, Any] | None = None


class QQBotChannelService:
    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        default_session_id: str = "main",
        on_message_sent: Any | None = None,
        load_target_openid: Any | None = None,
        save_target_openid: Any | None = None,
        api_base_url: str = "https://api.sgroup.qq.com",
        token_url: str = "https://bots.qq.com/app/getAppAccessToken",
        public_base_url: str = "",
        public_file_token: str = "",
        request_timeout_seconds: float = 15.0,
        image_renderer: Any | None = None,
    ) -> None:
        self.app_id = str(app_id).strip()
        self.app_secret = str(app_secret).strip()
        self.default_session_id = default_session_id
        self._on_message_sent = on_message_sent
        self._load_target_openid = load_target_openid
        self._save_target_openid = save_target_openid
        self.api_base_url = api_base_url.rstrip("/")
        self.token_url = token_url
        self.public_base_url = str(public_base_url or "").strip().rstrip("/")
        self.public_file_token = str(public_file_token or "").strip()
        self.request_timeout_seconds = max(1.0, request_timeout_seconds)
        self.renderer = QQRenderer(image_renderer=image_renderer)
        self._last_message_at: datetime | None = None
        self._messages_received = 0
        self._messages_sent = 0
        self._last_openid: str | None = None
        self._last_guild_id: str | None = None
        self._ws_connected = False
        self._connected_at: datetime | None = None
        self._last_delivery: DeliveryResult | None = None

    @staticmethod
    def build_signature_hex(*, secret: str, timestamp: str, body: bytes) -> str:
        private_key = _derive_private_key(secret)
        signature = private_key.sign(timestamp.encode("utf-8") + body)
        return signature.hex()

    @staticmethod
    def verify_signature(*, secret: str, timestamp: str, signature: str, body: bytes) -> bool:
        try:
            public_key = _derive_private_key(secret).public_key()
            signature_bytes = bytes.fromhex(signature)
            public_key.verify(signature_bytes, timestamp.encode("utf-8") + body)
            return True
        except (InvalidSignature, ValueError):
            return False

    async def handle_webhook_request(
        self,
        *,
        body: bytes,
        signature: str,
        timestamp: str,
        pipeline: Any,
    ) -> tuple[int, dict[str, Any]]:
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            return 400, {"detail": "invalid payload"}
        if not isinstance(payload, dict):
            return 400, {"detail": "invalid payload"}

        validation_response = self._build_validation_response(payload)
        if validation_response is not None:
            return 200, validation_response

        if not self.verify_signature(
            secret=self.app_secret,
            timestamp=timestamp,
            signature=signature,
            body=body,
        ):
            return 403, {"detail": "invalid signature"}

        handled = await self.handle_event(payload, pipeline=pipeline)
        return 200, {"ok": handled}

    async def handle_event(self, payload: dict[str, Any], *, pipeline: Any) -> bool:
        inbound_event = self._parse_inbound_event(payload)
        if inbound_event is None:
            return False

        self._last_openid = inbound_event.openid
        await self._remember_openid(inbound_event.openid)
        if inbound_event.guild_id:
            self._last_guild_id = inbound_event.guild_id
        self._last_message_at = inbound_event.timestamp
        self._messages_received += 1

        if inbound_event.event_type == "GROUP_AT_MESSAGE_CREATE":
            logger.warning(
                "qq_bot.group_at_message.received",
                msg_id=inbound_event.msg_id,
                openid=inbound_event.openid,
            )

        inbound = Message(
            text=inbound_event.content,
            sender="user",
            session_id=self.default_session_id,
            channel="qq",
            sender_id=inbound_event.openid,
            timestamp=inbound_event.timestamp,
            metadata={
                "qq": {
                    "backend": "qq_bot",
                    "event_type": inbound_event.event_type,
                    "msg_id": inbound_event.msg_id,
                    "openid": inbound_event.openid,
                    "guild_id": inbound_event.guild_id,
                }
            },
        )

        callback = getattr(pipeline, "on_proactive_message", None)
        if callable(callback):
            try:
                result = callback(inbound, message_type="user_message")
            except TypeError:
                result = callback(inbound)
            if inspect.isawaitable(result):
                await result

        await self._run_pipeline_for_user(
            openid=inbound_event.openid,
            guild_id=inbound_event.guild_id,
            inbound=inbound,
            pipeline=pipeline,
        )
        return True

    async def push_proactive(self, message: Message | UnifiedMessage) -> DeliveryResult:
        return await self.send_message(message)

    async def get_access_token(self) -> str:
        return await self._get_access_token()

    async def get_gateway_url(self, *, access_token: str | None = None) -> str:
        resolved_access_token = access_token or await self._get_access_token()
        payload = await self._request_json(
            "GET",
            f"{self.api_base_url}/gateway",
            access_token=resolved_access_token,
        )
        return str(payload.get("url") or "").strip()

    async def send_message(self, message: Message | UnifiedMessage) -> DeliveryResult:
        message = message_from_unified(message) if isinstance(message, UnifiedMessage) else message
        qq_meta = message.metadata.get("qq") if isinstance(message.metadata, dict) else {}
        if not isinstance(qq_meta, Mapping):
            qq_meta = {}

        openid = await self._resolve_openid(message=message, qq_meta=qq_meta)
        if not openid:
            logger.warning("qq_bot.message.skip", reason="missing_openid", session_id=message.session_id)
            result = DeliveryResult.failed("qq_bot", error="missing_openid")
            self._last_delivery = result
            return result

        guild_id = str(qq_meta.get("guild_id") or "").strip() or None
        msg_id = str(qq_meta.get("msg_id") or "").strip() or None
        route_kind = "dm" if guild_id else "c2c"
        pending_text = ""
        pending_segment_count = 0
        total_segment_count = 0
        delivered_segment_count = 0

        try:
            for prepared_message in [message]:
                rendered_segments = await self.renderer.render(prepared_message)
                total_segment_count += len(rendered_segments)
                if not rendered_segments:
                    continue
                for segment in rendered_segments:
                    segment_type = str(segment.get("type") or "").strip().lower()
                    if segment_type == "text":
                        pending_text = self._join_text_parts(pending_text, str(segment.get("text") or ""))
                        pending_segment_count += 1
                        continue
                    if segment_type == "file":
                        label = str(segment.get("name") or Path(str(segment.get("source") or "")).name or "file").strip()
                        pending_text = self._join_text_parts(pending_text, f"[文件] {label}")
                        pending_segment_count += 1
                        continue
                    if segment_type != "image":
                        continue

                    image_source = str(segment.get("source") or "").strip()
                    if pending_segment_count:
                        await self._send_with_retry(
                            route_kind=route_kind,
                            openid=openid,
                            guild_id=guild_id,
                            msg_id=msg_id,
                            text=pending_text,
                        )
                        delivered_segment_count += pending_segment_count
                        pending_text = ""
                        pending_segment_count = 0
                    if not image_source:
                        await self._send_with_retry(
                            route_kind=route_kind,
                            openid=openid,
                            guild_id=guild_id,
                            msg_id=msg_id,
                            text=self._segment_image_fallback_text(segment),
                        )
                        delivered_segment_count += 1
                        continue
                    await self._send_image_with_fallback(
                        route_kind=route_kind,
                        openid=openid,
                        guild_id=guild_id,
                        msg_id=msg_id,
                        text=None,
                        image_source=image_source,
                        fallback_text=self._segment_image_fallback_text(segment),
                    )
                    delivered_segment_count += 1

            if pending_segment_count:
                await self._send_with_retry(
                    route_kind=route_kind,
                    openid=openid,
                    guild_id=guild_id,
                    msg_id=msg_id,
                    text=pending_text,
                )
                delivered_segment_count += pending_segment_count
        except _QQ_BOT_DELIVERY_ERRORS as exc:
            result = DeliveryResult.failed(
                "qq_bot",
                segment_count=total_segment_count,
                failed_segments=max(1, total_segment_count - delivered_segment_count),
                error=str(exc),
            )
            self._last_delivery = result
            logger.warning(
                "qq_bot.message.delivery_failed",
                openid=openid,
                route_kind=route_kind,
                error=str(exc),
            )
            return result

        result = DeliveryResult.ok("qq_bot", segment_count=total_segment_count)
        self._last_delivery = result
        return result

    def get_status(self) -> dict[str, Any]:
        masked_app_id = ""
        if self.app_id:
            masked_app_id = f"••••{self.app_id[-4:]}" if len(self.app_id) >= 4 else "••••"
        status = "disabled"
        if self.app_id and self.app_secret:
            status = "connected" if self._ws_connected else "enabled"
        return {
            "status": status,
            "qq_bot_enabled": bool(self.app_id and self.app_secret),
            "qq_bot_app_id": masked_app_id,
            "ws_connected": self._ws_connected,
            "connected_at": self._connected_at.isoformat() if self._connected_at else None,
            "last_message_at": self._last_message_at.isoformat() if self._last_message_at else None,
            "messages_received": self._messages_received,
            "messages_sent": self._messages_sent,
            "last_delivery": self._last_delivery.to_status_payload() if self._last_delivery is not None else None,
        }

    def get_runtime_status(self) -> dict[str, Any]:
        return self.get_status()

    def is_runtime_online(self) -> bool | None:
        if not (self.app_id and self.app_secret):
            return None
        return self._ws_connected

    def set_ws_connection_state(
        self,
        *,
        connected: bool,
        connected_at: datetime | None = None,
    ) -> None:
        self._ws_connected = bool(connected)
        if connected:
            self._connected_at = connected_at or utc_now()
            return
        self._connected_at = None

    def _build_validation_response(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        if int(payload.get("op") or 0) != 13:
            return None
        data = payload.get("d")
        if not isinstance(data, dict):
            return None
        plain_token = str(data.get("plain_token") or "").strip()
        event_ts = str(data.get("event_ts") or "").strip()
        if not plain_token or not event_ts:
            return None
        return {
            "plain_token": plain_token,
            "signature": self.build_signature_hex(
                secret=self.app_secret,
                timestamp=event_ts,
                body=plain_token.encode("utf-8"),
            ),
        }

    def _parse_inbound_event(self, payload: dict[str, Any]) -> QQBotInboundEvent | None:
        event_type = str(payload.get("t") or "").strip()
        data = payload.get("d")
        if not event_type or not isinstance(data, dict):
            return None
        if event_type not in {
            "C2C_MESSAGE_CREATE",
            "DIRECT_MESSAGE_CREATE",
            "GROUP_AT_MESSAGE_CREATE",
        }:
            return None

        author = data.get("author")
        if not isinstance(author, dict):
            author = {}

        openid = ""
        guild_id: str | None = None
        if event_type == "C2C_MESSAGE_CREATE":
            openid = str(author.get("user_openid") or author.get("member_openid") or author.get("id") or "").strip()
        elif event_type == "DIRECT_MESSAGE_CREATE":
            openid = str(author.get("id") or author.get("user_openid") or "").strip()
            guild_id = str(data.get("guild_id") or "").strip() or None
        elif event_type == "GROUP_AT_MESSAGE_CREATE":
            openid = str(author.get("member_openid") or author.get("id") or "").strip()

        msg_id = str(data.get("id") or "").strip()
        content = _normalize_content(str(data.get("content") or ""))
        if not openid or not msg_id:
            return None

        return QQBotInboundEvent(
            event_type=event_type,
            openid=openid,
            content=content,
            msg_id=msg_id,
            timestamp=_parse_timestamp(str(data.get("timestamp") or "")),
            guild_id=guild_id,
            raw_event=data,
        )

    async def _run_pipeline_for_user(
        self,
        *,
        openid: str,
        guild_id: str | None,
        inbound: Message,
        pipeline: Any,
    ) -> None:
        async def emit(event: dict[str, Any]) -> None:
            if str(event.get("type") or "") != "error":
                return
            error_message = str(event.get("message") or "处理失败，请稍后重试")
            await self.send_message(
                Message(
                    text=error_message,
                    sender="assistant",
                    session_id=inbound.session_id,
                    channel="qq",
                    sender_id=openid,
                    metadata={
                        "qq": {
                            "backend": "qq_bot",
                            "openid": openid,
                            "guild_id": guild_id,
                            "msg_id": inbound.metadata.get("qq", {}).get("msg_id"),
                        }
                    },
                )
            )

        enqueue_user_message = getattr(pipeline, "enqueue_user_message", None)
        if callable(enqueue_user_message):
            result = enqueue_user_message(inbound, emit=emit)
            if inspect.isawaitable(result):
                await result
            return

        async for event in pipeline.stream_reply(inbound):
            await emit(event)

    async def _resolve_openid(
        self,
        *,
        message: Message,
        qq_meta: Mapping[str, Any],
    ) -> str:
        explicit_openid = str(qq_meta.get("openid") or "").strip()
        if explicit_openid:
            self._last_openid = explicit_openid
            return explicit_openid

        channel_name = str(message.channel or "").strip().lower()
        if channel_name == "qq":
            sender_openid = str(message.sender_id or "").strip()
            if sender_openid:
                self._last_openid = sender_openid
                return sender_openid

        if self._last_openid:
            return self._last_openid

        persisted_openid = await self._load_persisted_openid()
        if persisted_openid:
            self._last_openid = persisted_openid
            return persisted_openid
        return ""

    async def _send_image_with_fallback(
        self,
        *,
        route_kind: str,
        openid: str,
        guild_id: str | None,
        msg_id: str | None,
        text: str | None,
        image_source: str,
        fallback_text: str,
    ) -> None:
        try:
            await self._send_with_retry(
                route_kind=route_kind,
                openid=openid,
                guild_id=guild_id,
                msg_id=msg_id,
                text=text,
                image_source=image_source,
            )
        except (httpx.HTTPStatusError, httpx.TransportError, httpx.TimeoutException, OSError, ValueError) as exc:
            fallback_text = self._join_text_parts(text or "", fallback_text)
            logger.warning("qq_bot.image.fallback_to_text", openid=openid, error=str(exc))
            if not fallback_text:
                return
            await self._send_with_retry(
                route_kind=route_kind,
                openid=openid,
                guild_id=guild_id,
                msg_id=msg_id,
                text=fallback_text,
            )

    async def _load_persisted_openid(self) -> str:
        loader = self._load_target_openid
        if not callable(loader):
            return ""
        try:
            value = loader()
            if inspect.isawaitable(value):
                value = await value
        except (OSError, RuntimeError, TypeError, ValueError):
            logger.warning("qq_bot.target_openid.load_failed", exc_info=True)
            return ""
        return str(value or "").strip()

    async def _remember_openid(self, openid: str) -> None:
        normalized = str(openid or "").strip()
        if not normalized:
            return
        self._last_openid = normalized
        saver = self._save_target_openid
        if not callable(saver):
            return
        try:
            result = saver(normalized)
            if inspect.isawaitable(result):
                await result
        except (OSError, RuntimeError, TypeError, ValueError):
            logger.warning("qq_bot.target_openid.save_failed", exc_info=True)

    def _image_fallback_text(self, image_source: str) -> str:
        raw = str(image_source or "").strip()
        if not raw:
            return "[图片]"
        if _DATA_URL_PATTERN.match(raw):
            return "[图片]"
        if _HTTP_URL_PATTERN.match(raw):
            name = raw.rstrip("/").rsplit("/", 1)[-1]
            return f"[图片] {name}" if name else "[图片]"
        return f"[图片] {Path(raw).name or 'image'}"

    def _segment_image_fallback_text(self, segment: Mapping[str, Any]) -> str:
        label = str(segment.get("name") or "").strip()
        if label:
            return f"[图片] {label}"
        return self._image_fallback_text(str(segment.get("source") or ""))

    def _join_text_parts(self, *parts: str) -> str:
        return "\n".join(part.strip() for part in parts if str(part or "").strip()).strip()

    def _local_image_public_url(self, image_source: str) -> str | None:
        if not self.public_base_url:
            return None
        resolved = Path(str(image_source or "").strip()).expanduser().resolve(strict=False)
        if not resolved.exists() or not resolved.is_file():
            return None
        params = {"path": str(resolved)}
        if self.public_file_token:
            params["token"] = self.public_file_token
        return f"{self.public_base_url}/api/files?{urlencode(params)}"

    def _persist_data_url_image(self, image_source: str) -> str | None:
        if not _DATA_URL_PATTERN.match(image_source):
            return None
        header, _, payload = image_source.partition(",")
        mime_type = header.removeprefix("data:").split(";", 1)[0].strip().lower() or "image/png"
        suffix = mimetypes.guess_extension(mime_type) or ".png"
        target_path = build_upload_path(f"qqbot-outbound{suffix}")
        target_path.write_bytes(base64.b64decode(payload.encode("utf-8"), validate=False))
        return str(target_path)

    def _image_base64_payload(self, image_source: str) -> str | None:
        if _DATA_URL_PATTERN.match(image_source):
            _, _, base64_payload = image_source.partition(",")
            return base64_payload
        if _HTTP_URL_PATTERN.match(image_source):
            return None

        path = Path(str(image_source or "").strip()).expanduser().resolve(strict=False)
        if not path.exists() or not path.is_file():
            return None
        return base64.b64encode(path.read_bytes()).decode("ascii")

    async def _send_with_retry(
        self,
        *,
        route_kind: str,
        openid: str,
        guild_id: str | None,
        msg_id: str | None,
        text: str | None,
        image_source: str | None = None,
    ) -> None:
        for attempt in range(2):
            try:
                access_token = await self._get_access_token()
                if image_source is not None:
                    file_info = await self._upload_image(
                        access_token=access_token,
                        openid=openid,
                        image_source=image_source,
                    )
                    if route_kind == "dm" and guild_id:
                        await self._request_json(
                            "POST",
                            f"{self.api_base_url}/dms/{guild_id}/messages",
                            access_token=access_token,
                            body={
                                "content": text or "",
                                **({"msg_id": msg_id} if msg_id else {}),
                            },
                        )
                    else:
                        await self._request_json(
                            "POST",
                            f"{self.api_base_url}/v2/users/{openid}/messages",
                            access_token=access_token,
                            body={
                                "msg_type": 7,
                                "media": {"file_info": file_info},
                                "msg_seq": _next_msg_seq(msg_id),
                                **({"content": text} if text else {}),
                                **({"msg_id": msg_id} if msg_id else {}),
                            },
                        )
                elif route_kind == "dm" and guild_id:
                    await self._request_json(
                        "POST",
                        f"{self.api_base_url}/dms/{guild_id}/messages",
                        access_token=access_token,
                        body={
                            "content": text or "",
                            **({"msg_id": msg_id} if msg_id else {}),
                        },
                    )
                else:
                    await self._request_json(
                        "POST",
                        f"{self.api_base_url}/v2/users/{openid}/messages",
                        access_token=access_token,
                        body={
                            "msg_type": 0,
                            "content": text or "",
                            "msg_seq": _next_msg_seq(msg_id),
                            **({"msg_id": msg_id} if msg_id else {}),
                        },
                    )

                self._messages_sent += 1
                self._last_message_at = utc_now()
                if callable(self._on_message_sent):
                    self._on_message_sent()
                return
            except (httpx.HTTPStatusError, httpx.TransportError, httpx.TimeoutException) as exc:
                if attempt >= 1:
                    raise
                logger.warning("qq_bot.message.retry", error=str(exc), openid=openid)
                self._invalidate_access_token()

    async def _upload_image(self, *, access_token: str, openid: str, image_source: str) -> str:
        upload_bodies: list[dict[str, Any]] = []

        if _HTTP_URL_PATTERN.match(image_source):
            upload_bodies.append({"file_type": 1, "srv_send_msg": False, "url": image_source})
        else:
            local_public_url = self._local_image_public_url(image_source)
            if local_public_url:
                upload_bodies.append({"file_type": 1, "srv_send_msg": False, "url": local_public_url})
            elif _DATA_URL_PATTERN.match(image_source) and self.public_base_url:
                materialized_path = self._persist_data_url_image(image_source)
                materialized_url = self._local_image_public_url(materialized_path or "")
                if materialized_url:
                    upload_bodies.append({"file_type": 1, "srv_send_msg": False, "url": materialized_url})

            base64_payload = self._image_base64_payload(image_source)
            if base64_payload:
                upload_bodies.append({"file_type": 1, "srv_send_msg": False, "file_data": base64_payload})

        if not upload_bodies:
            raise ValueError("qq image source is unavailable")

        last_error: Exception | None = None
        for index, body in enumerate(upload_bodies, start=1):
            try:
                payload = await self._request_json(
                    "POST",
                    f"{self.api_base_url}/v2/users/{openid}/files",
                    access_token=access_token,
                    body=body,
                )
            except (httpx.HTTPStatusError, httpx.TransportError, httpx.TimeoutException) as exc:
                last_error = exc
                if index >= len(upload_bodies):
                    raise
                logger.warning(
                    "qq_bot.image.upload_variant_failed",
                    openid=openid,
                    variant="url" if "url" in body else "file_data",
                    error=str(exc),
                )
                continue

            file_info = str(payload.get("file_info") or "").strip()
            if file_info:
                return file_info
            last_error = ValueError("qq image upload response missing file_info")

        if last_error is not None:
            raise last_error
        raise ValueError("qq image upload response missing file_info")

    async def _get_access_token(self) -> str:
        cached = _TOKEN_CACHE.get(self.app_id)
        if cached is not None and cached[1] > utc_now() + timedelta(seconds=60):
            return cached[0]

        async with _token_lock_for(self.app_id):
            cached = _TOKEN_CACHE.get(self.app_id)
            if cached is not None and cached[1] > utc_now() + timedelta(seconds=60):
                return cached[0]
            payload = await self._request_json(
                "POST",
                self.token_url,
                body={"appId": self.app_id, "clientSecret": self.app_secret},
            )
            token = str(payload.get("access_token") or "").strip()
            expires_in = int(payload.get("expires_in") or 7200)
            expires_at = utc_now() + timedelta(seconds=max(60, expires_in))
            _TOKEN_CACHE[self.app_id] = (token, expires_at)
            return token

    def _invalidate_access_token(self) -> None:
        _TOKEN_CACHE.pop(self.app_id, None)

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        access_token: str | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if access_token is not None:
            headers["Authorization"] = f"QQBot {access_token}"
            if self.app_id:
                headers["X-Union-Appid"] = self.app_id
        async with httpx.AsyncClient(timeout=self.request_timeout_seconds) as client:
            response = await client.request(
                method,
                url,
                headers=headers,
                json=body,
            )
        response.raise_for_status()
        if not response.content:
            return {}
        return response.json()
