from __future__ import annotations

from hypo_agent.core.tool_display import classify_tool_error, summarize_tool_failure, tool_display


def test_tool_display_covers_high_frequency_tools() -> None:
    assert tool_display("read_file").display_name == "读取文件"
    assert tool_display("notion_query_db").display_name == "查询 Notion"
    assert tool_display("exec_command").running_text == "正在调用 执行命令"
    assert tool_display("generate_image").running_text == "正在生成图片"


def test_unknown_tool_display_has_stable_fallback() -> None:
    display = tool_display("mystery_tool")

    assert display.display_name == "mystery tool"
    assert display.running_text == "正在调用 mystery tool"


def test_summarize_tool_failure_hides_traceback_shape() -> None:
    summary = summarize_tool_failure(
        tool_name="generate_image",
        error="Traceback line 1\nTraceback line 2",
        outcome_class="tool_runtime_error",
        attempts=2,
        retryable=False,
    )

    assert summary.startswith("生成图片 失败")
    assert "已尝试 2 次" in summary
    assert "\n" not in summary


def test_classify_tool_error_for_m1_cases() -> None:
    assert classify_tool_error("Could not find property with name or id: Status") == "schema_mismatch"
    assert classify_tool_error("File not found: /tmp/missing.md") == "missing_resource"
    assert classify_tool_error("Command not allowed by exec profile") == "permission_or_policy"
