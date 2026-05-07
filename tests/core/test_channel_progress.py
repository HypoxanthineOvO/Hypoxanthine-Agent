from __future__ import annotations

from types import SimpleNamespace

from hypo_agent.core import channel_progress
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
            "terminal": True,
        }
    )

    assert sent is False
    assert text is not None
    assert text.startswith("查询 Notion 失败")
    assert "已尝试 3 次" in text


def test_channel_progress_suppresses_intermediate_tool_result_failure() -> None:
    text, sent = summarize_channel_progress_event(
        {
            "type": "tool_call_result",
            "tool_name": "read_file",
            "status": "error",
            "error_info": "File not found: /home/heyx/Hypo-Agent/weixin-image.png",
            "attempts": 1,
            "outcome_class": "missing_resource",
            "metadata": {"ephemeral": True},
        }
    )

    assert text is None
    assert sent is False


def test_channel_progress_suppresses_recoverable_tool_error_without_retry_flag() -> None:
    text, sent = summarize_channel_progress_event(
        {
            "type": "tool_call_error",
            "tool": "notion_plan_get_structure",
            "error": "Notion page not found: HYX的计划通",
            "will_retry": False,
            "retryable": True,
            "outcome_class": "missing_resource",
        }
    )

    assert text is None
    assert sent is False


def test_channel_progress_suppresses_nonterminal_nonretryable_tool_error() -> None:
    text, sent = summarize_channel_progress_event(
        {
            "type": "tool_call_error",
            "tool": "read_file",
            "error": "File not found: stale path",
            "will_retry": False,
            "retryable": False,
            "outcome_class": "missing_resource",
        }
    )

    assert text is None
    assert sent is False


def test_channel_progress_terminal_tool_error_is_visible() -> None:
    text, sent = summarize_channel_progress_event(
        {
            "type": "tool_call_error",
            "tool": "read_file",
            "error": "File not found: final path",
            "will_retry": False,
            "retryable": False,
            "outcome_class": "missing_resource",
            "terminal": True,
        }
    )

    assert sent is False
    assert text is not None
    assert text.startswith("读取文件 失败")
    assert "missing_resource" in text


def test_channel_progress_suppresses_successful_model_fallback_notice() -> None:
    text, sent = summarize_channel_progress_event(
        {
            "type": "model_fallback",
            "failed_model": "Gemini3Pro",
            "fallback_model": "DeepseekV3_2",
            "reason": "Request timed out after 60 seconds.",
        }
    )

    assert text is None
    assert sent is False


def test_channel_progress_collapses_repeated_tool_start_when_narration_disabled(monkeypatch) -> None:
    monkeypatch.setattr(
        channel_progress,
        "_narration_config",
        lambda: SimpleNamespace(enabled=False, tool_narration={}),
    )

    first_text, prelude_sent = summarize_channel_progress_event(
        {"type": "tool_call_start", "tool_name": "search_web"},
        prelude_sent=False,
    )
    second_text, second_prelude_sent = summarize_channel_progress_event(
        {"type": "tool_call_start", "tool_name": "read_file"},
        prelude_sent=prelude_sent,
    )

    assert first_text == "正在调用 搜索网页"
    assert prelude_sent is True
    assert second_text is None
    assert second_prelude_sent is True
