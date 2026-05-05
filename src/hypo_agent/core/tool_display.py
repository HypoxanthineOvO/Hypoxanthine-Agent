from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ToolDisplay:
    tool_name: str
    display_name: str
    category: str = "tool"
    running_text: str = ""
    success_text: str = ""
    failure_prefix: str = ""

    def with_defaults(self) -> "ToolDisplay":
        running = self.running_text or f"正在调用 {self.display_name}"
        success = self.success_text or f"{self.display_name} 已完成"
        failure = self.failure_prefix or f"{self.display_name} 失败"
        return ToolDisplay(
            tool_name=self.tool_name,
            display_name=self.display_name,
            category=self.category,
            running_text=running,
            success_text=success,
            failure_prefix=failure,
        )


_TOOL_DISPLAYS: dict[str, ToolDisplay] = {
    "get_notion_todo_snapshot": ToolDisplay("get_notion_todo_snapshot", "读取计划通", "notion"),
    "notion_plan_get_today": ToolDisplay("notion_plan_get_today", "读取计划通", "notion"),
    "notion_plan_get_structure": ToolDisplay("notion_plan_get_structure", "读取计划通结构", "notion"),
    "notion_plan_add_items": ToolDisplay("notion_plan_add_items", "写入计划通", "notion"),
    "notion_query_db": ToolDisplay("notion_query_db", "查询 Notion", "notion"),
    "notion_create_entry": ToolDisplay("notion_create_entry", "新建 Notion 记录", "notion"),
    "notion_update_page": ToolDisplay("notion_update_page", "更新 Notion 页面", "notion"),
    "notion_export_page_markdown": ToolDisplay("notion_export_page_markdown", "导出 Notion 页面", "notion"),
    "get_heartbeat_snapshot": ToolDisplay("get_heartbeat_snapshot", "读取今日概况", "notion"),
    "read_file": ToolDisplay("read_file", "读取文件", "file"),
    "write_file": ToolDisplay("write_file", "写入文件", "file"),
    "list_directory": ToolDisplay("list_directory", "查看目录", "file"),
    "scan_directory": ToolDisplay("scan_directory", "扫描目录", "file"),
    "update_directory_description": ToolDisplay("update_directory_description", "更新目录说明", "file"),
    "exec_command": ToolDisplay("exec_command", "执行命令", "command"),
    "run_code": ToolDisplay("run_code", "运行代码", "command"),
    "sub_check": ToolDisplay("sub_check", "运行子检查", "agent"),
    "coder_health": ToolDisplay("coder_health", "检查 Agent 状态", "agent"),
    "search_web": ToolDisplay("search_web", "搜索网页", "web"),
    "web_search": ToolDisplay("web_search", "搜索网页", "web"),
    "web_read": ToolDisplay("web_read", "阅读网页", "web"),
    "generate_image": ToolDisplay("generate_image", "生成图片", "image", running_text="正在生成图片"),
    "edit_image": ToolDisplay("edit_image", "编辑图片", "image", running_text="正在编辑图片"),
    "create_reminder": ToolDisplay("create_reminder", "新建提醒", "reminder"),
    "update_reminder": ToolDisplay("update_reminder", "更新提醒", "reminder"),
    "delete_reminder": ToolDisplay("delete_reminder", "删除提醒", "reminder"),
    "list_reminders": ToolDisplay("list_reminders", "查看提醒", "reminder"),
    "snooze_reminder": ToolDisplay("snooze_reminder", "延后提醒", "reminder"),
    "save_preference": ToolDisplay("save_preference", "写入记忆", "memory"),
    "get_preference": ToolDisplay("get_preference", "读取记忆", "memory"),
    "update_persona_memory": ToolDisplay("update_persona_memory", "更新记忆", "memory"),
    "save_sop": ToolDisplay("save_sop", "保存流程", "memory"),
    "search_sop": ToolDisplay("search_sop", "查找流程", "memory"),
    "search_emails": ToolDisplay("search_emails", "搜索邮件", "email"),
    "scan_emails": ToolDisplay("scan_emails", "扫描邮件", "email"),
    "get_email_detail": ToolDisplay("get_email_detail", "读取邮件详情", "email"),
    "info_today": ToolDisplay("info_today", "整理今日信息", "info"),
    "repair_run": ToolDisplay("repair_run", "运行修复任务", "agent"),
    "codex_job": ToolDisplay("codex_job", "运行 Agent 任务", "agent"),
    "agent_task": ToolDisplay("agent_task", "运行 Agent 任务", "agent"),
}


def tool_display(tool_name: str | None) -> ToolDisplay:
    normalized = str(tool_name or "").strip()
    if not normalized:
        return ToolDisplay("", "处理当前任务").with_defaults()
    configured = _TOOL_DISPLAYS.get(normalized)
    if configured is not None:
        return configured.with_defaults()
    return ToolDisplay(normalized, _humanize_tool_name(normalized)).with_defaults()


def tool_display_payload(tool_name: str | None) -> dict[str, Any]:
    display = tool_display(tool_name)
    return {
        "display_name": display.display_name,
        "tool_category": display.category,
        "running_text": display.running_text,
        "success_text": display.success_text,
        "failure_prefix": display.failure_prefix,
    }


def summarize_tool_failure(
    *,
    tool_name: str,
    error: str | None,
    outcome_class: str | None = None,
    attempts: int | None = None,
    retryable: bool | None = None,
    recovery_actions: list[str] | None = None,
) -> str:
    display = tool_display(tool_name)
    parts: list[str] = [display.failure_prefix]
    if attempts and attempts > 1:
        parts.append(f"已尝试 {attempts} 次")
    if outcome_class:
        parts.append(f"错误类别：{outcome_class}")
    reason = _shorten_error(error)
    if reason:
        parts.append(reason)
    if recovery_actions:
        actions = "；".join(item for item in recovery_actions if item)
        if actions:
            parts.append(f"已尝试恢复：{actions}")
    if retryable is False:
        parts.append("需要调整输入或配置后重试")
    return "：".join([parts[0], "；".join(parts[1:])]) if len(parts) > 1 else parts[0]


def classify_tool_error(error: str | None) -> str:
    text = str(error or "").lower()
    if any(token in text for token in ("could not find property", "validation_error", "body failed validation", "filter must be valid json")):
        return "schema_mismatch"
    if any(token in text for token in ("file not found", "no such file", "not found")):
        return "missing_resource"
    if any(token in text for token in ("allowlist", "not allowed", "forbidden", "permission", "sudo")):
        return "permission_or_policy"
    if any(token in text for token in ("timeout", "timed out")):
        return "timeout"
    if any(token in text for token in ("traceback", "exception", "command exited")):
        return "tool_runtime_error"
    return "tool_error"


def _shorten_error(error: str | None, *, limit: int = 180) -> str:
    normalized = " ".join(str(error or "").strip().split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 1]}..."


def _humanize_tool_name(tool_name: str) -> str:
    words = [part for part in str(tool_name or "").strip().split("_") if part]
    if not words:
        return "处理当前任务"
    return " ".join(words)
