from __future__ import annotations

from typing import Any


_TERMINAL_STATES = {"completed", "failed", "aborted"}


def is_terminal_status(status: str) -> bool:
    return str(status or "").strip().lower() in _TERMINAL_STATES


def format_status_message(*, task_id: str, status: str) -> str:
    return "\n".join(
        [
            "─────────────────────────────────",
            f"🤖 Codex · {task_id} | {str(status or '').strip().upper() or 'UNKNOWN'}",
            "─────────────────────────────────",
        ]
    )


def format_terminal_message(payload: dict[str, Any]) -> str:
    task_id = str(payload.get("task_id") or payload.get("taskId") or "").strip()
    status = str(payload.get("status") or "").strip().lower()

    if status == "completed":
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        summary = str(result.get("summary") or "暂无摘要").strip() or "暂无摘要"
        changes = result.get("fileChanges") if isinstance(result.get("fileChanges"), list) else []
        tests_passed = result.get("testsPassed")
        tests_text = "通过" if tests_passed is True else "失败" if tests_passed is False else "未知"
        return "\n".join(
            [
                "编码任务完成！",
                f"任务：{task_id}",
                f"摘要：{summary}",
                f"文件变更：{len(changes)} 个文件",
                f"测试：{tests_text}",
            ]
        )

    if status == "failed":
        error = str(
            payload.get("error")
            or payload.get("message")
            or payload.get("last_error")
            or "未知错误"
        ).strip() or "未知错误"
        return f"编码任务失败：{error}"

    if status == "aborted":
        return f"编码任务已中止：{task_id}"

    return format_status_message(task_id=task_id, status=status or "unknown")


def format_webhook_event_message(payload: dict[str, Any]) -> str:
    event = str(payload.get("event") or "").strip()
    if event == "task.completed":
        normalized = dict(payload)
        normalized["status"] = "completed"
        return format_terminal_message(normalized)
    if event == "task.failed":
        normalized = dict(payload)
        normalized["status"] = "failed"
        return format_terminal_message(normalized)
    if event == "task.approval_required":
        command = str(payload.get("command") or payload.get("message") or "未知操作").strip() or "未知操作"
        return f"Hypo-Coder 需要你审批一个操作：{command}"
    return f"Hypo-Coder 事件：{event}"
