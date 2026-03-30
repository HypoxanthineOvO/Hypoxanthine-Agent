from __future__ import annotations

import hashlib
import inspect
import os
from dataclasses import asdict
from dataclasses import dataclass
from time import monotonic
from typing import Any, Awaitable, Callable

import structlog

from hypo_agent.core.delivery import DeliveryResult, ensure_delivery_result
from hypo_agent.core.time_utils import utc_isoformat, utc_now
from hypo_agent.core.unified_message import (
    UnifiedMessage,
    message_from_unified,
    prepend_text_prefix,
    unified_message_from_message,
)
from hypo_agent.models import Message

logger = structlog.get_logger("hypo_agent.channel_dispatcher")

ChannelSink = Callable[[Message], Awaitable[DeliveryResult | None] | DeliveryResult | None]


@dataclass(slots=True)
class SinkRegistration:
    name: str
    sink: Callable[..., Awaitable[None] | None]
    platform: str
    is_external: bool


class ChannelDispatcher:
    def __init__(self) -> None:
        self._sinks: dict[str, SinkRegistration] = {}

    def register(
        self,
        channel: str,
        sink: Callable[..., Awaitable[None] | None],
        *,
        platform: str | None = None,
        is_external: bool | None = None,
    ) -> None:
        name = str(channel).strip()
        if not name:
            raise ValueError("channel is required")
        normalized_platform = str(platform or name).strip().lower() or name
        external = (name != "webui") if is_external is None else bool(is_external)
        self._sinks[name] = SinkRegistration(
            name=name,
            sink=sink,
            platform=normalized_platform,
            is_external=external,
        )

    def unregister(self, channel: str) -> None:
        self._sinks.pop(str(channel).strip(), None)

    async def broadcast(
        self,
        message: Message,
        *,
        exclude_channels: set[str] | None = None,
        exclude_client_ids: set[str] | None = None,
    ) -> None:
        excluded = {str(item).strip() for item in (exclude_channels or set()) if str(item).strip()}
        for registration in list(self._sinks.values()):
            if registration.name in excluded:
                continue
            try:
                if registration.platform == "webui" and exclude_client_ids:
                    try:
                        result = registration.sink(message, exclude_client_ids=exclude_client_ids)
                    except TypeError as exc:
                        if "exclude_client_ids" not in str(exc):
                            raise
                        result = registration.sink(message)
                else:
                    result = registration.sink(message)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.exception(
                    "channel_dispatcher.broadcast_failed",
                    channel=registration.name,
                    session_id=message.session_id,
                    message_tag=message.message_tag,
                )

    @property
    def channels(self) -> tuple[str, ...]:
        return tuple(self._sinks.keys())

    def registrations(self) -> tuple[SinkRegistration, ...]:
        return tuple(self._sinks.values())


class ChannelRelayPolicy:
    def __init__(
        self,
        dispatcher: ChannelDispatcher,
        *,
        dedupe_ttl_seconds: float = 60.0,
    ) -> None:
        self.dispatcher = dispatcher
        self._dedupe_ttl_seconds = max(1.0, float(dedupe_ttl_seconds))
        self._recent_delivery_keys: dict[tuple[str, str], float] = {}
        self._last_delivery_results: dict[str, DeliveryResult] = {}

    async def relay_message(
        self,
        message: Message,
        *,
        message_type: str | None = None,
        origin_channel: str | None = None,
        origin_client_id: str | None = None,
        exclude_channels: set[str] | None = None,
        exclude_client_ids: set[str] | None = None,
    ) -> None:
        normalized_message_type = self._normalized_message_type(message_type, sender=message.sender)
        if normalized_message_type == "user_message" and str(message.channel or "").strip().lower() == "feishu":
            feishu_meta = message.metadata.get("feishu") if isinstance(message.metadata, dict) else None
            chat_id = feishu_meta.get("chat_id") if isinstance(feishu_meta, dict) else None
            logger.info(
                "dispatcher.inbound",
                source="feishu",
                session_id=message.session_id,
                sender_id=str(message.sender_id or "").strip() or None,
                chat_id=str(chat_id or "").strip() or None,
                text_len=len(str(message.text or "")),
            )
        unified = unified_message_from_message(
            message,
            message_type=normalized_message_type,
        )
        await self.relay_unified_message(
            unified,
            original_message=message,
            origin_channel=origin_channel,
            origin_client_id=origin_client_id,
            exclude_channels=exclude_channels,
            exclude_client_ids=exclude_client_ids,
        )

    async def relay_unified_message(
        self,
        message: UnifiedMessage,
        *,
        original_message: Message | None = None,
        origin_channel: str | None = None,
        origin_client_id: str | None = None,
        exclude_channels: set[str] | None = None,
        exclude_client_ids: set[str] | None = None,
    ) -> None:
        source_platform = self._normalized_channel(
            origin_channel or message.provenance.source_channel or message.channel
        )
        source_client_id = (
            str(origin_client_id or "").strip()
            or str(message.provenance.origin_webui_client_id or "").strip()
            or str(message.metadata.get("webui_client_id") or "").strip()
        )
        explicit_exclusions = {
            self._normalized_channel(item) for item in (exclude_channels or set()) if str(item).strip()
        }
        explicit_client_exclusions = {
            str(item).strip() for item in (exclude_client_ids or set()) if str(item).strip()
        }
        target_channels = self._target_channels(message.metadata)
        delivery_results: list[DeliveryResult] = []
        trace_enabled = (
            source_platform == "feishu"
            or os.getenv("HYPO_CHANNEL_RELAY_DEBUG", "").strip() == "1"
        )
        trace: list[dict[str, Any]] = []

        for registration in self.dispatcher.registrations():
            if registration.name in explicit_exclusions or registration.platform in explicit_exclusions:
                if trace_enabled:
                    trace.append(
                        {
                            "channel": registration.name,
                            "platform": registration.platform,
                            "action": "skip",
                            "reason": "explicit_exclusion",
                        }
                    )
                continue
            if target_channels is not None and registration.name not in target_channels and registration.platform not in target_channels:
                if trace_enabled:
                    trace.append(
                        {
                            "channel": registration.name,
                            "platform": registration.platform,
                            "action": "skip",
                            "reason": "not_in_target_channels",
                        }
                    )
                continue
            if registration.is_external and message.session_id != "main":
                if trace_enabled:
                    trace.append(
                        {
                            "channel": registration.name,
                            "platform": registration.platform,
                            "action": "skip",
                            "reason": "non_main_session",
                        }
                    )
                continue
            if message.message_type == "user_message" and registration.platform == source_platform:
                if registration.platform != "webui":
                    if trace_enabled:
                        trace.append(
                            {
                                "channel": registration.name,
                                "platform": registration.platform,
                                "action": "skip",
                                "reason": "same_platform_user_message",
                            }
                        )
                    continue
                if not (source_client_id or explicit_client_exclusions):
                    if trace_enabled:
                        trace.append(
                            {
                                "channel": registration.name,
                                "platform": registration.platform,
                                "action": "skip",
                                "reason": "missing_webui_client_id",
                            }
                        )
                    continue

            delivered = message
            if message.message_type == "user_message" and registration.platform != source_platform:
                prefix = self._source_prefix(source_platform)
                if prefix:
                    delivered = prepend_text_prefix(delivered, prefix)

            delivery_key = self._delivery_key(delivered, registration=registration)
            if self._should_skip_duplicate(registration.name, delivery_key):
                if trace_enabled:
                    trace.append(
                        {
                            "channel": registration.name,
                            "platform": registration.platform,
                            "action": "skip",
                            "reason": "deduped",
                        }
                    )
                continue
            self._remember_delivery(registration.name, delivery_key)

            payload: Message | UnifiedMessage
            if registration.platform == "webui":
                payload = message_from_unified(delivered)
                if original_message is not None:
                    payload = payload.model_copy(
                        update={
                            "timestamp": original_message.timestamp,
                            "metadata": dict(original_message.metadata),
                        }
                    )
            else:
                payload = delivered

            delivery_result = await self._deliver(
                registration,
                payload,
                exclude_client_ids=explicit_client_exclusions or ({source_client_id} if source_client_id and registration.platform == "webui" else None),
            )
            delivery_results.append(delivery_result)
            self._last_delivery_results[delivery_result.channel] = delivery_result
            if trace_enabled:
                trace.append(
                    {
                        "channel": registration.name,
                        "platform": registration.platform,
                        "action": "deliver",
                        "success": bool(delivery_result.success),
                        "error": delivery_result.error,
                    }
                )

        if trace_enabled:
            logger.info(
                "channel_relay.trace",
                source=source_platform,
                session_id=message.session_id,
                message_type=message.message_type,
                target_channels=sorted(target_channels) if target_channels is not None else None,
                trace=trace,
            )

        self._log_delivery_results(
            message,
            source_platform=source_platform,
            results=delivery_results,
        )

    async def _deliver(
        self,
        registration: SinkRegistration,
        payload: Message | UnifiedMessage,
        *,
        exclude_client_ids: set[str] | None,
    ) -> DeliveryResult:
        try:
            if registration.platform == "webui" and exclude_client_ids:
                try:
                    result = registration.sink(payload, exclude_client_ids=exclude_client_ids)
                except TypeError as exc:
                    if "exclude_client_ids" not in str(exc):
                        raise
                    result = registration.sink(payload)
            else:
                result = registration.sink(payload)
            if inspect.isawaitable(result):
                result = await result
        except Exception:
            logger.exception(
                "channel_relay.delivery_failed",
                channel=registration.name,
                platform=registration.platform,
                session_id=getattr(payload, "session_id", ""),
            )
            return DeliveryResult.failed(
                registration.name,
                error="channel sink raised an exception",
            )
        return ensure_delivery_result(result, channel=registration.name)

    def last_delivery_for(self, channel: str) -> dict[str, Any] | None:
        result = self._last_delivery_results.get(str(channel or "").strip())
        if result is None:
            return None
        return result.to_status_payload()

    def _log_delivery_results(
        self,
        message: UnifiedMessage,
        *,
        source_platform: str,
        results: list[DeliveryResult],
    ) -> None:
        if not results:
            logger.info(
                "channel_relay.broadcast_skipped",
                source=source_platform,
                session_id=message.session_id,
                message_type=message.message_type,
                registrations=[{"channel": item.name, "platform": item.platform} for item in self.dispatcher.registrations()],
            )
            return

        failed = [item for item in results if not item.success]
        if not failed:
            log_method = logger.info
        elif len(failed) == len(results):
            log_method = logger.error
        else:
            log_method = logger.warning

        summary_lines = [
            self._format_delivery_line(result)
            for result in results
        ]
        log_method(
            "channel_relay.broadcast",
            msg_id=self._broadcast_message_id(message),
            source=source_platform,
            targets=len(results),
            session_id=message.session_id,
            results=[asdict(result) for result in results],
            summary="\n".join(summary_lines),
            timestamp=utc_isoformat(utc_now()),
        )

    def _delivery_key(self, message: UnifiedMessage, *, registration: SinkRegistration) -> str:
        source_message_id = str(message.provenance.source_message_id or "").strip()
        if source_message_id:
            return f"{registration.platform}:{message.message_type}:{message.session_id}:{source_message_id}"
        digest = hashlib.sha1(
            (
                f"{registration.platform}|{message.message_type}|{message.session_id}|"
                f"{message.channel}|{message.sender}|{message.sender_id or ''}|"
                f"{message.timestamp.isoformat() if message.timestamp is not None else ''}|"
                f"{message.raw_text or ''}|{message.plain_text()}"
            ).encode("utf-8")
        ).hexdigest()
        return digest

    def _should_skip_duplicate(self, channel: str, key: str) -> bool:
        self._prune_recent_keys()
        return (channel, key) in self._recent_delivery_keys

    def _remember_delivery(self, channel: str, key: str) -> None:
        self._prune_recent_keys()
        self._recent_delivery_keys[(channel, key)] = monotonic()

    def _prune_recent_keys(self) -> None:
        cutoff = monotonic() - self._dedupe_ttl_seconds
        stale = [item for item, ts in self._recent_delivery_keys.items() if ts < cutoff]
        for item in stale:
            self._recent_delivery_keys.pop(item, None)

    def _normalized_message_type(self, message_type: str | None, *, sender: str) -> str:
        normalized = str(message_type or "").strip().lower()
        if normalized in {"user_message", "ai_reply"}:
            return normalized
        return "user_message" if str(sender or "").strip().lower() == "user" else "ai_reply"

    def _target_channels(self, metadata: dict[str, Any]) -> set[str] | None:
        raw = metadata.get("target_channels")
        if not isinstance(raw, list):
            return None
        values = {self._normalized_channel(item) for item in raw if str(item).strip()}
        return values or None

    def _source_prefix(self, channel: str) -> str:
        mapping = {
            "qq": "[QQ] ",
            "qq_bot": "[QQ] ",
            "qq_napcat": "[QQ] ",
            "feishu": "[飞书] ",
            "weixin": "[微信] ",
            "webui": "[WebUI] ",
        }
        return mapping.get(channel, f"[{channel.upper()}] " if channel else "")

    def _normalized_channel(self, channel: str | None) -> str:
        return str(channel or "").strip().lower()

    def _broadcast_message_id(self, message: UnifiedMessage) -> str:
        source_message_id = str(message.provenance.source_message_id or "").strip()
        if source_message_id:
            return source_message_id
        digest = hashlib.sha1(
            (
                f"{message.session_id}|{message.channel}|{message.sender}|"
                f"{message.timestamp.isoformat() if message.timestamp is not None else ''}|"
                f"{message.raw_text or ''}|{message.plain_text()}"
            ).encode("utf-8")
        ).hexdigest()
        return digest[:12]

    def _format_delivery_line(self, result: DeliveryResult) -> str:
        delivered_segments = max(0, result.segment_count - result.failed_segments)
        prefix = "✅" if result.success else "❌"
        suffix = f" (error: {result.error})" if result.error else ""
        return f"→ {result.channel}: {prefix} {delivered_segments}/{result.segment_count} segments{suffix}"
