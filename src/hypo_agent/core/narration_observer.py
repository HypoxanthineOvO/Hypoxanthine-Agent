from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
import re
from typing import Any, Protocol

import structlog

from hypo_agent.core.config_loader import load_narration_config
from hypo_agent.models import NarrationConfig

logger = structlog.get_logger("hypo_agent.narration_observer")

NARRATION_SYSTEM_PROMPT = """你是 Hypo-Agent 的旁白模块。
用一句话、第一人称、口语化地向用户描述你正在做的事情。
要求：
- 简短自然，像朋友间的对话
- 不要用技术术语（不要说"调用 API"、"执行函数"）
- 不要用 emoji
- 不要解释为什么要这么做，只说在做什么
- 最多一句话"""


class NarrationRouter(Protocol):
    def get_model_for_task(self, task_type: str) -> str: ...

    async def call(
        self,
        model_name: str,
        messages: list[dict[str, Any]],
        *,
        session_id: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> str: ...


class NarrationObserver:
    def __init__(
        self,
        *,
        router: NarrationRouter,
        config: NarrationConfig | None = None,
        config_path: Path | str = "config/narration.yaml",
        llm_timeout_seconds: float = 2.0,
        time_fn: Any | None = None,
    ) -> None:
        self.router = router
        self.config = config or load_narration_config(config_path)
        self.llm_timeout_seconds = llm_timeout_seconds
        self._time_fn = time_fn or time.monotonic
        self._last_triggered_at: dict[str, float] = {}
        self._tool_levels = self._build_tool_level_index(self.config)

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    async def maybe_narrate(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        user_message_context: str,
        *,
        session_id: str | None = None,
    ) -> str | None:
        if not self.config.enabled:
            return None

        normalized_tool_name = str(tool_name or "").strip()
        if not normalized_tool_name:
            return None

        level = self._tool_levels.get(normalized_tool_name)
        if level not in {"heavy", "medium"}:
            return None

        now = float(self._time_fn())
        last_triggered = self._last_triggered_at.get(normalized_tool_name)
        if (
            last_triggered is not None
            and now - last_triggered < float(self.config.debounce_seconds)
        ):
            return None
        self._last_triggered_at[normalized_tool_name] = now

        model_name = self._resolve_model_name()
        if not model_name:
            logger.warning("narration.model.unresolved", tool_name=normalized_tool_name)
            return None

        prompt_messages = [
            {"role": "system", "content": NARRATION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"用户说：{self._truncate_text(user_message_context, 240)}\n"
                    f"我现在要执行的操作：{normalized_tool_name}\n"
                    f"操作参数：{self._summarize_tool_args(tool_args)}\n\n"
                    "请用一句话描述我正在做什么。"
                ),
            },
        ]

        try:
            raw_text = await asyncio.wait_for(
                self.router.call(
                    model_name,
                    prompt_messages,
                    session_id=session_id,
                ),
                timeout=self.llm_timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.debug("narration.timeout", tool_name=normalized_tool_name)
            return None
        except (OSError, RuntimeError, TypeError, ValueError):
            logger.exception("narration.failed", tool_name=normalized_tool_name)
            return None

        return self._normalize_output(raw_text)

    def _resolve_model_name(self) -> str:
        configured = str(self.config.model or "").strip()
        if configured and configured.lower() != "lightweight":
            return configured

        getter = getattr(self.router, "get_model_for_task", None)
        if callable(getter):
            return str(getter("lightweight") or "").strip()
        return configured

    def _summarize_tool_args(self, tool_args: dict[str, Any]) -> str:
        if not tool_args:
            return "{}"
        try:
            rendered = json.dumps(tool_args, ensure_ascii=False, separators=(",", ":"))
        except TypeError:
            rendered = str(tool_args)
        return self._truncate_text(rendered, 240)

    def _normalize_output(self, raw_text: Any) -> str | None:
        text = re.sub(r"\s+", " ", str(raw_text or "")).strip().strip("\"'“”")
        if not text:
            return None
        max_length = int(self.config.max_narration_length)
        if len(text) > max_length:
            text = text[:max_length].rstrip()
        return text or None

    def _truncate_text(self, text: Any, limit: int) -> str:
        normalized = re.sub(r"\s+", " ", str(text or "")).strip()
        if len(normalized) <= limit:
            return normalized
        return normalized[:limit].rstrip()

    def _build_tool_level_index(self, config: NarrationConfig) -> dict[str, str]:
        index: dict[str, str] = {}
        for name in config.tool_levels.heavy:
            normalized = str(name or "").strip()
            if normalized:
                index[normalized] = "heavy"
        for name in config.tool_levels.medium:
            normalized = str(name or "").strip()
            if normalized:
                index[normalized] = "medium"
        return index
