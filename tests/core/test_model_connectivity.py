from __future__ import annotations

import asyncio

from hypo_agent.core.config_loader import ResolvedModelConfig
from hypo_agent.core.model_connectivity import probe_model


def test_probe_model_reports_tool_calling_success() -> None:
    captured: list[dict] = []

    async def fake_acompletion(**kwargs):
        captured.append(kwargs)
        return {
            "choices": [
                {
                    "message": {
                        "content": "calling tool",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "echo",
                                    "arguments": "{\"text\": \"hello\"}",
                                },
                            }
                        ],
                    }
                }
            ]
        }

    config = ResolvedModelConfig(
        provider="volcengine_coding",
        litellm_model="openai/kimi-k2.5",
        api_base="https://ark.cn-beijing.volces.com/api/coding/v3",
        api_key="test-key",
    )
    result = asyncio.run(
        probe_model(
            "KimiK25",
            config,
            acompletion_fn=fake_acompletion,
            timeout_seconds=5.0,
        )
    )

    assert result.connectivity_ok is True
    assert result.tool_calling_ok is True
    assert result.tool_calls_count == 1
    assert captured[0]["api_base"] == "https://ark.cn-beijing.volces.com/api/coding/v3"
    assert captured[0]["api_key"] == "test-key"
    assert captured[0]["tool_choice"] == "auto"
    assert len(captured[0]["tools"]) == 1


def test_probe_model_reports_no_tool_calls() -> None:
    async def fake_acompletion(**kwargs):
        del kwargs
        return {"choices": [{"message": {"content": "plain text", "tool_calls": None}}]}

    config = ResolvedModelConfig(
        provider="Hiapi",
        litellm_model="openai/gemini-3-pro",
        api_base="https://hiapi.online/v1",
        api_key="sk-test",
    )
    result = asyncio.run(
        probe_model(
            "Gemini3Pro",
            config,
            acompletion_fn=fake_acompletion,
            timeout_seconds=5.0,
        )
    )

    assert result.connectivity_ok is True
    assert result.tool_calling_ok is False
    assert result.tool_calls_count == 0


def test_probe_model_supports_legacy_function_call_shape() -> None:
    async def fake_acompletion(**kwargs):
        del kwargs
        return {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "function_call": {"name": "echo", "arguments": {"text": "hello"}},
                    }
                }
            ]
        }

    config = ResolvedModelConfig(
        provider="zhipu",
        litellm_model="zai/glm-4-plus",
        api_base=None,
        api_key="zhipu-key",
    )
    result = asyncio.run(
        probe_model("GLM4", config, acompletion_fn=fake_acompletion, timeout_seconds=5.0)
    )

    assert result.connectivity_ok is True
    assert result.tool_calling_ok is True
    assert result.tool_calls_count == 1


def test_probe_model_omits_empty_api_base_and_reports_failure() -> None:
    captured: list[dict] = []

    async def fake_acompletion(**kwargs):
        captured.append(kwargs)
        raise RuntimeError("network down")

    config = ResolvedModelConfig(
        provider="minimax",
        litellm_model="minimax/MiniMax-M2",
        api_base="",
        api_key="",
    )
    result = asyncio.run(
        probe_model(
            "MiniMaxM2",
            config,
            acompletion_fn=fake_acompletion,
            timeout_seconds=5.0,
        )
    )

    assert result.connectivity_ok is False
    assert result.tool_calling_ok is None
    assert result.tool_calls_count == 0
    assert "network down" in (result.error or "")
    assert "api_base" not in captured[0]
    assert "api_key" not in captured[0]


def test_probe_model_uses_exception_type_when_message_is_empty() -> None:
    async def fake_acompletion(**kwargs):
        del kwargs
        raise TimeoutError()

    config = ResolvedModelConfig(
        provider="Hiapi",
        litellm_model="openai/gemini-3-pro",
        api_base="https://hiapi.online/v1",
        api_key="sk-test",
    )
    result = asyncio.run(
        probe_model(
            "Gemini3Pro",
            config,
            acompletion_fn=fake_acompletion,
            timeout_seconds=5.0,
        )
    )

    assert result.connectivity_ok is False
    assert result.error == "TimeoutError"
