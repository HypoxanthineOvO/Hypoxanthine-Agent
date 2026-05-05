from __future__ import annotations

from dataclasses import dataclass, field
import mimetypes
from pathlib import Path
from typing import Any

from hypo_agent.core.delivery import DeliveryResult
from hypo_agent.models import Attachment, Message


DEFAULT_OUTBOUND_CHANNELS = ("qq", "weixin", "feishu")
CHANNEL_RESULT_ALIASES = {
    "qq": ("qq", "qq_bot"),
}


@dataclass(slots=True)
class OutboundSendRequest:
    text: str = ""
    images: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    channels: list[str] = field(default_factory=list)
    dry_run: bool = False
    session_id: str = "main"
    recipient: str = "hyx"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class OutboundSendResult:
    success: bool
    dry_run: bool
    target_channels: list[str]
    channel_results: dict[str, dict[str, Any]]
    attachments: list[dict[str, Any]]
    error: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "dry_run": self.dry_run,
            "target_channels": list(self.target_channels),
            "channel_results": dict(self.channel_results),
            "attachments": list(self.attachments),
            "error": self.error,
        }


class OutboundSendService:
    def __init__(
        self,
        *,
        dispatcher: Any,
        default_channels: tuple[str, ...] = DEFAULT_OUTBOUND_CHANNELS,
        max_attachment_bytes: int = 25 * 1024 * 1024,
    ) -> None:
        self.dispatcher = dispatcher
        self.default_channels = tuple(default_channels)
        self.max_attachment_bytes = max(1, int(max_attachment_bytes))

    async def send(self, request: OutboundSendRequest) -> OutboundSendResult:
        target_channels = self._target_channels(request.channels)
        attachments_or_error = self._build_attachments(request)
        if isinstance(attachments_or_error, OutboundSendResult):
            return attachments_or_error
        attachments = attachments_or_error
        attachment_payloads = [item.model_dump(mode="json") for item in attachments]

        if request.dry_run:
            return OutboundSendResult(
                success=True,
                dry_run=True,
                target_channels=target_channels,
                channel_results={
                    channel: {"success": None, "planned": True, "error": None}
                    for channel in target_channels
                },
                attachments=attachment_payloads,
            )

        message = Message(
            text=str(request.text or "").strip() or None,
            attachments=attachments,
            sender="assistant",
            session_id=str(request.session_id or "").strip() or "main",
            channel="system",
            metadata={
                **dict(request.metadata),
                "target_channels": target_channels,
                "recipient": str(request.recipient or "hyx").strip() or "hyx",
                "source": "hypo_agent_cli",
            },
        )
        raw_results = await self._dispatch(message)
        channel_results = {
            result.channel: {
                "success": result.success,
                "planned": False,
                "error": result.error,
                "segments": result.segment_count,
                "failed_segments": result.failed_segments,
                "attachment_outcomes": [
                    item.to_payload() for item in result.attachment_outcomes
                ],
            }
            for result in raw_results
        }
        for channel in target_channels:
            channel_results.setdefault(
                channel,
                {
                    "success": False,
                    "planned": False,
                    "error": "no_delivery_result",
                },
            )
        return OutboundSendResult(
            success=all(bool(item.get("success")) for item in channel_results.values()),
            dry_run=False,
            target_channels=target_channels,
            channel_results=channel_results,
            attachments=attachment_payloads,
            error=None,
        )

    def _target_channels(self, requested: list[str]) -> list[str]:
        values = [str(item or "").strip().lower() for item in requested if str(item or "").strip()]
        if values:
            return list(dict.fromkeys(values))
        channels = getattr(self.dispatcher, "channels", None)
        if channels:
            return list(dict.fromkeys(str(item).strip() for item in channels if str(item).strip()))
        registrations_getter = getattr(getattr(self.dispatcher, "dispatcher", None), "registrations", None)
        if callable(registrations_getter):
            channels_from_registrations = [
                str(getattr(item, "name", "") or "").strip()
                for item in registrations_getter()
                if bool(getattr(item, "is_external", False))
            ]
            if channels_from_registrations:
                return list(dict.fromkeys(channels_from_registrations))
        return list(self.default_channels)

    def _build_attachments(
        self,
        request: OutboundSendRequest,
    ) -> list[Attachment] | OutboundSendResult:
        attachments: list[Attachment] = []
        for raw_path in request.images:
            built = self._attachment_from_path(raw_path, attachment_type="image")
            if isinstance(built, OutboundSendResult):
                return built
            attachments.append(built)
        for raw_path in request.files:
            built = self._attachment_from_path(raw_path, attachment_type="file")
            if isinstance(built, OutboundSendResult):
                return built
            attachments.append(built)
        return attachments

    def _attachment_from_path(self, raw_path: str, *, attachment_type: str) -> Attachment | OutboundSendResult:
        path = Path(str(raw_path or "").strip()).expanduser().resolve(strict=False)
        if not path.exists() or not path.is_file():
            return OutboundSendResult(
                success=False,
                dry_run=True,
                target_channels=[],
                channel_results={},
                attachments=[],
                error="attachment_not_found",
            )
        size_bytes = path.stat().st_size
        if size_bytes > self.max_attachment_bytes:
            return OutboundSendResult(
                success=False,
                dry_run=True,
                target_channels=[],
                channel_results={},
                attachments=[],
                error="attachment_too_large",
            )
        mime_type, _ = mimetypes.guess_type(path.name)
        return Attachment(
            type=attachment_type,  # type: ignore[arg-type]
            url=str(path),
            filename=path.name,
            mime_type=mime_type,
            size_bytes=size_bytes,
        )

    async def _dispatch(self, message: Message) -> list[DeliveryResult]:
        sender = getattr(self.dispatcher, "send", None)
        if callable(sender):
            results = sender(message)
            if hasattr(results, "__await__"):
                results = await results
            return [item for item in results if isinstance(item, DeliveryResult)]

        relay = getattr(self.dispatcher, "relay_message", None)
        if callable(relay):
            await relay(message, message_type="ai_reply", origin_channel="system")
            channels = message.metadata.get("target_channels")
            output: list[DeliveryResult] = []
            for channel in channels if isinstance(channels, list) else []:
                getter = getattr(self.dispatcher, "last_delivery_for", None)
                payload = None
                if callable(getter):
                    for candidate in CHANNEL_RESULT_ALIASES.get(str(channel), (str(channel),)):
                        payload = getter(candidate)
                        if isinstance(payload, dict):
                            break
                if isinstance(payload, dict):
                    output.append(
                        DeliveryResult(
                            channel=str(channel),
                            success=bool(payload.get("success")),
                            segment_count=0,
                            failed_segments=0,
                            error=payload.get("error"),
                            timestamp=str(payload.get("timestamp") or ""),
                            attachment_outcomes=[],
                        )
                    )
            return output

        return [
            DeliveryResult.failed(
                str(channel),
                error="dispatcher_unavailable",
            )
            for channel in message.metadata.get("target_channels", [])
        ]
