from __future__ import annotations

from hypo_agent.core.tool_outcome import classify_tool_outcome


def test_missing_resource_with_recovery_action_is_retryable_user_input_error() -> None:
    outcome = classify_tool_outcome(
        status="error",
        error_info="File not found: /tmp/report.md",
        operation="read",
        metadata={
            "recovery_action": {
                "type": "ask_user",
                "reason": "multiple_candidates",
                "message": "找到多个候选资源，请确认要使用哪一个。",
            }
        },
    )

    assert outcome.outcome_class == "user_input_error"
    assert outcome.retryable is True
    assert outcome.breaker_weight == 0
    assert "可以恢复" in outcome.user_visible_summary
