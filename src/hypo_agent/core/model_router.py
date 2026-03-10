from __future__ import annotations

import json
import inspect
from collections.abc import AsyncIterator
from collections.abc import Awaitable, Callable
import re
from time import perf_counter
from typing import Any

import structlog
try:
    from litellm import acompletion as litellm_acompletion
except ImportError:  # pragma: no cover - depends on runtime environment
    litellm_acompletion = None

from hypo_agent.core.config_loader import RuntimeModelConfig


class ModelRouter:
    def __init__(
        self,
        config: RuntimeModelConfig,
        acompletion_fn=None,
        on_stream_success: Callable[[dict[str, Any]], Awaitable[None] | None]
        | None = None,
    ) -> None:
        self.config = config
        self._acompletion = acompletion_fn or litellm_acompletion
        if self._acompletion is None:
            raise RuntimeError(
                "litellm is not installed and no acompletion_fn was provided"
            )
        self._on_stream_success = on_stream_success
        self.logger = structlog.get_logger("hypo_agent.model_router")

    async def call(
        self,
        model_name: str,
        messages: list[dict[str, Any]],
        *,
        session_id: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> str:
        payload = await self.call_with_tools(
            model_name,
            messages,
            tools=tools,
            session_id=session_id,
        )
        return payload["text"]

    async def call_with_tools(
        self,
        model_name: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        attempted: list[str] = []
        last_error: Exception | None = None
        started_at = perf_counter()

        for candidate in self._candidate_chain(model_name):
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

            try:
                kwargs: dict[str, Any] = {
                    "model": cfg.litellm_model,
                    "messages": messages,
                }
                if isinstance(cfg.api_base, str) and cfg.api_base.strip():
                    kwargs["api_base"] = cfg.api_base
                if isinstance(cfg.api_key, str) and cfg.api_key.strip():
                    kwargs["api_key"] = cfg.api_key
                if tools is not None:
                    kwargs["tools"] = tools
                self.logger.debug(
                    "call_with_tools.request",
                    model=cfg.litellm_model,
                    tools_count=len(tools or []),
                    messages_count=len(messages),
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
                return {"text": text, "tool_calls": tool_calls}
            except Exception as exc:  # pragma: no cover - exercised in tests
                attempted.append(candidate)
                last_error = exc
                self.logger.warning(
                    "model_call_failed",
                    requested_model=model_name,
                    resolved_model=candidate,
                    error=str(exc),
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
    ) -> AsyncIterator[str]:
        attempted: list[str] = []
        last_error: Exception | None = None
        started_at = perf_counter()

        for candidate in self._candidate_chain(model_name):
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

            started = False
            usage = {"input_tokens": None, "output_tokens": None, "total_tokens": None}

            try:
                kwargs: dict[str, Any] = {
                    "model": cfg.litellm_model,
                    "messages": messages,
                    "stream": True,
                }
                if isinstance(cfg.api_base, str) and cfg.api_base.strip():
                    kwargs["api_base"] = cfg.api_base
                if isinstance(cfg.api_key, str) and cfg.api_key.strip():
                    kwargs["api_key"] = cfg.api_key
                if tools is not None:
                    kwargs["tools"] = tools

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
            except Exception as exc:  # pragma: no cover - exercised in tests
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

        raise RuntimeError(
            f"All stream models failed for '{model_name}'. Attempted chain: {attempted}"
        ) from last_error

    def get_model_for_task(self, task_type: str) -> str:
        return self.config.task_routing.get(task_type, self.config.default_model)

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
        return normalized

    def _has_tool_call_payload(self, payload: Any) -> bool:
        return bool(self._extract_tool_calls(payload))

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
        raw = (text or "").strip()
        if not raw:
            return None

        parsed_direct = self._try_parse_json_dict(raw)
        if parsed_direct is not None:
            return parsed_direct

        fenced_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.DOTALL)
        if fenced_match:
            parsed_fenced = self._try_parse_json_dict(fenced_match.group(1).strip())
            if parsed_fenced is not None:
                return parsed_fenced

        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            parsed_slice = self._try_parse_json_dict(raw[start : end + 1])
            if parsed_slice is not None:
                return parsed_slice

        return None

    def _try_parse_json_dict(self, payload: str) -> dict[str, Any] | None:
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict):
            return parsed
        return None

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
