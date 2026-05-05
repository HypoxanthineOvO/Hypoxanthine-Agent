from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hypo_agent.core.time_utils import utc_isoformat, utc_now


@dataclass(frozen=True, slots=True)
class AttachmentDeliveryOutcome:
    filename: str
    attachment_type: str
    success: bool
    error: str | None = None
    recovery_action: dict[str, Any] | None = None

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "filename": self.filename,
            "attachment_type": self.attachment_type,
            "success": self.success,
            "error": self.error,
        }
        if self.recovery_action is not None:
            payload["recovery_action"] = self.recovery_action
        return payload


@dataclass(frozen=True, slots=True)
class ChannelCapability:
    channel: str
    supports_text: bool = True
    supported_attachment_types: set[str] | None = None
    max_attachment_bytes: int | None = None
    fallback_actions: list[str] | None = None

    def supports_attachment_type(self, attachment_type: str) -> bool:
        supported = self.supported_attachment_types or set()
        return str(attachment_type or "").strip().lower() in {
            str(item).strip().lower() for item in supported
        }

    def describe_limit(self, attachment_type: str) -> str:
        normalized_type = str(attachment_type or "attachment").strip() or "attachment"
        if self.max_attachment_bytes is None:
            return f"{self.channel} supports {normalized_type}"
        return f"{self.channel} supports {normalized_type} up to {self.max_attachment_bytes} bytes"


@dataclass(slots=True)
class DeliveryResult:
    channel: str
    success: bool
    segment_count: int
    failed_segments: int
    error: str | None
    timestamp: str
    attachment_outcomes: list[AttachmentDeliveryOutcome]

    @classmethod
    def ok(
        cls,
        channel: str,
        *,
        segment_count: int = 0,
        attachment_outcomes: list[AttachmentDeliveryOutcome] | None = None,
    ) -> DeliveryResult:
        return cls(
            channel=str(channel or "").strip(),
            success=True,
            segment_count=max(0, int(segment_count)),
            failed_segments=0,
            error=None,
            timestamp=utc_isoformat(utc_now()) or "",
            attachment_outcomes=list(attachment_outcomes or []),
        )

    @classmethod
    def failed(
        cls,
        channel: str,
        *,
        segment_count: int = 0,
        failed_segments: int | None = None,
        error: str | None = None,
        attachment_outcomes: list[AttachmentDeliveryOutcome] | None = None,
    ) -> DeliveryResult:
        normalized_segment_count = max(0, int(segment_count))
        normalized_failed = (
            normalized_segment_count
            if failed_segments is None
            else max(0, min(int(failed_segments), normalized_segment_count))
        )
        return cls(
            channel=str(channel or "").strip(),
            success=False,
            segment_count=normalized_segment_count,
            failed_segments=normalized_failed,
            error=str(error or "").strip() or None,
            timestamp=utc_isoformat(utc_now()) or "",
            attachment_outcomes=list(attachment_outcomes or []),
        )

    def to_status_payload(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "error": self.error,
            "timestamp": self.timestamp,
            "attachment_outcomes": [
                item.to_payload() for item in self.attachment_outcomes
            ],
        }


def ensure_delivery_result(
    value: DeliveryResult | bool | None,
    *,
    channel: str,
    segment_count: int = 1,
    error: str | None = None,
) -> DeliveryResult:
    if isinstance(value, DeliveryResult):
        if value.channel:
            return value
        return DeliveryResult(
            channel=str(channel or "").strip(),
            success=value.success,
            segment_count=value.segment_count,
            failed_segments=value.failed_segments,
            error=value.error,
            timestamp=value.timestamp,
            attachment_outcomes=list(value.attachment_outcomes),
        )
    if isinstance(value, bool):
        if value:
            return DeliveryResult.ok(channel, segment_count=segment_count)
        return DeliveryResult.failed(channel, segment_count=segment_count, error=error)
    if value is None:
        return DeliveryResult.ok(channel, segment_count=segment_count)
    return DeliveryResult.ok(channel, segment_count=segment_count)


def combine_delivery_results(channel: str, results: list[DeliveryResult]) -> DeliveryResult:
    if not results:
        return DeliveryResult.ok(channel, segment_count=0)

    segment_count = sum(max(0, int(item.segment_count)) for item in results)
    failed_segments = min(
        segment_count,
        sum(max(0, int(item.failed_segments)) for item in results),
    )
    errors = [
        str(item.error or "").strip()
        for item in results
        if not item.success and str(item.error or "").strip()
    ]
    attachment_outcomes: list[AttachmentDeliveryOutcome] = []
    for item in results:
        attachment_outcomes.extend(item.attachment_outcomes)
    return DeliveryResult(
        channel=str(channel or "").strip(),
        success=all(item.success for item in results),
        segment_count=segment_count,
        failed_segments=failed_segments,
        error="; ".join(dict.fromkeys(errors)) or None,
        timestamp=utc_isoformat(utc_now()) or "",
        attachment_outcomes=attachment_outcomes,
    )
