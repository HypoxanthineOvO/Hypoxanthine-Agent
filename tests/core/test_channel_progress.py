from __future__ import annotations

from hypo_agent.core.channel_progress import summarize_channel_progress_event


def test_channel_progress_suppresses_retryable_tool_error() -> None:
    text, sent = summarize_channel_progress_event(
        {
            "type": "tool_call_error",
            "tool": "read_file",
            "error": "File not found",
            "will_retry": True,
        }
    )

    assert text is None
    assert sent is False


def test_channel_progress_final_failure_uses_display_summary() -> None:
    text, sent = summarize_channel_progress_event(
        {
            "type": "tool_call_result",
            "tool_name": "notion_query_db",
            "status": "error",
            "error_info": "Could not find property with name or id: Status",
            "attempts": 3,
            "outcome_class": "schema_mismatch",
        }
    )

    assert sent is False
    assert text is not None
    assert text.startswith("查询 Notion 失败")
    assert "已尝试 3 次" in text
