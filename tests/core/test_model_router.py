from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from hypo_agent.core.config_loader import RuntimeModelConfig
from hypo_agent.core.model_router import ModelRouter


@pytest.fixture
def runtime_config() -> RuntimeModelConfig:
    return RuntimeModelConfig.model_validate(
        {
            "default_model": "Gemini3Pro",
            "task_routing": {"chat": "Gemini3Pro", "lightweight": "DeepseekV3_2"},
            "models": {
                "Gemini3Pro": {
                    "provider": "Hiapi",
                    "litellm_model": "openai/gemini-2.5-pro",
                    "fallback": "DeepseekV3_2",
                    "api_base": "https://hiapi.online/v1",
                    "api_key": "sk-hiapi",
                },
                "DeepseekV3_2": {
                    "provider": "Volcengine",
                    "litellm_model": "openai/ep-20251215171209-4z5qk",
                    "fallback": "QwenPlus",
                    "api_base": "https://ark.cn-beijing.volces.com/api/v3",
                    "api_key": "volc-key",
                },
                "QwenPlus": {
                    "provider": "Dashscope",
                    "litellm_model": "openai/qwen-plus",
                    "fallback": None,
                    "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                    "api_key": "sk-dashscope",
                },
            },
        }
    )


@pytest.fixture
def runtime_config_with_null_head() -> RuntimeModelConfig:
    return RuntimeModelConfig.model_validate(
        {
            "default_model": "ClaudeSonnet",
            "task_routing": {"chat": "ClaudeSonnet"},
            "models": {
                "ClaudeSonnet": {
                    "provider": None,
                    "litellm_model": None,
                    "fallback": "Gemini3Pro",
                    "api_base": None,
                    "api_key": None,
                },
                "Gemini3Pro": {
                    "provider": "Hiapi",
                    "litellm_model": "openai/gemini-2.5-pro",
                    "fallback": None,
                    "api_base": "https://hiapi.online/v1",
                    "api_key": "sk-hiapi",
                },
            },
        }
    )


def test_model_router_call_uses_primary_model_first(
    runtime_config: RuntimeModelConfig,
) -> None:
    calls: list[dict] = []

    async def fake_acompletion(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="hello"))],
            usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7, total_tokens=18),
        )

    router = ModelRouter(runtime_config, acompletion_fn=fake_acompletion)
    text = asyncio.run(router.call("Gemini3Pro", [{"role": "user", "content": "hi"}]))

    assert text == "hello"
    assert calls[0]["model"] == "openai/gemini-2.5-pro"
    assert calls[0]["api_base"] == "https://hiapi.online/v1"
    assert calls[0]["api_key"] == "sk-hiapi"


def test_model_router_fallback_on_failure(runtime_config: RuntimeModelConfig) -> None:
    called_models: list[str] = []

    async def fake_acompletion(**kwargs):
        called_models.append(kwargs["model"])
        if kwargs["model"] == "openai/gemini-2.5-pro":
            raise RuntimeError("boom")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="fallback ok"))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )

    router = ModelRouter(runtime_config, acompletion_fn=fake_acompletion)
    text = asyncio.run(router.call("Gemini3Pro", [{"role": "user", "content": "hi"}]))

    assert text == "fallback ok"
    assert called_models == [
        "openai/gemini-2.5-pro",
        "openai/ep-20251215171209-4z5qk",
    ]


def test_model_router_skips_null_provider_model(
    runtime_config_with_null_head: RuntimeModelConfig,
) -> None:
    called_models: list[str] = []

    async def fake_acompletion(**kwargs):
        called_models.append(kwargs["model"])
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            usage=SimpleNamespace(prompt_tokens=2, completion_tokens=3, total_tokens=5),
        )

    router = ModelRouter(runtime_config_with_null_head, acompletion_fn=fake_acompletion)
    text = asyncio.run(router.call("ClaudeSonnet", [{"role": "user", "content": "hi"}]))

    assert text == "ok"
    assert called_models == ["openai/gemini-2.5-pro"]


def test_model_router_stream_yields_chunks(runtime_config: RuntimeModelConfig) -> None:
    async def fake_acompletion(**kwargs):
        assert kwargs["stream"] is True

        async def _gen():
            yield {"choices": [{"delta": {"content": "He"}}]}
            yield {"choices": [{"delta": {"content": "llo"}}]}

        return _gen()

    router = ModelRouter(runtime_config, acompletion_fn=fake_acompletion)

    async def _collect() -> list[str]:
        chunks: list[str] = []
        async for chunk in router.stream(
            "Gemini3Pro",
            [{"role": "user", "content": "hi"}],
        ):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(_collect())
    assert chunks == ["He", "llo"]


def test_model_router_stream_fallback_before_first_chunk(
    runtime_config: RuntimeModelConfig,
) -> None:
    called_models: list[str] = []

    async def fake_acompletion(**kwargs):
        called_models.append(kwargs["model"])
        if kwargs["model"] == "openai/gemini-2.5-pro":
            raise RuntimeError("stream boom")

        async def _gen():
            yield {"choices": [{"delta": {"content": "fa"}}]}
            yield {"choices": [{"delta": {"content": "llback"}}]}

        return _gen()

    router = ModelRouter(runtime_config, acompletion_fn=fake_acompletion)

    async def _collect() -> list[str]:
        chunks: list[str] = []
        async for chunk in router.stream(
            "Gemini3Pro",
            [{"role": "user", "content": "hi"}],
        ):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(_collect())
    assert chunks == ["fa", "llback"]
    assert called_models == [
        "openai/gemini-2.5-pro",
        "openai/ep-20251215171209-4z5qk",
    ]


def test_model_router_emits_stream_success_event_with_usage(
    runtime_config: RuntimeModelConfig,
) -> None:
    emitted: list[dict] = []

    async def on_stream_success(event: dict) -> None:
        emitted.append(event)

    async def fake_acompletion(**kwargs):
        async def _gen():
            yield {"choices": [{"delta": {"content": "ok"}}]}
            yield {
                "choices": [{"delta": {"content": "!"}}],
                "usage": {
                    "prompt_tokens": 3,
                    "completion_tokens": 2,
                    "total_tokens": 5,
                },
            }

        return _gen()

    router = ModelRouter(
        runtime_config,
        acompletion_fn=fake_acompletion,
        on_stream_success=on_stream_success,
    )

    async def _collect() -> list[str]:
        chunks: list[str] = []
        async for chunk in router.stream(
            "Gemini3Pro",
            [{"role": "user", "content": "hi"}],
            session_id="s1",
        ):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(_collect())
    assert chunks == ["ok", "!"]
    assert emitted == [
        {
            "event": "model_stream_success",
            "session_id": "s1",
            "requested_model": "Gemini3Pro",
            "resolved_model": "Gemini3Pro",
            "input_tokens": 3,
            "output_tokens": 2,
            "total_tokens": 5,
        }
    ]


def test_model_router_rejects_fallback_cycle() -> None:
    config = RuntimeModelConfig.model_validate(
        {
            "default_model": "A",
            "task_routing": {"chat": "A"},
            "models": {
                "A": {
                    "provider": None,
                    "litellm_model": None,
                    "fallback": "B",
                    "api_base": None,
                    "api_key": None,
                },
                "B": {
                    "provider": None,
                    "litellm_model": None,
                    "fallback": "A",
                    "api_base": None,
                    "api_key": None,
                },
            },
        }
    )

    async def fake_acompletion(**kwargs):
        raise AssertionError("Should not call completion for cycle configs")

    router = ModelRouter(config, acompletion_fn=fake_acompletion)
    with pytest.raises(ValueError, match="Fallback cycle"):
        asyncio.run(router.call("A", [{"role": "user", "content": "hi"}]))
