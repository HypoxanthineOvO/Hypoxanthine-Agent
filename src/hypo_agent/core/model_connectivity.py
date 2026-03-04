from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from hypo_agent.core.config_loader import ResolvedModelConfig

DEFAULT_PROBE_PROMPT = "Please call the echo tool with text 'hello'"
DEFAULT_TOOL_CHOICE = "auto"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_PROBE_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "echo",
            "description": "Echo back the input text",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    }
]


@dataclass(slots=True)
class ModelProbeResult:
    model_name: str
    provider: str | None
    litellm_model: str | None
    connectivity_ok: bool
    tool_calling_ok: bool | None
    tool_calls_count: int
    text: str
    error: str | None
    latency_ms: float
    tool_calls: list[dict[str, Any]]


async def probe_model(
    model_name: str,
    config: ResolvedModelConfig,
    *,
    acompletion_fn,
    prompt: str = DEFAULT_PROBE_PROMPT,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str = DEFAULT_TOOL_CHOICE,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> ModelProbeResult:
    if config.litellm_model is None:
        return ModelProbeResult(
            model_name=model_name,
            provider=config.provider,
            litellm_model=None,
            connectivity_ok=False,
            tool_calling_ok=None,
            tool_calls_count=0,
            text="",
            error="litellm_model is not configured",
            latency_ms=0.0,
            tool_calls=[],
        )

    kwargs: dict[str, Any] = {
        "model": config.litellm_model,
        "messages": [{"role": "user", "content": prompt}],
        "tools": tools or DEFAULT_PROBE_TOOLS,
        "tool_choice": tool_choice,
    }
    if isinstance(config.api_base, str) and config.api_base.strip():
        kwargs["api_base"] = config.api_base
    if isinstance(config.api_key, str) and config.api_key.strip():
        kwargs["api_key"] = config.api_key

    start = perf_counter()
    try:
        response = await asyncio.wait_for(acompletion_fn(**kwargs), timeout=timeout_seconds)
    except Exception as exc:  # pragma: no cover - covered by unit tests
        elapsed_ms = (perf_counter() - start) * 1000
        error_text = str(exc).strip() or exc.__class__.__name__
        return ModelProbeResult(
            model_name=model_name,
            provider=config.provider,
            litellm_model=config.litellm_model,
            connectivity_ok=False,
            tool_calling_ok=None,
            tool_calls_count=0,
            text="",
            error=error_text,
            latency_ms=elapsed_ms,
            tool_calls=[],
        )

    elapsed_ms = (perf_counter() - start) * 1000
    text = _extract_message_text(response)
    tool_calls = _extract_tool_calls(response)
    return ModelProbeResult(
        model_name=model_name,
        provider=config.provider,
        litellm_model=config.litellm_model,
        connectivity_ok=True,
        tool_calling_ok=bool(tool_calls),
        tool_calls_count=len(tool_calls),
        text=text,
        error=None,
        latency_ms=elapsed_ms,
        tool_calls=tool_calls,
    )


def _extract_message_text(payload: Any) -> str:
    choices = _read_field(payload, "choices") or []
    if not choices:
        return ""

    message = _read_field(choices[0], "message")
    if message is None:
        return ""

    content = _read_field(message, "content")
    return _normalize_content(content)


def _extract_tool_calls(payload: Any) -> list[dict[str, Any]]:
    choices = _read_field(payload, "choices") or []
    if not choices:
        return []

    message = _read_field(choices[0], "message")
    if message is None:
        return []

    normalized: list[dict[str, Any]] = []

    raw_calls = _read_field(message, "tool_calls")
    if raw_calls is None:
        raw_calls = []
    if isinstance(raw_calls, dict):
        raw_calls = [raw_calls]
    if not isinstance(raw_calls, list):
        raw_calls = []

    for idx, item in enumerate(raw_calls):
        normalized_item = _normalize_tool_call_item(item, default_id=f"call_{idx + 1}")
        if normalized_item is not None:
            normalized.append(normalized_item)
    if normalized:
        return normalized

    legacy_call = _normalize_function_call(
        _read_field(message, "function_call"),
        default_id="call_1",
    )
    if legacy_call is not None:
        return [legacy_call]

    content = _read_field(message, "content")
    if isinstance(content, list):
        for idx, part in enumerate(content):
            normalized_item = _normalize_content_part_tool_call(
                part,
                default_id=f"call_{idx + 1}",
            )
            if normalized_item is not None:
                normalized.append(normalized_item)
    return normalized


def _normalize_tool_call_item(item: Any, *, default_id: str) -> dict[str, Any] | None:
    function_payload = _read_field(item, "function")
    if function_payload is None:
        return None
    name = _read_field(function_payload, "name")
    if not isinstance(name, str) or not name:
        return None

    arguments = _read_field(function_payload, "arguments")
    arguments_str = _normalize_arguments(arguments)
    call_id = _read_field(item, "id")
    if not isinstance(call_id, str) or not call_id:
        call_id = default_id

    call_type = _read_field(item, "type")
    if not isinstance(call_type, str) or not call_type:
        call_type = "function"

    return {
        "id": call_id,
        "type": call_type,
        "function": {"name": name, "arguments": arguments_str},
    }


def _normalize_function_call(
    function_call: Any,
    *,
    default_id: str,
) -> dict[str, Any] | None:
    if function_call is None:
        return None

    name = _read_field(function_call, "name")
    if not isinstance(name, str) or not name:
        return None

    arguments = _normalize_arguments(_read_field(function_call, "arguments"))
    return {
        "id": default_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def _normalize_content_part_tool_call(
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

    arguments = part.get("arguments", part.get("input"))
    arguments_str = _normalize_arguments(arguments)
    call_id = part.get("id")
    if not isinstance(call_id, str) or not call_id:
        call_id = default_id

    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments_str},
    }


def _normalize_arguments(arguments: Any) -> str:
    if isinstance(arguments, dict):
        return json.dumps(arguments, ensure_ascii=False)
    if arguments is None:
        return "{}"
    if isinstance(arguments, str):
        return arguments
    return str(arguments)


def _normalize_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts: list[str] = []
        for item in content:
            text = _read_field(item, "text")
            if isinstance(text, str):
                text_parts.append(text)
        return "".join(text_parts)
    return str(content)


def _read_field(payload: Any, key: str) -> Any:
    if isinstance(payload, dict):
        return payload.get(key)
    return getattr(payload, key, None)
