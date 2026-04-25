from __future__ import annotations

import json
import inspect
from copy import deepcopy
from collections.abc import AsyncIterator
from collections.abc import Awaitable, Callable
import asyncio
import re
from time import perf_counter
from typing import Any

import structlog
try:
    from litellm import acompletion as litellm_acompletion
    from litellm import aembedding as litellm_aembedding
    from litellm.exceptions import OpenAIError as LiteLLMOpenAIError
except ImportError:  # pragma: no cover - depends on runtime environment
    litellm_acompletion = None
    litellm_aembedding = None
    LiteLLMOpenAIError = None

try:
    from openai import OpenAIError as OpenAIClientError
except ImportError:  # pragma: no cover - optional runtime dependency
    OpenAIClientError = None

from hypo_agent.core.antigravity_compat import (
    is_antigravity_provider,
    restore_antigravity_tool_call_names,
    transform_antigravity_tools,
)
from hypo_agent.core.config_loader import RuntimeModelConfig
from hypo_agent.core.model_runtime_context import (
    RUNTIME_MODEL_CONTEXT_HEADING,
    build_runtime_model_context,
)
from hypo_agent.core.model_request_options import build_model_request_kwargs

_MODEL_ROUTER_BASE_ERRORS = (
    asyncio.TimeoutError,
    TimeoutError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)
_MODEL_ROUTER_PROVIDER_ERRORS = tuple(
    error_type
    for error_type in (LiteLLMOpenAIError, OpenAIClientError)
    if isinstance(error_type, type)
)
_MODEL_ROUTER_ERRORS = _MODEL_ROUTER_BASE_ERRORS + _MODEL_ROUTER_PROVIDER_ERRORS


class ModelRouter:
    def __init__(
        self,
        config: RuntimeModelConfig,
        acompletion_fn=None,
        aembedding_fn=None,
        on_stream_success: Callable[[dict[str, Any]], Awaitable[None] | None]
        | None = None,
        embed_retry_attempts: int = 3,
        embed_retry_backoff_seconds: float = 0.5,
    ) -> None:
        self.config = config
        self._acompletion = acompletion_fn or litellm_acompletion
        if self._acompletion is None:
            raise RuntimeError(
                "litellm is not installed and no acompletion_fn was provided"
            )
        self._aembedding = aembedding_fn or litellm_aembedding
        self._on_stream_success = on_stream_success
        self._embed_retry_attempts = max(1, int(embed_retry_attempts))
        self._embed_retry_backoff_seconds = max(0.0, float(embed_retry_backoff_seconds))
        self.logger = structlog.get_logger("hypo_agent.model_router")

    async def call(
        self,
        model_name: str,
        messages: list[dict[str, Any]],
        *,
        session_id: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        timeout_seconds: float | None = None,
        task_type: str | None = None,
        event_emitter: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None,
    ) -> str:
        payload = await self.call_with_tools(
            model_name,
            messages,
            tools=tools,
            session_id=session_id,
            timeout_seconds=timeout_seconds,
            task_type=task_type,
            event_emitter=event_emitter,
        )
        return payload["text"]

    async def call_with_tools(
        self,
        model_name: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        session_id: str | None = None,
        timeout_seconds: float | None = None,
        task_type: str | None = None,
        event_emitter: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None,
    ) -> dict[str, Any]:
        attempted: list[str] = []
        last_error: Exception | None = None
        started_at = perf_counter()
        candidate_chain = self._candidate_chain(model_name)

        for index, candidate in enumerate(candidate_chain):
            cfg = self.config.models[candidate]
            if cfg.provider is None or cfg.litellm_model is None:
                attempted.append(f"{candidate}(skipped)")
                self.logger.info(
                    "model_skipped",
                    requested_model=model_name,
                    resolved_model=candidate,
                    reason="provider_or_litellm_model_missing",
                )
                continue
            if tools and cfg.supports_tool_calling is False:
                attempted.append(f"{candidate}(no-tools)")
                self.logger.warning(
                    "model_skipped",
                    requested_model=model_name,
                    resolved_model=candidate,
                    reason="tool_calling_not_supported",
                )
                continue

            try:
                runtime_messages = self._apply_runtime_model_context(
                    messages,
                    requested_model=model_name,
                    resolved_model=candidate,
                    task_type=task_type,
                )
                prepared_messages, remapped_ids = self._prepare_messages_for_candidate(
                    runtime_messages,
                    cfg.litellm_model,
                    cfg.provider,
                    cfg.api_base,
                )
                kwargs: dict[str, Any] = {
                    "model": cfg.litellm_model,
                    "messages": prepared_messages,
                }
                if isinstance(cfg.api_base, str) and cfg.api_base.strip():
                    kwargs["api_base"] = cfg.api_base
                if isinstance(cfg.api_key, str) and cfg.api_key.strip():
                    kwargs["api_key"] = cfg.api_key
                transformed_tools = tools
                reverse_tool_name_map: dict[str, str] = {}
                if tools is not None:
                    try:
                        transform_result = self._transform_tools_for_candidate(
                            tools,
                            provider=cfg.provider,
                        )
                    except Exception:
                        self.logger.exception(
                            "model_tool_transform_failed",
                            requested_model=model_name,
                            resolved_model=candidate,
                            provider=cfg.provider,
                        )
                        transform_result = None
                    if transform_result is not None:
                        transformed_tools = transform_result.tools
                        reverse_tool_name_map = dict(transform_result.reverse_name_map)
                        if transform_result.renamed_tools:
                            self.logger.info(
                                "model_tool_transform_applied",
                                requested_model=model_name,
                                resolved_model=candidate,
                                provider=cfg.provider,
                                renamed_tools=transform_result.renamed_tools,
                            )
                            await self._emit_model_event(
                                event_emitter,
                                {
                                    "type": "model_tool_transform",
                                    "requested_model": model_name,
                                    "resolved_model": candidate,
                                    "provider": cfg.provider,
                                    "session_id": session_id,
                                    "renamed_tools": transform_result.renamed_tools,
                                },
                            )
                    kwargs["tools"] = transformed_tools
                if timeout_seconds is not None:
                    kwargs["timeout"] = timeout_seconds
                kwargs.update(
                    build_model_request_kwargs(
                        model_config=cfg,
                        litellm_model=cfg.litellm_model,
                        task_type=task_type,
                    )
                )
                if remapped_ids:
                    self.logger.info(
                        "tool_call_ids_sanitized",
                        requested_model=model_name,
                        resolved_model=candidate,
                        remapped_ids=remapped_ids,
                    )
                self.logger.debug(
                    "call_with_tools.request",
                    model=cfg.litellm_model,
                    tools_count=len(transformed_tools or []),
                    messages_count=len(prepared_messages),
                )

                response = await self._acompletion(**kwargs)
                text = self._extract_message_text(response)
                has_tool_calls = self._has_tool_call_payload(response)
                self.logger.debug(
                    "call_with_tools.response_raw",
                    has_tool_calls=has_tool_calls,
                    text_length=len(text),
                )
                tool_calls = self._extract_tool_calls(response)
                tool_calls = restore_antigravity_tool_call_names(
                    tool_calls,
                    reverse_tool_name_map,
                )
                reasoning_content = self._extract_reasoning_content(response)
                assistant_message: dict[str, Any] = {
                    "role": "assistant",
                    "content": text,
                }
                if tool_calls:
                    assistant_message["tool_calls"] = tool_calls
                if reasoning_content:
                    assistant_message["reasoning_content"] = reasoning_content
                usage = self._extract_usage(response)
                self.logger.info(
                    "model_call_success",
                    requested_model=model_name,
                    resolved_model=candidate,
                    input_tokens=usage["input_tokens"],
                    output_tokens=usage["output_tokens"],
                    total_tokens=usage["total_tokens"],
                )
                await self._emit_stream_success(
                    {
                        "event": "model_call_success",
                        "session_id": session_id,
                        "requested_model": model_name,
                        "resolved_model": candidate,
                        "input_tokens": usage["input_tokens"],
                        "output_tokens": usage["output_tokens"],
                        "total_tokens": usage["total_tokens"],
                        "latency_ms": (perf_counter() - started_at) * 1000.0,
                    }
                )
                return {
                    "text": text,
                    "tool_calls": tool_calls,
                    "assistant_message": assistant_message,
                    "resolved_model": candidate,
                    "model_id": cfg.litellm_model,
                }
            except _MODEL_ROUTER_ERRORS as exc:  # pragma: no cover - exercised in tests
                attempted.append(candidate)
                last_error = exc
                self.logger.warning(
                    "model_call_failed",
                    requested_model=model_name,
                    resolved_model=candidate,
                    error=str(exc),
                )
                await self._emit_fallback_event(
                    requested_model=model_name,
                    candidate_chain=candidate_chain,
                    failed_index=index,
                    reason=str(exc),
                    provider=cfg.provider,
                    session_id=session_id,
                    event_emitter=event_emitter,
                )

        await self._emit_fallback_exhausted_event(
            requested_model=model_name,
            candidate_chain=candidate_chain,
            reason=str(last_error or "unknown model failure"),
            session_id=session_id,
            event_emitter=event_emitter,
        )
        raise RuntimeError(
            f"All models failed for '{model_name}'. Attempted chain: {attempted}"
        ) from last_error

    async def stream(
        self,
        model_name: str,
        messages: list[dict[str, Any]],
        *,
        session_id: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        timeout_seconds: float | None = None,
        task_type: str | None = None,
        event_emitter: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None,
    ) -> AsyncIterator[str]:
        attempted: list[str] = []
        last_error: Exception | None = None
        started_at = perf_counter()
        candidate_chain = self._candidate_chain(model_name)

        for index, candidate in enumerate(candidate_chain):
            cfg = self.config.models[candidate]
            if cfg.provider is None or cfg.litellm_model is None:
                attempted.append(f"{candidate}(skipped)")
                self.logger.info(
                    "model_skipped",
                    requested_model=model_name,
                    resolved_model=candidate,
                    reason="provider_or_litellm_model_missing",
                )
                continue
            if tools and cfg.supports_tool_calling is False:
                attempted.append(f"{candidate}(no-tools)")
                self.logger.warning(
                    "model_skipped",
                    requested_model=model_name,
                    resolved_model=candidate,
                    reason="tool_calling_not_supported",
                )
                continue

            started = False
            usage = {"input_tokens": None, "output_tokens": None, "total_tokens": None}

            try:
                runtime_messages = self._apply_runtime_model_context(
                    messages,
                    requested_model=model_name,
                    resolved_model=candidate,
                    task_type=task_type,
                )
                prepared_messages, remapped_ids = self._prepare_messages_for_candidate(
                    runtime_messages,
                    cfg.litellm_model,
                    cfg.provider,
                    cfg.api_base,
                )
                kwargs: dict[str, Any] = {
                    "model": cfg.litellm_model,
                    "messages": prepared_messages,
                    "stream": True,
                }
                if isinstance(cfg.api_base, str) and cfg.api_base.strip():
                    kwargs["api_base"] = cfg.api_base
                if isinstance(cfg.api_key, str) and cfg.api_key.strip():
                    kwargs["api_key"] = cfg.api_key
                transformed_tools = tools
                if tools is not None:
                    try:
                        transform_result = self._transform_tools_for_candidate(
                            tools,
                            provider=cfg.provider,
                        )
                    except Exception:
                        self.logger.exception(
                            "model_tool_transform_failed",
                            requested_model=model_name,
                            resolved_model=candidate,
                            provider=cfg.provider,
                        )
                        transform_result = None
                    if transform_result is not None:
                        transformed_tools = transform_result.tools
                        if transform_result.renamed_tools:
                            self.logger.info(
                                "model_tool_transform_applied",
                                requested_model=model_name,
                                resolved_model=candidate,
                                provider=cfg.provider,
                                renamed_tools=transform_result.renamed_tools,
                            )
                            await self._emit_model_event(
                                event_emitter,
                                {
                                    "type": "model_tool_transform",
                                    "requested_model": model_name,
                                    "resolved_model": candidate,
                                    "provider": cfg.provider,
                                    "session_id": session_id,
                                    "renamed_tools": transform_result.renamed_tools,
                                },
                            )
                    kwargs["tools"] = transformed_tools
                if timeout_seconds is not None:
                    kwargs["timeout"] = timeout_seconds
                kwargs.update(
                    build_model_request_kwargs(
                        model_config=cfg,
                        litellm_model=cfg.litellm_model,
                        task_type=task_type,
                    )
                )
                if remapped_ids:
                    self.logger.info(
                        "tool_call_ids_sanitized",
                        requested_model=model_name,
                        resolved_model=candidate,
                        remapped_ids=remapped_ids,
                    )

                response_stream = await self._acompletion(**kwargs)

                async for chunk in response_stream:
                    usage = self._extract_usage(chunk, default=usage)
                    text = self._extract_delta_text(chunk)
                    if not text:
                        continue
                    started = True
                    yield text

                event_payload = {
                    "event": "model_stream_success",
                    "session_id": session_id,
                    "requested_model": model_name,
                    "resolved_model": candidate,
                    "input_tokens": usage["input_tokens"],
                    "output_tokens": usage["output_tokens"],
                    "total_tokens": usage["total_tokens"],
                    "latency_ms": (perf_counter() - started_at) * 1000.0,
                }
                self.logger.info(
                    "model_stream_success",
                    session_id=session_id,
                    requested_model=model_name,
                    resolved_model=candidate,
                    input_tokens=usage["input_tokens"],
                    output_tokens=usage["output_tokens"],
                    total_tokens=usage["total_tokens"],
                    latency_ms=event_payload["latency_ms"],
                )
                await self._emit_stream_success(event_payload)
                return
            except _MODEL_ROUTER_ERRORS as exc:  # pragma: no cover - exercised in tests
                if started:
                    self.logger.error(
                        "model_stream_failed_after_output",
                        requested_model=model_name,
                        resolved_model=candidate,
                        error=str(exc),
                    )
                    raise

                attempted.append(candidate)
                last_error = exc
                self.logger.warning(
                    "model_stream_failed",
                    requested_model=model_name,
                    resolved_model=candidate,
                    error=str(exc),
                )
                await self._emit_fallback_event(
                    requested_model=model_name,
                    candidate_chain=candidate_chain,
                    failed_index=index,
                    reason=str(exc),
                    provider=cfg.provider,
                    session_id=session_id,
                    event_emitter=event_emitter,
                )

        await self._emit_fallback_exhausted_event(
            requested_model=model_name,
            candidate_chain=candidate_chain,
            reason=str(last_error or "unknown model failure"),
            session_id=session_id,
            event_emitter=event_emitter,
        )
        raise RuntimeError(
            f"All stream models failed for '{model_name}'. Attempted chain: {attempted}"
        ) from last_error

    def get_model_for_task(self, task_type: str) -> str:
        routed_model = self.config.task_routing.get(task_type)
        if routed_model and routed_model in self.config.models:
            return routed_model
        if routed_model:
            self.logger.warning(
                "task_routing_model_missing",
                task_type=task_type,
                configured_model=routed_model,
                fallback_model=self.config.default_model,
            )
        return self.config.default_model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if self._aembedding is None:
            raise RuntimeError("litellm embedding is not installed and no aembedding_fn was provided")

        model_name = self.get_model_for_task("embedding")
        attempted: list[str] = []
        last_error: Exception | None = None

        for candidate in self._candidate_chain(model_name):
            cfg = self.config.models[candidate]
            if cfg.provider is None or cfg.litellm_model is None:
                attempted.append(f"{candidate}(skipped)")
                self.logger.info(
                    "embedding_model_skipped",
                    requested_model=model_name,
                    resolved_model=candidate,
                    reason="provider_or_litellm_model_missing",
                )
                continue

            kwargs: dict[str, Any] = {
                "model": cfg.litellm_model,
                "input": texts,
            }
            if isinstance(cfg.api_base, str) and cfg.api_base.strip():
                kwargs["api_base"] = cfg.api_base
            if isinstance(cfg.api_key, str) and cfg.api_key.strip():
                kwargs["api_key"] = cfg.api_key

            for attempt in range(1, self._embed_retry_attempts + 1):
                try:
                    response = await self._aembedding(**kwargs)
                    embeddings = self._extract_embeddings(response)
                    if len(embeddings) != len(texts):
                        raise RuntimeError(
                            "Embedding response length mismatch: "
                            f"expected {len(texts)}, got {len(embeddings)}"
                        )
                    return embeddings
                except _MODEL_ROUTER_ERRORS as exc:  # pragma: no cover - exercised in tests
                    last_error = exc
                    attempted.append(f"{candidate}#{attempt}")
                    self.logger.warning(
                        "embedding_call_failed",
                        requested_model=model_name,
                        resolved_model=candidate,
                        attempt=attempt,
                        error=str(exc),
                    )
                    if attempt >= self._embed_retry_attempts:
                        break
                    await asyncio.sleep(self._embed_retry_backoff_seconds * attempt)

        raise RuntimeError(
            f"All embedding models failed for '{model_name}'. Attempted chain: {attempted}"
        ) from last_error

    async def call_lightweight_json(
        self,
        prompt: str,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        model_name = self.get_model_for_task("lightweight")
        text = await self.call(
            model_name,
            [{"role": "user", "content": prompt}],
            session_id=session_id,
            task_type="lightweight",
        )
        parsed = self._parse_json_object_from_text(text)
        return parsed if isinstance(parsed, dict) else {}

    def get_fallback_chain(self, start_model: str) -> list[str]:
        return self._candidate_chain(start_model)

    async def _emit_stream_success(self, payload: dict[str, Any]) -> None:
        if self._on_stream_success is None:
            return

        result = self._on_stream_success(payload)
        if inspect.isawaitable(result):
            await result

    async def _emit_model_event(
        self,
        event_emitter: Callable[[dict[str, Any]], Awaitable[None] | None] | None,
        payload: dict[str, Any],
    ) -> None:
        if event_emitter is None:
            return
        result = event_emitter(payload)
        if inspect.isawaitable(result):
            await result

    async def _emit_fallback_event(
        self,
        *,
        requested_model: str,
        candidate_chain: list[str],
        failed_index: int,
        reason: str,
        provider: str | None,
        session_id: str | None,
        event_emitter: Callable[[dict[str, Any]], Awaitable[None] | None] | None,
    ) -> None:
        next_index = failed_index + 1
        if next_index >= len(candidate_chain):
            return
        failed_model = candidate_chain[failed_index]
        fallback_model = candidate_chain[next_index]
        self.logger.warning(
            "model.fallback",
            requested_model=requested_model,
            triggered=failed_model,
            reason=reason,
            fallback=fallback_model,
        )
        await self._emit_model_event(
            event_emitter,
            {
                "type": "model_fallback",
                "failed_model": failed_model,
                "reason": reason,
                "fallback_model": fallback_model,
                "requested_model": requested_model,
                "provider": provider,
                "session_id": session_id,
            },
        )

    async def _emit_fallback_exhausted_event(
        self,
        *,
        requested_model: str,
        candidate_chain: list[str],
        reason: str,
        session_id: str | None,
        event_emitter: Callable[[dict[str, Any]], Awaitable[None] | None] | None,
    ) -> None:
        failed_model = candidate_chain[-1] if candidate_chain else requested_model
        self.logger.error(
            "model.fallback_exhausted",
            requested_model=requested_model,
            triggered=failed_model,
            reason=reason,
        )
        await self._emit_model_event(
            event_emitter,
            {
                "type": "model_fallback_exhausted",
                "failed_model": failed_model,
                "reason": reason,
                "requested_model": requested_model,
                "session_id": session_id,
            },
        )

    def _apply_runtime_model_context(
        self,
        messages: list[dict[str, Any]],
        *,
        requested_model: str,
        resolved_model: str,
        task_type: str | None,
    ) -> list[dict[str, Any]]:
        cfg = self.config.models.get(resolved_model)
        model_id = cfg.litellm_model if cfg is not None else resolved_model
        runtime_context = build_runtime_model_context(
            model_display_name=resolved_model,
            model_id=model_id,
            task_type=task_type,
            primary_model_display_name=requested_model,
        )
        updated_messages: list[dict[str, Any]] = []
        replaced = False
        for message in messages:
            if (
                not replaced
                and isinstance(message, dict)
                and message.get("role") == "system"
                and str(message.get("content") or "").startswith(RUNTIME_MODEL_CONTEXT_HEADING)
            ):
                updated_message = dict(message)
                updated_message["content"] = runtime_context
                updated_messages.append(updated_message)
                replaced = True
                continue
            updated_messages.append(message)
        if replaced:
            return updated_messages
        return [{"role": "system", "content": runtime_context}, *messages]

    def _candidate_chain(self, start_model: str) -> list[str]:
        if start_model not in self.config.models:
            raise ValueError(f"Unknown model '{start_model}'")

        chain: list[str] = []
        visited: set[str] = set()
        current = start_model
        while current is not None:
            if current in visited:
                raise ValueError(f"Fallback cycle detected at '{current}'")
            if current not in self.config.models:
                raise ValueError(f"Model '{current}' not found in configuration")
            visited.add(current)
            chain.append(current)
            current = self.config.models[current].fallback
        return chain

    def _extract_message_text(self, payload: Any) -> str:
        choices = self._read_field(payload, "choices") or []
        if not choices:
            return ""

        first_choice = choices[0]
        message = self._read_field(first_choice, "message")
        if message is None:
            return ""

        content = self._read_field(message, "content")
        return self._normalize_content(content)

    def _extract_tool_calls(self, payload: Any) -> list[dict[str, Any]]:
        choices = self._read_field(payload, "choices") or []
        if not choices:
            return []

        first_choice = choices[0]
        message = self._read_field(first_choice, "message")
        if message is None:
            return []

        raw_calls = self._read_field(message, "tool_calls")
        if raw_calls is None:
            raw_calls = []
        if isinstance(raw_calls, dict):
            raw_calls = [raw_calls]
        if not isinstance(raw_calls, list):
            raw_calls = []

        normalized: list[dict[str, Any]] = []
        for idx, item in enumerate(raw_calls):
            normalized_item = self._normalize_tool_call_item(item, default_id=f"call_{idx + 1}")
            if normalized_item is not None:
                normalized.append(normalized_item)
        if normalized:
            return normalized

        # Legacy function-call format (some providers expose this shape).
        function_call = self._read_field(message, "function_call")
        normalized_function_call = self._normalize_function_call(
            function_call,
            default_id="call_1",
        )
        if normalized_function_call is not None:
            return [normalized_function_call]

        # Gemini-style content parts may include structured function call data.
        content = self._read_field(message, "content")
        if isinstance(content, list):
            for idx, part in enumerate(content):
                normalized_part = self._normalize_gemini_part_tool_call(
                    part,
                    default_id=f"call_{idx + 1}",
                )
                if normalized_part is not None:
                    normalized.append(normalized_part)
            if normalized:
                return normalized

        if isinstance(content, str):
            return self._extract_text_embedded_tool_calls(content)
        return normalized

    def _extract_reasoning_content(self, payload: Any) -> str:
        choices = self._read_field(payload, "choices") or []
        if not choices:
            return ""

        first_choice = choices[0]
        message = self._read_field(first_choice, "message")
        if message is None:
            return ""

        reasoning = self._read_field(message, "reasoning_content")
        if isinstance(reasoning, str) and reasoning:
            return reasoning

        provider_fields = self._read_field(message, "provider_specific_fields")
        if isinstance(provider_fields, dict):
            for key in ("reasoning_content", "reasoning"):
                value = provider_fields.get(key)
                if isinstance(value, str) and value:
                    return value
        return ""

    def _extract_embeddings(self, payload: Any) -> list[list[float]]:
        data = self._read_field(payload, "data") or []
        if not isinstance(data, list):
            return []

        embeddings: list[list[float]] = []
        for item in data:
            raw_embedding = self._read_field(item, "embedding")
            if not isinstance(raw_embedding, list):
                continue
            embeddings.append([float(value) for value in raw_embedding])
        return embeddings

    def _has_tool_call_payload(self, payload: Any) -> bool:
        return bool(self._extract_tool_calls(payload))

    def _prepare_messages_for_candidate(
        self,
        messages: list[dict[str, Any]],
        litellm_model: str | None,
        provider: str | None = None,
        api_base: str | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        messages = self._normalize_messages_for_candidate(
            messages,
            litellm_model=litellm_model,
            provider=provider,
            api_base=api_base,
        )
        if not self._candidate_requires_fc_tool_call_ids(litellm_model):
            return messages, []

        id_mapping = self._build_tool_call_id_mapping(messages)
        if not id_mapping:
            return messages, []

        prepared_messages: list[dict[str, Any]] = []
        changed = False
        for message in messages:
            if not isinstance(message, dict):
                prepared_messages.append(message)
                continue

            role = message.get("role")
            updated_message = message

            if role == "assistant":
                tool_calls = message.get("tool_calls")
                if isinstance(tool_calls, list):
                    updated_tool_calls: list[Any] = []
                    tool_calls_changed = False
                    for tool_call in tool_calls:
                        if not isinstance(tool_call, dict):
                            updated_tool_calls.append(tool_call)
                            continue
                        original_id = tool_call.get("id")
                        replacement_id = (
                            id_mapping.get(original_id)
                            if isinstance(original_id, str)
                            else None
                        )
                        if replacement_id is None:
                            updated_tool_calls.append(tool_call)
                            continue
                        updated_tool_call = deepcopy(tool_call)
                        updated_tool_call["id"] = replacement_id
                        updated_tool_calls.append(updated_tool_call)
                        tool_calls_changed = True
                    if tool_calls_changed:
                        updated_message = dict(message)
                        updated_message["tool_calls"] = updated_tool_calls
                        changed = True

            elif role == "tool":
                original_tool_call_id = message.get("tool_call_id")
                replacement_id = (
                    id_mapping.get(original_tool_call_id)
                    if isinstance(original_tool_call_id, str)
                    else None
                )
                if replacement_id is not None:
                    updated_message = dict(message)
                    updated_message["tool_call_id"] = replacement_id
                    changed = True

            prepared_messages.append(updated_message)

        if not changed:
            return messages, []

        remapped_ids = [
            {"original_id": original_id, "sanitized_id": sanitized_id}
            for original_id, sanitized_id in id_mapping.items()
            if original_id != sanitized_id
        ]
        return prepared_messages, remapped_ids

    def _normalize_messages_for_candidate(
        self,
        messages: list[dict[str, Any]],
        *,
        litellm_model: str | None,
        provider: str | None,
        api_base: str | None,
    ) -> list[dict[str, Any]]:
        if not self._candidate_requires_single_leading_system_message(
            litellm_model=litellm_model,
            provider=provider,
            api_base=api_base,
        ):
            return messages

        system_chunks: list[str] = []
        non_system_messages: list[dict[str, Any]] = []
        for message in messages:
            if (
                isinstance(message, dict)
                and message.get("role") == "system"
                and isinstance(message.get("content"), str)
            ):
                content = str(message.get("content") or "").strip()
                if content:
                    system_chunks.append(content)
                continue
            non_system_messages.append(message)

        if not system_chunks:
            return messages

        return [
            {"role": "system", "content": "\n\n".join(system_chunks)},
            *non_system_messages,
        ]

    def _build_tool_call_id_mapping(
        self,
        messages: list[dict[str, Any]],
    ) -> dict[str, str]:
        discovered_ids: list[str] = []
        for message in messages:
            if not isinstance(message, dict):
                continue

            role = message.get("role")
            if role == "assistant":
                tool_calls = message.get("tool_calls")
                if not isinstance(tool_calls, list):
                    continue
                for tool_call in tool_calls:
                    if not isinstance(tool_call, dict):
                        continue
                    call_id = tool_call.get("id")
                    if isinstance(call_id, str) and call_id.strip():
                        discovered_ids.append(call_id.strip())
            elif role == "tool":
                tool_call_id = message.get("tool_call_id")
                if isinstance(tool_call_id, str) and tool_call_id.strip():
                    discovered_ids.append(tool_call_id.strip())

        if not discovered_ids:
            return {}

        mapping: dict[str, str] = {}
        reserved_ids: set[str] = set()
        for call_id in discovered_ids:
            if call_id in mapping:
                continue
            mapping[call_id] = self._build_fc_compatible_tool_call_id(
                call_id,
                reserved_ids,
            )
            reserved_ids.add(mapping[call_id])
        return {
            original_id: sanitized_id
            for original_id, sanitized_id in mapping.items()
            if sanitized_id != original_id
        }

    def _candidate_requires_fc_tool_call_ids(self, litellm_model: str | None) -> bool:
        # GPT-5 family requests can be bridged onto OpenAI Responses API, which
        # rejects historical function-call IDs unless they use the `fc_` prefix.
        model_name = str(litellm_model or "").split("/")[-1].strip().lower()
        return model_name.startswith("gpt-5")

    def _transform_tools_for_candidate(
        self,
        tools: list[dict[str, Any]] | None,
        *,
        provider: str | None,
    ):
        if not tools:
            return None
        if not is_antigravity_provider(provider):
            return None
        return transform_antigravity_tools(tools)

    def _candidate_requires_single_leading_system_message(
        self,
        *,
        litellm_model: str | None,
        provider: str | None,
        api_base: str | None,
    ) -> bool:
        model_name = str(litellm_model or "").strip().lower()
        provider_name = str(provider or "").strip().lower()
        base_url = str(api_base or "").strip().lower()
        return (
            provider_name in {"genesis", "genesislocal"}
            or ":8100" in base_url
            or ":18081" in base_url
            or model_name == "openai/qwen3.5-122b"
            or model_name == "openai/qwen3.6-35b"
        )

    def _build_fc_compatible_tool_call_id(
        self,
        original_id: str,
        reserved_ids: set[str],
    ) -> str:
        normalized = original_id.strip()
        if normalized.startswith("fc_") and normalized not in reserved_ids:
            return normalized

        safe_id = re.sub(r"[^A-Za-z0-9_-]+", "_", normalized).strip("_")
        if not safe_id:
            safe_id = "tool_call"
        base_id = safe_id if safe_id.startswith("fc_") else f"fc_{safe_id}"
        candidate_id = base_id
        suffix = 1
        while candidate_id in reserved_ids:
            candidate_id = f"{base_id}_{suffix}"
            suffix += 1
        return candidate_id

    def _normalize_tool_call_item(
        self,
        item: Any,
        *,
        default_id: str,
    ) -> dict[str, Any] | None:
        function_payload = self._read_field(item, "function")
        if function_payload is None:
            return None
        name = self._read_field(function_payload, "name")
        if not isinstance(name, str) or not name:
            return None

        arguments = self._read_field(function_payload, "arguments")
        if isinstance(arguments, dict):
            arguments = self._json_dump(arguments)
        elif arguments is None:
            arguments = "{}"
        elif not isinstance(arguments, str):
            arguments = str(arguments)

        call_id = self._read_field(item, "id")
        if not isinstance(call_id, str) or not call_id:
            call_id = default_id

        call_type = self._read_field(item, "type")
        if not isinstance(call_type, str) or not call_type:
            call_type = "function"

        return {
            "id": call_id,
            "type": call_type,
            "function": {
                "name": name,
                "arguments": arguments,
            },
        }

    def _normalize_function_call(
        self,
        function_call: Any,
        *,
        default_id: str,
    ) -> dict[str, Any] | None:
        if function_call is None:
            return None
        name = self._read_field(function_call, "name")
        if not isinstance(name, str) or not name:
            return None

        arguments = self._read_field(function_call, "arguments")
        if isinstance(arguments, dict):
            arguments = self._json_dump(arguments)
        elif arguments is None:
            arguments = "{}"
        elif not isinstance(arguments, str):
            arguments = str(arguments)

        return {
            "id": default_id,
            "type": "function",
            "function": {"name": name, "arguments": arguments},
        }

    def _normalize_gemini_part_tool_call(
        self,
        part: Any,
        *,
        default_id: str,
    ) -> dict[str, Any] | None:
        if not isinstance(part, dict):
            return None

        part_type = part.get("type")
        if part_type not in {"tool_use", "function_call"}:
            return None

        name = part.get("name") or part.get("tool_name")
        if not isinstance(name, str) or not name:
            return None

        arguments = part.get("arguments")
        if arguments is None:
            arguments = part.get("input")
        if isinstance(arguments, dict):
            arguments = self._json_dump(arguments)
        elif arguments is None:
            arguments = "{}"
        elif not isinstance(arguments, str):
            arguments = str(arguments)

        call_id = part.get("id")
        if not isinstance(call_id, str) or not call_id:
            call_id = default_id

        return {
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": arguments},
        }

    def _json_dump(self, payload: dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False)

    def _parse_json_object_from_text(self, text: str) -> dict[str, Any] | None:
        parsed = self._parse_json_value_from_text(text)
        return parsed if isinstance(parsed, dict) else None

    def _parse_json_value_from_text(self, text: str) -> Any | None:
        raw = (text or "").strip()
        if not raw:
            return None

        parsed_direct = self._try_parse_json_value(raw)
        if parsed_direct is not None:
            return parsed_direct

        fenced_match = re.search(r"```(?:json)?\s*(.+?)\s*```", raw, flags=re.DOTALL)
        if fenced_match:
            parsed_fenced = self._try_parse_json_value(fenced_match.group(1).strip())
            if parsed_fenced is not None:
                return parsed_fenced

        for start_char, end_char in (("{", "}"), ("[", "]")):
            start = raw.find(start_char)
            end = raw.rfind(end_char)
            if start >= 0 and end > start:
                parsed_slice = self._try_parse_json_value(raw[start : end + 1])
                if parsed_slice is not None:
                    return parsed_slice

        return None

    def _try_parse_json_dict(self, payload: str) -> dict[str, Any] | None:
        parsed = self._try_parse_json_value(payload)
        return parsed if isinstance(parsed, dict) else None

    def _try_parse_json_value(self, payload: str) -> Any | None:
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return None

    def _extract_text_embedded_tool_calls(self, text: str) -> list[dict[str, Any]]:
        parsed = self._parse_json_value_from_text(text)
        if parsed is None:
            return []

        raw_items: list[Any]
        if isinstance(parsed, dict) and isinstance(parsed.get("tool_calls"), list):
            raw_items = list(parsed["tool_calls"])
        elif isinstance(parsed, list):
            raw_items = list(parsed)
        else:
            raw_items = [parsed]

        normalized: list[dict[str, Any]] = []
        for idx, item in enumerate(raw_items):
            normalized_item = self._normalize_text_tool_call_candidate(
                item,
                default_id=f"call_{idx + 1}",
            )
            if normalized_item is not None:
                normalized.append(normalized_item)
        return normalized

    def _normalize_text_tool_call_candidate(
        self,
        item: Any,
        *,
        default_id: str,
    ) -> dict[str, Any] | None:
        if not isinstance(item, dict):
            return None

        normalized_item = self._normalize_tool_call_item(item, default_id=default_id)
        if normalized_item is not None:
            return normalized_item

        function_call = self._normalize_function_call(item, default_id=default_id)
        if function_call is not None:
            return function_call

        name = item.get("tool_name") or item.get("name")
        if not isinstance(name, str) or not name:
            return None

        arguments = item.get("arguments", item.get("input", item.get("params")))
        if isinstance(arguments, dict):
            arguments = self._json_dump(arguments)
        elif arguments is None:
            arguments = "{}"
        elif not isinstance(arguments, str):
            arguments = str(arguments)

        call_id = item.get("id")
        if not isinstance(call_id, str) or not call_id:
            call_id = default_id

        return {
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": arguments},
        }

    def _extract_delta_text(self, chunk: Any) -> str:
        choices = self._read_field(chunk, "choices") or []
        if not choices:
            return ""
        first_choice = choices[0]
        delta = self._read_field(first_choice, "delta")
        if delta is None:
            return ""
        return self._normalize_content(self._read_field(delta, "content"))

    def _normalize_content(self, content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text_parts: list[str] = []
            for item in content:
                text = self._read_field(item, "text")
                if isinstance(text, str):
                    text_parts.append(text)
            return "".join(text_parts)
        return str(content)

    def _extract_usage(
        self,
        payload: Any,
        default: dict[str, int | None] | None = None,
    ) -> dict[str, int | None]:
        usage = self._read_field(payload, "usage")
        if usage is None:
            return default or {
                "input_tokens": None,
                "output_tokens": None,
                "total_tokens": None,
            }

        prompt = self._read_field(usage, "prompt_tokens")
        completion = self._read_field(usage, "completion_tokens")
        total = self._read_field(usage, "total_tokens")
        return {
            "input_tokens": prompt,
            "output_tokens": completion,
            "total_tokens": total,
        }

    def _read_field(self, payload: Any, key: str) -> Any:
        if isinstance(payload, dict):
            return payload.get(key)
        return getattr(payload, key, None)
