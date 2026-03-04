from __future__ import annotations

import asyncio
import json
from typing import Any

from litellm import acompletion

from hypo_agent.core.config_loader import load_runtime_model_config


def _to_serializable(payload: Any) -> Any:
    if payload is None:
        return None
    if isinstance(payload, (str, int, float, bool)):
        return payload
    if isinstance(payload, list):
        return [_to_serializable(item) for item in payload]
    if isinstance(payload, dict):
        return {str(k): _to_serializable(v) for k, v in payload.items()}
    model_dump = getattr(payload, "model_dump", None)
    if callable(model_dump):
        return _to_serializable(model_dump())
    dict_fn = getattr(payload, "dict", None)
    if callable(dict_fn):
        return _to_serializable(dict_fn())
    return str(payload)


def _read_field(payload: Any, key: str) -> Any:
    if isinstance(payload, dict):
        return payload.get(key)
    return getattr(payload, key, None)


def _extract_message(response: Any) -> Any:
    choices = _read_field(response, "choices") or []
    if not choices:
        return None
    return _read_field(choices[0], "message")


async def main() -> int:
    runtime = load_runtime_model_config("config/models.yaml", "config/secrets.yaml")
    model_name = runtime.task_routing.get("chat", runtime.default_model)
    model_cfg = runtime.models[model_name]
    if model_cfg.litellm_model is None:
        print(f"ERROR: model '{model_name}' has no litellm_model configured.")
        return 1

    tools = [
        {
            "type": "function",
            "function": {
                "name": "run_command",
                "description": "Execute a shell command in terminal.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                    },
                    "required": ["command"],
                },
            },
        }
    ]
    messages = [{"role": "user", "content": "请执行命令 echo hello"}]

    print("=== Tool Calling Diagnosis ===")
    print(f"requested_model_name: {model_name}")
    print(f"litellm_model: {model_cfg.litellm_model}")
    print(f"api_base: {model_cfg.api_base}")
    print(f"tools_count: {len(tools)}")
    print(f"messages_count: {len(messages)}")
    print()

    response = await acompletion(
        model=model_cfg.litellm_model,
        messages=messages,
        tools=tools,
        api_base=model_cfg.api_base,
        api_key=model_cfg.api_key,
    )

    message = _extract_message(response)
    content = _read_field(message, "content") if message is not None else None
    tool_calls = _read_field(message, "tool_calls") if message is not None else None

    print("message.content:")
    print(content)
    print()
    print("message.tool_calls:")
    print(tool_calls)
    print()
    print("raw_response:")
    print(json.dumps(_to_serializable(response), ensure_ascii=False, indent=2))

    if not tool_calls:
        print()
        print(
            "WARNING: tool_calls is empty. The model/provider may not support "
            "tool calling or LiteLLM may not pass tools correctly for this endpoint."
        )
    else:
        print()
        print("OK: model returned tool_calls.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
