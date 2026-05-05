from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from hypo_agent.core.delivery import ChannelCapability, DeliveryResult
from hypo_agent.core.resource_resolution import ResourceResolution

RecoveryState = Literal[
    "resolve_resource",
    "ask_user",
    "fallback",
    "send_or_upload",
    "retry",
    "verify_result",
    "give_up_explained",
]


@dataclass(frozen=True, slots=True)
class ActiveRecoveryDecision:
    state: RecoveryState
    action: str
    recovery_action: dict[str, Any] = field(default_factory=dict)
    retry_after_attempts: int | None = None


class ActiveRecoveryStateMachine:
    def __init__(self, *, max_retries: int = 2) -> None:
        self.max_retries = max(0, int(max_retries))

    def decide_channel_delivery(
        self,
        *,
        resource_resolution: ResourceResolution,
        capability: ChannelCapability,
    ) -> ActiveRecoveryDecision:
        if resource_resolution.status == "ambiguous":
            return ActiveRecoveryDecision(
                state="ask_user",
                action="confirm_resource",
                recovery_action=_recovery_payload(resource_resolution.recovery_action),
            )
        if resource_resolution.status == "not_found":
            return ActiveRecoveryDecision(
                state="ask_user",
                action="clarify_resource",
                recovery_action=_recovery_payload(resource_resolution.recovery_action),
            )
        if resource_resolution.status == "blocked":
            return ActiveRecoveryDecision(
                state="give_up_explained",
                action="explain_failure",
                recovery_action={
                    "type": "request_permission",
                    "reason": "resource_blocked",
                    "message": "资源被权限策略阻止，无法发送。",
                },
            )

        ref = resource_resolution.ref
        if ref is None:
            return ActiveRecoveryDecision(
                state="ask_user",
                action="clarify_resource",
                recovery_action={
                    "type": "search_or_ask",
                    "reason": "missing_resource_ref",
                    "message": "没有可发送的资源，请提供文件或链接。",
                },
            )

        attachment_type = _attachment_type_for_ref(ref.metadata, fallback=ref.kind)
        if not capability.supports_attachment_type(attachment_type):
            action = _first_supported_fallback(capability)
            return ActiveRecoveryDecision(
                state="fallback",
                action=action,
                recovery_action={
                    "type": action,
                    "reason": "unsupported_attachment_type",
                    "message": f"{capability.channel} 不支持发送 {attachment_type}，需要降级处理。",
                },
            )

        return ActiveRecoveryDecision(
            state="send_or_upload",
            action="send_attachment",
        )

    def decide_after_delivery(
        self,
        delivery_result: DeliveryResult,
        *,
        attempts: int,
    ) -> ActiveRecoveryDecision:
        if delivery_result.success:
            return ActiveRecoveryDecision(
                state="verify_result",
                action="verify_delivery",
            )
        if attempts < self.max_retries:
            return ActiveRecoveryDecision(
                state="retry",
                action="retry_upload",
                retry_after_attempts=attempts,
                recovery_action={
                    "type": "retry_upload",
                    "reason": "delivery_failed",
                    "message": "渠道发送失败，将按重试预算再次尝试。",
                },
            )
        return ActiveRecoveryDecision(
            state="give_up_explained",
            action="explain_failure",
            recovery_action={
                "type": "give_up_explained",
                "reason": "retry_budget_exhausted",
                "message": f"渠道发送失败且已达到重试上限：{delivery_result.error or 'unknown error'}",
            },
        )


def _attachment_type_for_ref(metadata: dict[str, Any], *, fallback: str) -> str:
    attachment_type = str(metadata.get("attachment_type") or "").strip()
    if attachment_type:
        return attachment_type
    if fallback in {"generated_file", "file"}:
        return "file"
    if fallback == "url":
        return "file"
    return str(fallback or "file")


def _first_supported_fallback(capability: ChannelCapability) -> str:
    fallbacks = list(capability.fallback_actions or [])
    if fallbacks:
        return str(fallbacks[0])
    return "send_summary"


def _recovery_payload(action: Any | None) -> dict[str, Any]:
    if action is None:
        return {}
    return {
        "type": getattr(action, "type", ""),
        "reason": getattr(action, "reason", ""),
        "message": getattr(action, "message", ""),
    }
