from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import structlog

from hypo_agent.core.config_loader import load_narration_config
from hypo_agent.core.tool_display import summarize_tool_failure, tool_display
from hypo_agent.core.tool_narration import render_tool_narration

logger = structlog.get_logger("hypo_agent.core.channel_progress")

@lru_cache(maxsize=1)
def _narration_config():
    try:
        config = load_narration_config(Path("config/narration.yaml"))
    except Exception:
        logger.warning("channel_progress.load_narration_config_failed", exc_info=True)
        return None
    return config


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

    if event_type == "model_tool_transform":
        return None, False

    tool_name = str(event.get("tool_name") or event.get("tool") or "").strip()
    display = tool_display(str(event.get("display_name") or tool_name))
    narration_config = _narration_config()

    if event_type == "tool_call_start":
        if narration_config is not None and narration_config.enabled:
            return None, False
        if narration_config is not None:
            rendered = render_tool_narration(
                narration_config,
                tool_name,
                event.get("arguments", {}),
            )
            if rendered:
                return rendered, False
        return display.running_text, False

    if event_type == "tool_call_result":
        status = str(event.get("status") or "").strip().lower()
        if status != "success":
            error = str(event.get("error") or event.get("error_info") or "处理失败").strip()
            summary = str(event.get("summary") or "").strip()
            if summary:
                return summary, False
            return summarize_tool_failure(
                tool_name=tool_name,
                error=error,
                outcome_class=str(event.get("outcome_class") or "").strip() or None,
                attempts=_int_or_none(event.get("attempts")),
                retryable=event.get("retryable") if isinstance(event.get("retryable"), bool) else None,
            ), False
        return None, False

    if event_type == "tool_call_error":
        if bool(event.get("will_retry")):
            return None, False
        error = str(event.get("error") or "处理失败").strip()
        return summarize_tool_failure(
            tool_name=tool_name,
            error=error,
            outcome_class=str(event.get("outcome_class") or "").strip() or None,
            attempts=_int_or_none(event.get("attempts")),
            retryable=event.get("retryable") if isinstance(event.get("retryable"), bool) else None,
        ), False

    return None, False


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
