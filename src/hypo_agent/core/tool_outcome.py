from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

ToolOutcomeClass = Literal[
    "success",
    "model_error",
    "user_input_error",
    "policy_block",
    "external_unavailable",
    "tool_bug",
    "dangerous_failure",
]


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    outcome_class: ToolOutcomeClass
    retryable: bool
    breaker_weight: int
    user_visible_summary: str
    side_effect_class: str = ""
    operation: str = ""


def success_outcome(*, operation: str = "") -> ToolOutcome:
    return ToolOutcome(
        outcome_class="success",
        retryable=False,
        breaker_weight=0,
        user_visible_summary="工具调用已完成。",
        side_effect_class=operation,
        operation=operation,
    )


def unknown_tool_outcome(tool_name: str, *, operation: str = "") -> ToolOutcome:
    return ToolOutcome(
        outcome_class="model_error",
        retryable=False,
        breaker_weight=0,
        user_visible_summary=f"未知工具：{tool_name}。请从当前可用工具列表中重新选择。",
        side_effect_class=operation,
        operation=operation,
    )


def policy_block_outcome(reason: str, *, operation: str = "") -> ToolOutcome:
    return ToolOutcome(
        outcome_class="policy_block",
        retryable=False,
        breaker_weight=0,
        user_visible_summary=f"权限策略已阻止该工具调用：{reason}",
        side_effect_class=operation,
        operation=operation,
    )


def blocked_precheck_outcome(reason: str, *, operation: str = "") -> ToolOutcome:
    lowered = str(reason or "").lower()
    if "kill switch" in lowered or "permission" in lowered:
        return policy_block_outcome(reason, operation=operation)
    return ToolOutcome(
        outcome_class="tool_bug",
        retryable=False,
        breaker_weight=0,
        user_visible_summary=f"工具当前不可执行：{reason}",
        side_effect_class=operation,
        operation=operation,
    )


def classify_tool_outcome(
    *,
    status: str,
    error_info: str | None = "",
    operation: str = "",
    metadata: dict[str, Any] | None = None,
) -> ToolOutcome:
    normalized_status = str(status or "").strip().lower()
    normalized_error = str(error_info or "").strip()
    lowered_error = normalized_error.lower()

    if normalized_status == "success":
        return success_outcome(operation=operation)
    if normalized_status == "timeout" or "timed out" in lowered_error or "timeout" in lowered_error:
        return ToolOutcome(
            outcome_class="external_unavailable",
            retryable=True,
            breaker_weight=1,
            user_visible_summary="外部服务或工具调用超时，可以稍后重试。",
            side_effect_class=operation,
            operation=operation,
        )
    if "permission denied" in lowered_error or "outside whitelist" in lowered_error:
        return policy_block_outcome(normalized_error or "permission denied", operation=operation)
    if (
        "not found" in lowered_error
        or "no such file" in lowered_error
        or "does not exist" in lowered_error
        or "required" in lowered_error
        or "missing" in lowered_error
    ):
        recovery_action = (metadata or {}).get("recovery_action")
        if isinstance(recovery_action, dict) and str(recovery_action.get("type") or "").strip():
            return ToolOutcome(
                outcome_class="user_input_error",
                retryable=True,
                breaker_weight=0,
                user_visible_summary=f"输入信息不完整或目标不存在，但工具返回了可以恢复的动作：{normalized_error}",
                side_effect_class=operation,
                operation=operation,
            )
        return ToolOutcome(
            outcome_class="user_input_error",
            retryable=False,
            breaker_weight=0,
            user_visible_summary=f"输入信息不完整或目标不存在：{normalized_error}",
            side_effect_class=operation,
            operation=operation,
        )
    return ToolOutcome(
        outcome_class="tool_bug",
        retryable=False,
        breaker_weight=1,
        user_visible_summary=f"工具执行失败：{normalized_error or normalized_status}",
        side_effect_class=operation,
        operation=operation,
    )
