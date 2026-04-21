from __future__ import annotations

import asyncio
from collections import deque
from difflib import SequenceMatcher
import json
import time
from pathlib import Path
import re
from typing import Any, Protocol

import httpx
import structlog
try:
    from litellm import acompletion as litellm_acompletion
except ImportError:  # pragma: no cover - runtime dependent
    litellm_acompletion = None

from hypo_agent.core.config_loader import load_narration_config
from hypo_agent.core.model_request_options import build_model_request_kwargs
from hypo_agent.core.tool_narration import (
    NarrationContext,
    TraceEntry,
    describe_tool_for_narration,
    format_recent_trace,
    render_tool_narration,
    sanitize_narration_args,
)
from hypo_agent.models import NarrationConfig

logger = structlog.get_logger("hypo_agent.narration_observer")

NARRATION_SYSTEM_PROMPT = """你是 Hypo-Agent 的旁白模块。
用一句话、口语化地向用户描述你正在做的事情。
要求：
- 简短自然，像朋友间的对话
- 不要用技术术语（不要说"调用 API"、"执行函数"）
- 尽量带上标题、关键词或路径等关键信息
- 不要解释为什么要这么做，只说在做什么
- 不要暴露工具名、函数名、技术细节
- 最多 25 个字
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
        llm_timeout_seconds: float | None = None,
        time_fn: Any | None = None,
    ) -> None:
        self.router = router
        self.config = config or load_narration_config(config_path)
        self.llm_timeout_seconds = (
            float(llm_timeout_seconds)
            if llm_timeout_seconds is not None
            else max(0.05, float(self.config.llm_timeout_ms) / 1000.0)
        )
        self._time_fn = time_fn or time.monotonic
        self._tool_levels = self._build_tool_level_index(self.config)
        self._llm_cache: dict[tuple[str, str, str], str | None] = {}
        self._last_text_by_session: dict[str, tuple[str, int]] = {}
        self._recent_trace_by_session: dict[str, deque[TraceEntry]] = {}
        self._llm_ready = True

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
        iteration_number: int = 0,
        total_tools_called: int = 0,
    ) -> str | None:
        if not self.config.enabled:
            return None

        normalized_tool_name = str(tool_name or "").strip()
        if not normalized_tool_name:
            return None

        session_key = str(session_id or "__global__")
        template_text = render_tool_narration(self.config, normalized_tool_name, tool_args)
        if template_text:
            return self._dedupe_text(session_key, template_text)

        if self._tool_levels.get(normalized_tool_name) not in {"heavy", "medium"}:
            return None
        if not self._llm_ready:
            return None

        context = self._build_context(
            session_key=session_key,
            tool_name=normalized_tool_name,
            tool_args=tool_args,
            user_message_context=user_message_context,
            iteration_number=iteration_number,
            total_tools_called=total_tools_called,
        )
        llm_text = await self._maybe_generate_llm_narration(
            session_key=session_key,
            tool_name=normalized_tool_name,
            context=context,
        )
        if llm_text:
            return self._dedupe_text(session_key, llm_text)
        return None

    def set_llm_ready(self, ready: bool) -> None:
        self._llm_ready = bool(ready)

    def is_llm_ready(self) -> bool:
        return self._llm_ready

    def record_trace_event(
        self,
        *,
        session_id: str | None,
        event_type: str,
        tool_name: str,
        summary: str = "",
        elapsed_ms: int = 0,
    ) -> None:
        session_key = str(session_id or "__global__")
        trace = self._recent_trace_by_session.setdefault(session_key, deque(maxlen=8))
        trace.append(
            TraceEntry(
                event_type=str(event_type or "").strip(),
                tool_name=describe_tool_for_narration(self.config, tool_name),
                summary=str(summary or "").strip(),
                elapsed_ms=max(0, int(elapsed_ms)),
            )
        )

    def _resolve_model_name(self) -> str:
        configured = str(self.config.model or "").strip()
        if configured and configured.lower() != "lightweight":
            return configured

        getter = getattr(self.router, "get_model_for_task", None)
        if callable(getter):
            return str(getter("lightweight") or "").strip()
        return configured

    def _resolve_runtime_model_config(self, model_name: str) -> Any | None:
        runtime_config = getattr(self.router, "config", None)
        runtime_models = getattr(runtime_config, "models", {}) if runtime_config is not None else {}
        return runtime_models.get(model_name)

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
        max_length = min(25, int(self.config.max_narration_length))
        if len(text) > max_length:
            text = text[:max_length].rstrip()
        return text or None

    def _truncate_text(self, text: Any, limit: int) -> str:
        normalized = re.sub(r"\s+", " ", str(text or "")).strip()
        if len(normalized) <= limit:
            return normalized
        return normalized[:limit].rstrip()

    async def _maybe_generate_llm_narration(
        self,
        *,
        session_key: str,
        tool_name: str,
        context: NarrationContext,
    ) -> str | None:
        cache_key = (
            session_key,
            tool_name,
            self._summarize_context_cache_key(context),
        )
        if cache_key in self._llm_cache:
            return self._llm_cache[cache_key]

        model_name = self._resolve_model_name()
        if not model_name:
            logger.warning("narration.model.unresolved", tool_name=tool_name)
            self._llm_cache[cache_key] = None
            return None

        prompt_messages = [
            {
                "role": "user",
                "content": (
                    f"{NARRATION_SYSTEM_PROMPT}\n\n"
                    "当前状态：\n"
                    f"用户说：{self._truncate_text(context.user_message, 240)}\n"
                    f"我正在做：{context.current_tool}（参数：{self._summarize_tool_args(context.current_args)}）\n"
                    f"已经做了 {context.total_tools_called} 步，这是第 {context.iteration_number} 轮。\n"
                    "最近几步：\n"
                    f"{format_recent_trace(context.recent_trace)}\n\n"
                    "请用一句简短中文描述我正在做什么或正在想什么。"
                ),
            }
        ]

        try:
            raw_text = await asyncio.wait_for(
                self._call_narration_model(
                    model_name=model_name,
                    prompt_messages=prompt_messages,
                    session_id=None if session_key == "__global__" else session_key,
                ),
                timeout=self.llm_timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.debug("narration.timeout", tool_name=tool_name)
            self._llm_cache[cache_key] = None
            return None
        except (OSError, RuntimeError, TypeError, ValueError):
            logger.exception("narration.failed", tool_name=tool_name)
            self._llm_cache[cache_key] = None
            return None

        normalized = self._normalize_output(raw_text)
        self._llm_cache[cache_key] = normalized
        return normalized

    async def _call_narration_model(
        self,
        *,
        model_name: str,
        prompt_messages: list[dict[str, str]],
        session_id: str | None,
    ) -> str:
        model_config = self._resolve_runtime_model_config(model_name)
        if model_config is not None and litellm_acompletion is not None:
            kwargs: dict[str, Any] = {
                "model": str(getattr(model_config, "litellm_model", "") or ""),
                "messages": prompt_messages,
                "max_tokens": 40,
            }
            api_base = str(getattr(model_config, "api_base", "") or "").strip()
            api_key = str(getattr(model_config, "api_key", "") or "").strip()
            provider = str(getattr(model_config, "provider", "") or "").strip()
            if api_base:
                kwargs["api_base"] = api_base
            if api_key:
                kwargs["api_key"] = api_key
            if is_local_vllm_model(provider=provider, api_base=api_base):
                kwargs["chat_template_kwargs"] = {"enable_thinking": False}
            kwargs.update(
                build_model_request_kwargs(
                    model_config=model_config,
                    litellm_model=getattr(model_config, "litellm_model", None),
                    provider=provider,
                    api_base=api_base,
                    reasoning_config=getattr(model_config, "reasoning_config", None),
                    task_type="lightweight",
                )
            )
            payload = await litellm_acompletion(**kwargs)
            return self._extract_completion_text(payload)

        return await self.router.call(
            model_name,
            prompt_messages,
            session_id=session_id,
        )

    def _extract_completion_text(self, payload: Any) -> str:
        choices = getattr(payload, "choices", None) or []
        if not choices:
            return ""
        first = choices[0]
        message = getattr(first, "message", None)
        if message is None:
            return ""
        content = getattr(message, "content", None)
        if isinstance(content, str) and content.strip():
            return content
        reasoning = getattr(message, "reasoning_content", None)
        if isinstance(reasoning, str):
            return reasoning
        provider_fields = getattr(message, "provider_specific_fields", None)
        if isinstance(provider_fields, dict):
            for key in ("reasoning_content", "reasoning"):
                value = provider_fields.get(key)
                if isinstance(value, str):
                    return value
        return ""

    def _build_context(
        self,
        *,
        session_key: str,
        tool_name: str,
        tool_args: dict[str, Any],
        user_message_context: str,
        iteration_number: int,
        total_tools_called: int,
    ) -> NarrationContext:
        return NarrationContext(
            user_message=self._truncate_text(user_message_context, 240),
            current_tool=describe_tool_for_narration(self.config, tool_name, tool_args),
            current_args=sanitize_narration_args(tool_args),
            recent_trace=list(self._recent_trace_by_session.get(session_key, deque()))[-4:],
            iteration_number=max(1, int(iteration_number or 1)),
            total_tools_called=max(0, int(total_tools_called or 0)),
        )

    def _summarize_context_cache_key(self, context: NarrationContext) -> str:
        payload = {
            "current_tool": context.current_tool,
            "current_args": context.current_args,
            "recent_trace": [
                {
                    "event_type": item.event_type,
                    "tool_name": item.tool_name,
                    "summary": item.summary,
                    "elapsed_ms": item.elapsed_ms,
                }
                for item in context.recent_trace
            ],
            "iteration_number": context.iteration_number,
            "total_tools_called": context.total_tools_called,
        }
        return self._summarize_tool_args(payload)

    def _dedupe_text(self, session_key: str, text: str | None) -> str | None:
        normalized = str(text or "").strip()
        if not normalized:
            return None

        previous_text, previous_count = self._last_text_by_session.get(session_key, ("", 0))
        if previous_text == normalized:
            next_count = previous_count + 1
            self._last_text_by_session[session_key] = (normalized, next_count)
            if next_count > int(self.config.dedup_max_consecutive):
                return None
            return normalized

        if previous_text and SequenceMatcher(None, previous_text, normalized).ratio() > 0.7:
            return None

        next_count = 1
        self._last_text_by_session[session_key] = (normalized, next_count)
        return normalized

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


def is_local_vllm_model(*, provider: str | None, api_base: str | None) -> bool:
    provider_name = str(provider or "").strip().lower()
    base_url = str(api_base or "").strip().lower()
    return provider_name == "genesislocal" or "localhost:18081" in base_url or "127.0.0.1:18081" in base_url


def probe_local_vllm_model(
    *,
    api_base: str,
    api_key: str | None,
    transport: httpx.BaseTransport | None = None,
) -> bool:
    url = str(api_base or "").rstrip("/") + "/models"
    if not url or url == "/models":
        return False
    headers: dict[str, str] = {}
    if str(api_key or "").strip():
        headers["Authorization"] = f"Bearer {str(api_key).strip()}"
    try:
        with httpx.Client(timeout=2.0, transport=transport) as client:
            response = client.get(url, headers=headers)
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError, TypeError):
        return False
    return isinstance(payload, dict) and isinstance(payload.get("data"), list)
