from __future__ import annotations

from hypo_agent.core.config_loader import ResolvedModelConfig
from hypo_agent.core.model_request_options import build_model_request_kwargs


def _resolved_model(**overrides) -> ResolvedModelConfig:
    payload = {
        "provider": "OpenAI",
        "litellm_model": "openai/gpt-5.4",
        "api_base": "https://api.openai.com/v1",
        "api_key": "sk-test",
        "reasoning_config": {},
    }
    payload.update(overrides)
    return ResolvedModelConfig.model_validate(payload)


def test_openai_chat_gets_low_reasoning() -> None:
    config = _resolved_model()

    kwargs = build_model_request_kwargs(
        model_config=config,
        litellm_model=config.litellm_model,
        task_type="chat",
    )

    assert kwargs == {"reasoning_effort": "low"}


def test_openai_reasoning_gets_high() -> None:
    config = _resolved_model()

    kwargs = build_model_request_kwargs(
        model_config=config,
        litellm_model=config.litellm_model,
        task_type="reasoning",
    )

    assert kwargs == {"reasoning_effort": "high"}


def test_anthropic_reasoning_gets_thinking_enabled() -> None:
    config = _resolved_model(
        provider="Anthropic",
        litellm_model="anthropic/claude-3-7-sonnet-latest",
        api_base="https://api.anthropic.com",
    )

    kwargs = build_model_request_kwargs(
        model_config=config,
        litellm_model=config.litellm_model,
        task_type="reasoning",
    )

    assert kwargs == {"thinking": {"type": "enabled", "budget_tokens": 4096}}


def test_anthropic_chat_no_thinking() -> None:
    config = _resolved_model(
        provider="Anthropic",
        litellm_model="anthropic/claude-3-7-sonnet-latest",
        api_base="https://api.anthropic.com",
    )

    kwargs = build_model_request_kwargs(
        model_config=config,
        litellm_model=config.litellm_model,
        task_type="chat",
    )

    assert kwargs == {}


def test_ollama_chat_think_false() -> None:
    config = _resolved_model(
        provider="Eden",
        litellm_model="ollama_chat/qwen3.5:27b",
        api_base="http://10.19.138.13:11434",
    )

    kwargs = build_model_request_kwargs(
        model_config=config,
        litellm_model=config.litellm_model,
        task_type="chat",
    )

    assert kwargs == {"think": False}


def test_ollama_reasoning_think_true() -> None:
    config = _resolved_model(
        provider="Eden",
        litellm_model="ollama_chat/gemma4:31b",
        api_base="http://10.19.138.13:11434",
    )

    kwargs = build_model_request_kwargs(
        model_config=config,
        litellm_model=config.litellm_model,
        task_type="reasoning",
    )

    assert kwargs == {"think": True}


def test_unknown_provider_no_injection() -> None:
    config = _resolved_model(
        provider="AcmeGateway",
        litellm_model="openai/custom-model",
        api_base="https://llm.acme.internal/v1",
    )

    kwargs = build_model_request_kwargs(
        model_config=config,
        litellm_model=config.litellm_model,
        task_type="chat",
    )

    assert kwargs == {}


def test_user_override_via_reasoning_config() -> None:
    config = _resolved_model(
        provider="OpenAI",
        litellm_model="openai/gpt-5.4",
        reasoning_config={"chat": "high"},
    )

    kwargs = build_model_request_kwargs(
        model_config=config,
        litellm_model=config.litellm_model,
        task_type="chat",
    )

    assert kwargs == {"reasoning_effort": "high"}


def test_genesis_chat_disables_thinking() -> None:
    config = _resolved_model(
        provider="Genesis",
        litellm_model="openai/qwen3.5-122b",
        api_base="http://10.15.88.94:8100/v1",
    )

    kwargs = build_model_request_kwargs(
        model_config=config,
        litellm_model=config.litellm_model,
        task_type="chat",
    )

    assert kwargs == {"chat_template_kwargs": {"enable_thinking": False}}


def test_genesis_reasoning_enables_thinking() -> None:
    config = _resolved_model(
        provider="Genesis",
        litellm_model="openai/qwen3.5-122b",
        api_base="http://10.15.88.94:8100/v1",
    )

    kwargs = build_model_request_kwargs(
        model_config=config,
        litellm_model=config.litellm_model,
        task_type="reasoning",
    )

    assert kwargs == {"chat_template_kwargs": {"enable_thinking": True}}


def test_volcengine_coding_route_does_not_get_reasoning_injection() -> None:
    config = _resolved_model(
        provider="volcengine_coding",
        litellm_model="openai/ark-code-latest",
        api_base="https://ark.cn-beijing.volces.com/api/coding/v3",
    )

    kwargs = build_model_request_kwargs(
        model_config=config,
        litellm_model=config.litellm_model,
        task_type="chat",
    )

    assert kwargs == {}
