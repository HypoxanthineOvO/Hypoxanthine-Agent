from __future__ import annotations

from dataclasses import dataclass
import json
import re
from string import Formatter
from typing import Any

from hypo_agent.models import NarrationConfig

_FORMATTER = Formatter()
_ELLIPSIS_RE = re.compile(r"(?:\.\.\.|…)+$")
_LEADING_DECORATION_RE = re.compile(r"^[^\w\u4e00-\u9fff]+")
_SENSITIVE_KEY_PARTS = ("password", "cookie", "token", "secret", "api_key", "apikey")

_TOOL_LABEL_OVERRIDES: dict[str, str] = {
    "get_notion_todo_snapshot": "读取计划通",
    "get_heartbeat_snapshot": "读取今日概况",
    "update_reminder": "更新提醒",
    "create_reminder": "新建提醒",
    "delete_reminder": "删除提醒",
    "list_reminders": "查看提醒",
    "search_web": "搜索网页",
    "web_search": "搜索网页",
    "web_read": "阅读网页",
    "read_file": "读取文件",
    "write_file": "写入文件",
    "exec_command": "执行命令",
    "run_code": "运行代码",
    "list_directory": "查看目录",
    "save_sop": "保存流程",
    "search_sop": "查找流程",
    "update_persona_memory": "更新记忆",
    "save_preference": "写入记忆",
    "get_preference": "读取记忆",
    "snooze_reminder": "延后提醒",
    "search_emails": "搜索邮件",
    "scan_emails": "扫描邮件",
    "get_email_detail": "读取邮件详情",
    "info_today": "整理今日信息",
}


@dataclass(slots=True)
class TraceEntry:
    event_type: str
    tool_name: str
    summary: str
    elapsed_ms: int


@dataclass(slots=True)
class NarrationContext:
    user_message: str
    current_tool: str
    current_args: dict[str, Any]
    recent_trace: list[TraceEntry]
    iteration_number: int
    total_tools_called: int


def render_tool_narration(
    config: NarrationConfig,
    tool_name: str,
    tool_args: dict[str, Any] | None,
) -> str | None:
    normalized_tool_name = str(tool_name or "").strip()
    if not normalized_tool_name:
        return None

    tool_config = config.tool_narration.get(normalized_tool_name)
    if tool_config is None:
        return None

    values = build_template_values(tool_args or {})
    rendered = _try_format(tool_config.template, values)
    if rendered:
        return rendered

    fallback = str(tool_config.fallback or "").strip()
    return fallback or None


def build_template_values(tool_args: dict[str, Any]) -> dict[str, str]:
    values = {str(key): stringify_narration_value(value) for key, value in tool_args.items()}

    if not values.get("query"):
        values["query"] = values.get("q") or values.get("keyword") or values.get("keywords") or ""
    if not values.get("path"):
        values["path"] = values.get("file_path") or values.get("directory") or values.get("dir") or ""
    if not values.get("title"):
        values["title"] = values.get("name") or values.get("reminder_title") or ""
    return values


def describe_tool_for_narration(
    config: NarrationConfig,
    tool_name: str,
    tool_args: dict[str, Any] | None = None,
) -> str:
    rendered = render_tool_narration(config, tool_name, tool_args)
    if rendered:
        normalized = _normalize_narration_phrase(rendered)
        if normalized:
            return normalized
    override = _TOOL_LABEL_OVERRIDES.get(str(tool_name or "").strip())
    if override:
        return override
    return _humanize_tool_name(str(tool_name or ""))


def sanitize_narration_args(payload: dict[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        return {}
    sanitized: dict[str, Any] = {}
    for key, value in payload.items():
        key_text = str(key)
        lowered = key_text.lower()
        if any(part in lowered for part in _SENSITIVE_KEY_PARTS):
            sanitized[key_text] = "***"
            continue
        sanitized[key_text] = _sanitize_value(value)
    return sanitized


def format_recent_trace(entries: list[TraceEntry]) -> str:
    if not entries:
        return "- 暂无"
    lines: list[str] = []
    for item in entries[-4:]:
        summary = str(item.summary or "").strip() or item.event_type
        lines.append(f"- {item.tool_name}: {summary}（{int(item.elapsed_ms)}ms）")
    return "\n".join(lines)


def stringify_narration_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        except TypeError:
            return str(value)
    if isinstance(value, (list, tuple, set)):
        rendered = [stringify_narration_value(item) for item in value]
        return ", ".join(item for item in rendered if item)
    return str(value).strip()


def _try_format(template: str, values: dict[str, str]) -> str | None:
    fields = [field_name for _, field_name, _, _ in _FORMATTER.parse(template) if field_name]
    if any(not values.get(field_name, "").strip() for field_name in fields):
        return None
    try:
        rendered = template.format_map(values)
    except (KeyError, ValueError):
        return None
    normalized = str(rendered or "").strip()
    return normalized or None


def _normalize_narration_phrase(text: str) -> str:
    normalized = str(text or "").strip()
    normalized = _LEADING_DECORATION_RE.sub("", normalized).strip()
    if normalized.startswith("正在"):
        normalized = normalized[2:].strip()
    normalized = _ELLIPSIS_RE.sub("", normalized).strip()
    return normalized or ""


def _humanize_tool_name(tool_name: str) -> str:
    words = [part for part in str(tool_name or "").strip().split("_") if part]
    if not words:
        return "处理当前任务"
    joined = " ".join(words)
    return joined


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return sanitize_narration_args(value)
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_value(item) for item in value]
    return value
