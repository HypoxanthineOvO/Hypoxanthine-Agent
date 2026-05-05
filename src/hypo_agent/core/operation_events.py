from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from hypo_agent.core.time_utils import utc_isoformat, utc_now

OperationEventType = Literal[
    "resource_candidates",
    "recovery_action",
    "channel_delivery",
    "verify_result",
    "image_generation",
    "image_delivery",
]


@dataclass(frozen=True, slots=True)
class OperationEvent:
    operation_id: str
    session_id: str
    event_type: OperationEventType
    status: str = ""
    channel: str | None = None
    candidates: list[dict[str, Any]] = field(default_factory=list)
    delivery: dict[str, Any] = field(default_factory=dict)
    generation: dict[str, Any] = field(default_factory=dict)
    recovery_action: dict[str, Any] | None = None
    timestamp: str = field(default_factory=lambda: utc_isoformat(utc_now()) or "")

    @classmethod
    def resource_candidates(
        cls,
        *,
        operation_id: str,
        session_id: str,
        candidates: list[dict[str, Any]],
        recovery_action: dict[str, Any] | None = None,
    ) -> OperationEvent:
        return cls(
            operation_id=operation_id,
            session_id=session_id,
            event_type="resource_candidates",
            status="needs_confirmation",
            candidates=list(candidates),
            recovery_action=recovery_action,
        )

    @classmethod
    def channel_delivery(
        cls,
        *,
        operation_id: str,
        session_id: str,
        channel: str,
        status: str,
        delivery: dict[str, Any],
        recovery_action: dict[str, Any] | None = None,
    ) -> OperationEvent:
        return cls(
            operation_id=operation_id,
            session_id=session_id,
            event_type="channel_delivery",
            status=status,
            channel=channel,
            delivery=dict(delivery),
            recovery_action=recovery_action,
        )

    @classmethod
    def image_generation(
        cls,
        *,
        operation_id: str,
        session_id: str,
        status: str,
        generation: dict[str, Any],
        recovery_action: dict[str, Any] | None = None,
    ) -> OperationEvent:
        return cls(
            operation_id=operation_id,
            session_id=session_id,
            event_type="image_generation",
            status=status,
            generation=dict(generation),
            recovery_action=recovery_action,
        )

    @classmethod
    def image_delivery(
        cls,
        *,
        operation_id: str,
        session_id: str,
        channel: str,
        status: str,
        delivery: dict[str, Any],
        recovery_action: dict[str, Any] | None = None,
    ) -> OperationEvent:
        return cls(
            operation_id=operation_id,
            session_id=session_id,
            event_type="image_delivery",
            status=status,
            channel=channel,
            delivery=dict(delivery),
            recovery_action=recovery_action,
        )

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": "operation_event",
            "event_type": self.event_type,
            "operation_id": self.operation_id,
            "session_id": self.session_id,
            "status": self.status,
            "timestamp": self.timestamp,
        }
        if self.channel is not None:
            payload["channel"] = self.channel
        if self.candidates:
            payload["candidates"] = self.candidates
        if self.delivery:
            payload["delivery"] = self.delivery
        if self.generation:
            payload["generation"] = self.generation
        if self.recovery_action is not None:
            payload["recovery_action"] = self.recovery_action
        return payload
