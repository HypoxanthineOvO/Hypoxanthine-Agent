from __future__ import annotations

from collections.abc import Mapping
from typing import Any

DEFAULT_REASONING_BY_TASK: dict[str, str] = {
    "chat": "low",
    "lightweight": "low",
    "compression": "low",
    "reasoning": "high",
    "vision": "medium",
}

ANTHROPIC_THINKING_BUDGETS: dict[str, int] = {
    "low": 1024,
    "medium": 2048,
    "high": 4096,
}

DASHSCOPE_THINKING_BUDGETS: dict[str, int] = {
    "low": 1024,
    "medium": 2048,
    "high": 4096,
}


def normalize_task_type(task_type: str | None) -> str:
    return str(task_type or "").strip().lower()


def _normalize_reasoning_level(value: Any) -> str | None:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"low", "medium", "high"} else None


def _read_model_field(model_config: Any, field_name: str) -> Any:
    if model_config is None:
        return None
    if isinstance(model_config, Mapping):
        return model_config.get(field_name)
    return getattr(model_config, field_name, None)


def _resolve_model_context(
    *,
    model_config: Any = None,
    litellm_model: str | None = None,
    provider: str | None = None,
    api_base: str | None = None,
    reasoning_config: Mapping[str, Any] | None = None,
) -> tuple[str | None, str | None, str | None, Mapping[str, Any]]:
    resolved_litellm_model = litellm_model
    if resolved_litellm_model is None:
        resolved_litellm_model = _read_model_field(model_config, "litellm_model")

    resolved_provider = provider
    if resolved_provider is None:
        resolved_provider = _read_model_field(model_config, "provider")

    resolved_api_base = api_base
    if resolved_api_base is None:
        resolved_api_base = _read_model_field(model_config, "api_base")

    resolved_reasoning_config = reasoning_config
    if resolved_reasoning_config is None:
        resolved_reasoning_config = _read_model_field(model_config, "reasoning_config")
    if not isinstance(resolved_reasoning_config, Mapping):
        resolved_reasoning_config = {}

    return (
        resolved_litellm_model,
        resolved_provider,
        resolved_api_base,
        resolved_reasoning_config,
    )


def _detect_provider(
    *,
    litellm_model: str | None,
    provider: str | None,
    api_base: str | None,
) -> str | None:
    model_name = str(litellm_model or "").strip().lower()
    provider_name = str(provider or "").strip().lower()
    base_url = str(api_base or "").strip().lower()
    prefix, _, remainder = model_name.partition("/")
    slug = remainder or model_name

    if prefix == "ollama_chat" or "ollama" in provider_name or ":11434" in base_url:
        return "ollama"
    if provider_name == "genesis" or ":8100" in base_url:
        return "genesis_qwen"
    if provider_name == "volcengine_coding":
        return None
    if (
        prefix == "anthropic"
        or "anthropic" in provider_name
        or "anthropic.com" in base_url
        or slug.startswith("claude")
        or "claude" in slug
    ):
        return "anthropic"
    if (
        prefix in {"gemini", "vertex_ai", "vertex_ai_beta", "palm"}
        or "gemini" in provider_name
        or "google" in provider_name
        or "vertex" in provider_name
        or "gemini" in slug
    ):
        return "gemini"
    if (
        "dashscope" in provider_name
        or "tongyi" in provider_name
        or "aliyun" in provider_name
        or "dashscope.aliyuncs.com" in base_url
    ):
        return "dashscope"
    if (
        prefix == "volcengine"
        or "volcengine" in provider_name
        or "volcano" in provider_name
        or "ark" in provider_name
        or "volces.com" in base_url
    ):
        return "volcengine"
    if (
        prefix == "deepseek"
        or "deepseek" in provider_name
        or "api.deepseek.com" in base_url
        or slug.startswith("deepseek")
    ):
        return "deepseek"
    if (
        prefix == "moonshot"
        or "moonshot" in provider_name
        or "kimi" in provider_name
        or "moonshot.ai" in base_url
        or slug.startswith("kimi")
        or "moonshot" in slug
    ):
        return "moonshot"
    if (
        prefix == "openai"
        or "openai" in provider_name
        or "ttapi" in provider_name
        or "api.openai.com" in base_url
        or slug.startswith(("gpt-", "o1", "o3", "o4", "chatgpt"))
    ):
        return "openai"
    return None


def _resolve_reasoning_level(
    *,
    reasoning_config: Mapping[str, Any],
    task_type: str,
) -> tuple[str | None, bool]:
    override = _normalize_reasoning_level(reasoning_config.get(task_type))
    if override is not None:
        return override, True
    return _normalize_reasoning_level(DEFAULT_REASONING_BY_TASK.get(task_type)), False


def _supports_openai_reasoning_model(model_name: str) -> bool:
    _, _, remainder = model_name.partition("/")
    slug = (remainder or model_name).lower()
    return slug.startswith(("gpt-5", "o1", "o3", "o4"))


def _supports_dashscope_reasoning(model_name: str) -> bool:
    _, _, remainder = model_name.partition("/")
    slug = (remainder or model_name).lower()
    return any(token in slug for token in ("qwen", "qwq", "qvq", "deepseek", "kimi"))


def _anthropic_overrides(level: str, *, allow_low: bool) -> dict[str, Any]:
    if level == "low" and not allow_low:
        return {}
    budget = ANTHROPIC_THINKING_BUDGETS.get(level)
    if budget is None:
        return {}
    return {"thinking": {"type": "enabled", "budget_tokens": budget}}


def _dashscope_overrides(model_name: str, level: str) -> dict[str, Any]:
    if not _supports_dashscope_reasoning(model_name):
        return {}
    if level == "low":
        return {"extra_body": {"enable_thinking": False}}
    budget = DASHSCOPE_THINKING_BUDGETS.get(level)
    if budget is None:
        return {}
    return {"extra_body": {"enable_thinking": True, "thinking_budget": budget}}


def _genesis_qwen_overrides(task_type: str) -> dict[str, Any]:
    return {
        "chat_template_kwargs": {
            "enable_thinking": task_type == "reasoning",
        }
    }


def _deepseek_overrides(level: str) -> dict[str, Any]:
    # DeepSeek's OpenAI-compatible API expects `thinking` inside `extra_body`.
    # In practice, our low/medium/high abstraction maps cleanly to:
    # - low: disable thinking
    # - medium/high: enable thinking and request DeepSeek's supported `high` effort
    if level == "low":
        return {"extra_body": {"thinking": {"type": "disabled"}}}
    return {
        "reasoning_effort": "high",
        "extra_body": {"thinking": {"type": "enabled"}},
    }


def get_request_options(
    *,
    model_config: Any = None,
    litellm_model: str | None = None,
    provider: str | None = None,
    api_base: str | None = None,
    reasoning_config: Mapping[str, Any] | None = None,
    task_type: str | None,
) -> dict[str, Any]:
    normalized_task_type = normalize_task_type(task_type)
    (
        resolved_litellm_model,
        resolved_provider,
        resolved_api_base,
        resolved_reasoning_config,
    ) = _resolve_model_context(
        model_config=model_config,
        litellm_model=litellm_model,
        provider=provider,
        api_base=api_base,
        reasoning_config=reasoning_config,
    )
    model_name = str(resolved_litellm_model or "").strip().lower()
    overrides: dict[str, Any] = {}

    # Thinking-capable Ollama chat models often emit empty `content` plus a
    # separate `reasoning` field by default. Keep chat-like routes responsive,
    # and only enable thinking on the explicit reasoning route.
    if model_name.startswith("ollama_chat/"):
        overrides["think"] = normalized_task_type == "reasoning"
        return overrides

    if not normalized_task_type:
        return overrides

    reasoning_level, from_override = _resolve_reasoning_level(
        reasoning_config=resolved_reasoning_config,
        task_type=normalized_task_type,
    )
    if reasoning_level is None:
        return overrides

    provider_type = _detect_provider(
        litellm_model=resolved_litellm_model,
        provider=resolved_provider,
        api_base=resolved_api_base,
    )
    if provider_type == "openai":
        if _supports_openai_reasoning_model(model_name):
            overrides["reasoning_effort"] = reasoning_level
        return overrides
    if provider_type == "anthropic":
        return _anthropic_overrides(reasoning_level, allow_low=from_override)
    if provider_type == "genesis_qwen":
        return _genesis_qwen_overrides(normalized_task_type)
    if provider_type == "gemini":
        overrides["reasoning_effort"] = reasoning_level
        return overrides
    if provider_type == "deepseek":
        return _deepseek_overrides(reasoning_level)
    if provider_type == "moonshot":
        return overrides
    if provider_type == "volcengine":
        volcengine_thinking_type = {
            "low": "disabled",
            "medium": "auto",
            "high": "enabled",
        }.get(reasoning_level)
        if volcengine_thinking_type is not None:
            overrides["thinking"] = {"type": volcengine_thinking_type}
        return overrides
    if provider_type == "dashscope":
        return _dashscope_overrides(model_name, reasoning_level)
    return overrides


def build_model_request_kwargs(
    *,
    model_config: Any = None,
    litellm_model: str | None = None,
    provider: str | None = None,
    api_base: str | None = None,
    reasoning_config: Mapping[str, Any] | None = None,
    task_type: str | None,
) -> dict[str, Any]:
    return get_request_options(
        model_config=model_config,
        litellm_model=litellm_model,
        provider=provider,
        api_base=api_base,
        reasoning_config=reasoning_config,
        task_type=task_type,
    )
