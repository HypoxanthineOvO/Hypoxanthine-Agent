from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import structlog

from hypo_agent.core.config_loader import load_narration_config

logger = structlog.get_logger("hypo_agent.core.channel_progress")

_TOOL_STATUS_TEMPLATES: dict[str, dict[str, str]] = {
    "create_reminder": {
        "start": "🔔 正在创建提醒...",
        "ok": "✅ 提醒创建成功",
        "fail": "❌ 创建提醒失败：{error}",
    },
    "list_reminders": {
        "start": "📋 正在查询提醒列表...",
        "ok": "📋 提醒列表已获取",
        "fail": "❌ 查询提醒失败：{error}",
    },
    "delete_reminder": {
        "start": "🗑️ 正在删除提醒...",
        "ok": "✅ 提醒已删除",
        "fail": "❌ 删除提醒失败：{error}",
    },
    "update_reminder": {
        "start": "✏️ 正在更新提醒...",
        "ok": "✅ 提醒已更新",
        "fail": "❌ 更新提醒失败：{error}",
    },
    "snooze_reminder": {
        "start": "💤 正在延后提醒...",
        "ok": "✅ 提醒已延后",
        "fail": "❌ 延后提醒失败：{error}",
    },
    "run_code": {
        "start": "⚡ 正在执行代码...",
        "ok": "✅ 代码执行完成",
        "fail": "❌ 代码执行失败：{error}",
    },
    "exec_command": {
        "start": "⚡ 正在执行命令...",
        "ok": "✅ 命令执行完成",
        "fail": "❌ 命令执行失败：{error}",
    },
    "web_search": {
        "start": "🔍 正在搜索...",
        "ok": "🔍 搜索完成",
        "fail": "❌ 搜索失败：{error}",
    },
    "_default": {
        "start": "⏳ 正在处理...",
        "ok": "✅ 处理完成",
        "fail": "❌ 处理失败：{error}",
    },
}


@lru_cache(maxsize=1)
def _narration_enabled_tool_names() -> frozenset[str]:
    try:
        config = load_narration_config(Path("config/narration.yaml"))
    except Exception:
        logger.warning("channel_progress.load_narration_config_failed", exc_info=True)
        return frozenset()
    if not config.enabled:
        return frozenset()
    return frozenset({*config.tool_levels.heavy, *config.tool_levels.medium})


def summarize_channel_progress_event(
    event: dict[str, Any],
    *,
    prelude_sent: bool = False,
) -> tuple[str | None, bool]:
    del prelude_sent
    event_type = str(event.get("type") or "").strip().lower()

    if event_type in {"pipeline_stage", "thinking_delta", "react_iteration", "react_complete", "compression"}:
        return None, False

    if event_type == "model_fallback":
        return "⚠️ 主模型暂时不可用，已切换备用模型回复你", False

    if event_type == "model_fallback_exhausted":
        return "❌ 所有模型均不可用，请稍后再试", False

    tool_name = str(event.get("tool_name") or event.get("tool") or "").strip()
    templates = _TOOL_STATUS_TEMPLATES.get(tool_name) or _TOOL_STATUS_TEMPLATES["_default"]
    narration_tools = _narration_enabled_tool_names()

    if event_type == "tool_call_start":
        if tool_name in narration_tools:
            return None, False
        return templates["start"], False

    if event_type == "tool_call_result":
        status = str(event.get("status") or "").strip().lower()
        if status != "success":
            error = str(event.get("error") or event.get("error_info") or "处理失败").strip()
            return templates["fail"].format(error=error), False
        return None, False

    if event_type == "tool_call_error":
        if bool(event.get("will_retry")):
            return None, False
        error = str(event.get("error") or "处理失败").strip()
        return templates["fail"].format(error=error), False

    return None, False
