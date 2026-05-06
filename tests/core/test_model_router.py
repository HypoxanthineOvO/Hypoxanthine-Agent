from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from litellm.exceptions import InternalServerError

from hypo_agent.core.config_loader import RuntimeModelConfig
from hypo_agent.core.model_router import ModelFallbackError, ModelRouter


def _web_search_tool_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web and return ranked results for a query.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer"},
                },
                "required": ["query"],
            },
        },
    }


def _read_file_tool_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read file content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                },
                "required": ["path"],
            },
        },
    }


@pytest.fixture
def runtime_config() -> RuntimeModelConfig:
    return RuntimeModelConfig.model_validate(
        {
            "default_model": "Gemini3Pro",
            "task_routing": {
                "chat": "Gemini3Pro",
                "lightweight": "DeepseekV3_2",
                "embedding": "VolcanoEmbedding",
            },
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
                "VolcanoEmbedding": {
                    "provider": "volcano",
                    "litellm_model": "openai/doubao-embedding-text-240715",
                    "fallback": None,
                    "api_base": "https://ark.cn-beijing.volces.com/api/v3",
                    "api_key": "volcano-key",
                    "type": "embedding",
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


def test_model_router_vision_chain_uses_only_vision_models() -> None:
    runtime = RuntimeModelConfig.model_validate(
        {
            "default_model": "MimoV25Flash",
            "task_routing": {"vision": "MimoV2Omni"},
            "models": {
                "MimoV2Omni": {
                    "type": "vision",
                    "provider": "MimoTokenPlan",
                    "litellm_model": "openai/mimo-v2-omni",
                    "fallback": "DashscopeQwenVLPlus",
                    "api_base": "https://token-plan-cn.xiaomimimo.com/v1",
                    "api_key": "mimo-key",
                },
                "DashscopeQwenVLPlus": {
                    "type": "vision",
                    "provider": "Dashscope",
                    "litellm_model": "openai/qwen-vl-plus-latest",
                    "fallback": "DashscopeQwen3VLFlash",
                    "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                    "api_key": "dashscope-key",
                },
                "DashscopeQwen3VLFlash": {
                    "type": "vision",
                    "provider": "Dashscope",
                    "litellm_model": "openai/qwen3-vl-flash",
                    "fallback": "MimoV25VisionFlash",
                    "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                    "api_key": "dashscope-key",
                },
                "MimoV25VisionFlash": {
                    "type": "vision",
                    "provider": "MimoTokenPlan",
                    "litellm_model": "openai/mimo-v2.5",
                    "fallback": None,
                    "api_base": "https://token-plan-cn.xiaomimimo.com/v1",
                    "api_key": "mimo-key",
                },
            },
        }
    )
    router = ModelRouter(runtime, acompletion_fn=lambda **_: None)

    chain = router.get_fallback_chain(router.get_model_for_task("vision"))

    assert chain == [
        "MimoV2Omni",
        "DashscopeQwenVLPlus",
        "DashscopeQwen3VLFlash",
        "MimoV25VisionFlash",
    ]
    assert all(runtime.models[item].type == "vision" for item in chain)


def test_model_router_disables_thinking_for_ollama_chat_routes() -> None:
    captured: list[dict] = []
    runtime = RuntimeModelConfig.model_validate(
        {
            "default_model": "EdenQwen",
            "task_routing": {"chat": "EdenQwen", "reasoning": "EdenQwen"},
            "models": {
                "EdenQwen": {
                    "provider": "Eden",
                    "litellm_model": "ollama_chat/qwen3.5:27b",
                    "fallback": None,
                    "api_base": "http://10.19.138.13:11434",
                    "api_key": "dummy",
                }
            },
        }
    )

    async def fake_acompletion(**kwargs):
        captured.append(kwargs)
        return {
            "choices": [{"message": {"content": "hello"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    router = ModelRouter(runtime, acompletion_fn=fake_acompletion)
    text = asyncio.run(
        router.call(
            "EdenQwen",
            [{"role": "user", "content": "hi"}],
            task_type="chat",
        )
    )

    assert text == "hello"
    assert captured[0]["think"] is False


def test_model_router_enables_thinking_for_ollama_reasoning_routes() -> None:
    captured: list[dict] = []
    runtime = RuntimeModelConfig.model_validate(
        {
            "default_model": "EdenGemma",
            "task_routing": {"chat": "EdenGemma", "reasoning": "EdenGemma"},
            "models": {
                "EdenGemma": {
                    "provider": "Eden",
                    "litellm_model": "ollama_chat/gemma4:31b",
                    "fallback": None,
                    "api_base": "http://10.19.138.13:11434",
                    "api_key": "dummy",
                }
            },
        }
    )

    async def fake_acompletion(**kwargs):
        captured.append(kwargs)
        return {
            "choices": [{"message": {"content": "analysis done"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    router = ModelRouter(runtime, acompletion_fn=fake_acompletion)
    text = asyncio.run(
        router.call_with_tools(
            "EdenGemma",
            [{"role": "user", "content": "solve this"}],
            task_type="reasoning",
        )
    )

    assert text["text"] == "analysis done"
    assert captured[0]["think"] is True


def test_model_router_merges_system_messages_for_genesis_qwen() -> None:
    captured: list[dict] = []
    runtime = RuntimeModelConfig.model_validate(
        {
            "default_model": "GenesisQwen122B",
            "task_routing": {"chat": "GenesisQwen122B"},
            "models": {
                "GenesisQwen122B": {
                    "provider": "Genesis",
                    "litellm_model": "openai/qwen3.5-122b",
                    "fallback": None,
                    "api_base": "http://10.15.88.94:8100/v1",
                    "api_key": "genesis-llm-2026",
                    "supports_tool_calling": True,
                }
            },
        }
    )

    async def fake_acompletion(**kwargs):
        captured.append(kwargs)
        return {
            "choices": [
                {
                    "message": {
                        "content": "ok",
                        "tool_calls": [],
                    }
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    router = ModelRouter(runtime, acompletion_fn=fake_acompletion)
    text = asyncio.run(
        router.call(
            "GenesisQwen122B",
            [
                {"role": "system", "content": "你是助手"},
                {"role": "system", "content": "请简洁回答"},
                {"role": "user", "content": "hi"},
            ],
            task_type="chat",
        )
    )

    assert text == "ok"
    assert len([m for m in captured[0]["messages"] if m["role"] == "system"]) == 1
    assert captured[0]["messages"][0]["role"] == "system"
    assert "你是助手" in captured[0]["messages"][0]["content"]
    assert "请简洁回答" in captured[0]["messages"][0]["content"]


def test_model_router_merges_system_messages_for_genesis_local_qwen() -> None:
    captured: list[dict] = []
    runtime = RuntimeModelConfig.model_validate(
        {
            "default_model": "GenesiQWen35BA3B",
            "task_routing": {"chat": "GenesiQWen35BA3B"},
            "models": {
                "GenesiQWen35BA3B": {
                    "provider": "GenesisLocal",
                    "litellm_model": "openai/qwen3.6-35b",
                    "fallback": None,
                    "api_base": "http://localhost:18081/v1",
                    "api_key": "genesis-llm-2026",
                    "supports_tool_calling": True,
                }
            },
        }
    )

    async def fake_acompletion(**kwargs):
        captured.append(kwargs)
        return {
            "choices": [
                {
                    "message": {
                        "content": "ok",
                        "tool_calls": [],
                    }
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    router = ModelRouter(runtime, acompletion_fn=fake_acompletion)
    text = asyncio.run(
        router.call(
            "GenesiQWen35BA3B",
            [
                {"role": "system", "content": "你是助手"},
                {"role": "system", "content": "请简洁回答"},
                {"role": "user", "content": "hi"},
            ],
            task_type="chat",
        )
    )

    assert text == "ok"
    assert len([m for m in captured[0]["messages"] if m["role"] == "system"]) == 1
    assert captured[0]["messages"][0]["role"] == "system"
    assert "你是助手" in captured[0]["messages"][0]["content"]
    assert "请简洁回答" in captured[0]["messages"][0]["content"]


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


def test_model_router_fallback_on_litellm_api_error(runtime_config: RuntimeModelConfig) -> None:
    called_models: list[str] = []

    async def fake_acompletion(**kwargs):
        called_models.append(kwargs["model"])
        if kwargs["model"] == "openai/gemini-2.5-pro":
            raise InternalServerError(
                message="gateway returned html",
                llm_provider="openai",
                model=kwargs["model"],
            )
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


def test_fallback_emits_event(runtime_config: RuntimeModelConfig) -> None:
    called_models: list[str] = []
    emitted: list[dict] = []

    async def fake_acompletion(**kwargs):
        called_models.append(kwargs["model"])
        if kwargs["model"] == "openai/gemini-2.5-pro":
            raise TimeoutError("API timeout")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="fallback ok"))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )

    async def event_emitter(event: dict) -> None:
        emitted.append(event)

    router = ModelRouter(runtime_config, acompletion_fn=fake_acompletion)
    text = asyncio.run(
        router.call(
            "Gemini3Pro",
            [{"role": "user", "content": "hi"}],
            session_id="s-fallback",
            event_emitter=event_emitter,
        )
    )

    assert text == "fallback ok"
    assert called_models == [
        "openai/gemini-2.5-pro",
        "openai/ep-20251215171209-4z5qk",
    ]
    assert len(emitted) == 1
    assert emitted[0]["type"] == "model_fallback"
    assert emitted[0]["failed_model"] == "Gemini3Pro"
    assert emitted[0]["fallback_model"] == "DeepseekV3_2"
    assert emitted[0]["attempted_chain"][0]["model"] == "Gemini3Pro"
    assert emitted[0]["attempted_chain"][0]["error_class"] == "TimeoutError"


def test_fallback_exhausted_raises_structured_error(runtime_config: RuntimeModelConfig) -> None:
    emitted: list[dict] = []

    async def fake_acompletion(**kwargs):
        raise TimeoutError(f"{kwargs['model']} timeout")

    async def event_emitter(event: dict) -> None:
        emitted.append(event)

    router = ModelRouter(runtime_config, acompletion_fn=fake_acompletion)

    with pytest.raises(ModelFallbackError) as exc_info:
        asyncio.run(
            router.call(
                "Gemini3Pro",
                [{"role": "user", "content": "hi"}],
                session_id="s-failed",
                event_emitter=event_emitter,
            )
        )

    assert [item["model"] for item in exc_info.value.attempted_chain] == [
        "Gemini3Pro",
        "DeepseekV3_2",
        "QwenPlus",
    ]
    exhausted = emitted[-1]
    assert exhausted["type"] == "model_fallback_exhausted"
    assert exhausted["attempted_chain"][0]["model"] == "Gemini3Pro"
    assert "Gemini3Pro -> DeepseekV3_2 -> QwenPlus" in exhausted["user_message"]


def test_vision_task_skips_non_vision_fallbacks() -> None:
    runtime = RuntimeModelConfig.model_validate(
        {
            "default_model": "VisionA",
            "task_routing": {"vision": "VisionA"},
            "models": {
                "VisionA": {
                    "type": "vision",
                    "provider": "Hiapi",
                    "litellm_model": "openai/vision-a",
                    "fallback": "ChatOnly",
                    "api_base": "https://example.test/v1",
                    "api_key": "key",
                },
                "ChatOnly": {
                    "type": "chat",
                    "provider": "Hiapi",
                    "litellm_model": "openai/chat",
                    "fallback": "VisionB",
                    "api_base": "https://example.test/v1",
                    "api_key": "key",
                },
                "VisionB": {
                    "type": "vision",
                    "provider": "Hiapi",
                    "litellm_model": "openai/vision-b",
                    "fallback": None,
                    "api_base": "https://example.test/v1",
                    "api_key": "key",
                },
            },
        }
    )
    called_models: list[str] = []

    async def fake_acompletion(**kwargs):
        called_models.append(kwargs["model"])
        if kwargs["model"] == "openai/vision-a":
            raise TimeoutError("vision-a timeout")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="vision ok"))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )

    router = ModelRouter(runtime, acompletion_fn=fake_acompletion)
    text = asyncio.run(
        router.call(
            "VisionA",
            [{"role": "user", "content": [{"type": "text", "text": "看图"}]}],
            task_type="vision",
        )
    )

    assert text == "vision ok"
    assert called_models == ["openai/vision-a", "openai/vision-b"]


def test_model_router_fallback_sanitizes_tool_call_ids_for_gpt5_responses_models() -> None:
    runtime = RuntimeModelConfig.model_validate(
        {
            "default_model": "DeepseekV3_2",
            "task_routing": {"chat": "DeepseekV3_2"},
            "models": {
                "DeepseekV3_2": {
                    "provider": "volcengine_coding",
                    "litellm_model": "openai/deepseek-v3.2",
                    "fallback": "GPT",
                    "api_base": "https://ark.cn-beijing.volces.com/api/v3",
                    "api_key": "volc-key",
                },
                "GPT": {
                    "provider": "AISTOCK",
                    "litellm_model": "openai/gpt-5.2",
                    "fallback": None,
                    "api_base": "https://api.openai.example/v1",
                    "api_key": "gpt-key",
                },
            },
        }
    )
    captured: list[dict] = []
    original_tool_call_id = "call_fXveNA5ZEadmkDm42ZZS7HnI"
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": original_tool_call_id,
                    "type": "function",
                    "function": {
                        "name": "exec_command",
                        "arguments": "{\"command\": \"echo hi\"}",
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": original_tool_call_id,
            "content": "{\"status\": \"success\", \"result\": \"hi\"}",
        },
    ]

    async def fake_acompletion(**kwargs):
        captured.append(kwargs)
        if kwargs["model"] == "openai/deepseek-v3.2":
            raise RuntimeError("primary failed")
        return {
            "choices": [{"message": {"content": "fallback ok", "tool_calls": []}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    router = ModelRouter(runtime, acompletion_fn=fake_acompletion)
    text = asyncio.run(router.call("DeepseekV3_2", messages))

    assert text == "fallback ok"
    assert len(captured) == 2
    first_assistant = next(item for item in captured[0]["messages"] if item.get("role") == "assistant")
    fallback_assistant = next(item for item in captured[1]["messages"] if item.get("role") == "assistant")
    fallback_tool = next(item for item in captured[1]["messages"] if item.get("role") == "tool")
    assert first_assistant["tool_calls"][0]["id"] == original_tool_call_id
    assert captured[1]["model"] == "openai/gpt-5.2"
    assert fallback_assistant["tool_calls"][0]["id"].startswith("fc_")
    assert fallback_tool["tool_call_id"] == fallback_assistant["tool_calls"][0]["id"]
    assert messages[0]["tool_calls"][0]["id"] == original_tool_call_id
    assert messages[1]["tool_call_id"] == original_tool_call_id


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


def test_model_router_antigravity_noop_when_tool_names_are_clean() -> None:
    runtime = RuntimeModelConfig.model_validate(
        {
            "default_model": "GeminiLow",
            "task_routing": {"chat": "GeminiLow"},
            "models": {
                "GeminiLow": {
                    "provider": "VSPLab_Gemini",
                    "litellm_model": "anthropic/gemini-3.1-pro-low",
                    "fallback": None,
                    "api_base": "http://api.vsplab.cn/antigravity",
                    "api_key": "test-key",
                    "supports_tool_calling": True,
                }
            },
        }
    )
    captured: list[dict[str, Any]] = []
    emitted: list[dict[str, Any]] = []

    async def fake_acompletion(**kwargs):
        captured.append(kwargs)
        return {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "search_web",
                                    "arguments": "{\"query\":\"hi\"}",
                                },
                            }
                        ],
                    }
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    async def event_emitter(event: dict[str, Any]) -> None:
        emitted.append(event)

    router = ModelRouter(runtime, acompletion_fn=fake_acompletion)
    result = asyncio.run(
        router.call_with_tools(
            "GeminiLow",
            [{"role": "user", "content": "hi"}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "search_web",
                        "description": "Search the web and return ranked results for a query.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "query": {"type": "string"},
                            },
                            "required": ["query"],
                        },
                    },
                },
                _read_file_tool_schema(),
            ],
            session_id="s-antigravity",
            event_emitter=event_emitter,
        )
    )

    assert captured[0]["tools"][0]["function"]["name"] == "search_web"
    assert captured[0]["tools"][1]["function"]["name"] == "read_file"
    assert result["tool_calls"][0]["function"]["name"] == "search_web"
    assert emitted == []


def test_model_router_does_not_apply_antigravity_tool_transform_to_other_providers() -> None:
    runtime = RuntimeModelConfig.model_validate(
        {
            "default_model": "GPT",
            "task_routing": {"chat": "GPT"},
            "models": {
                "GPT": {
                    "provider": "VSPLab",
                    "litellm_model": "openai/gpt-5.4",
                    "fallback": None,
                    "api_base": "https://api.example.com/v1",
                    "api_key": "test-key",
                    "supports_tool_calling": True,
                }
            },
        }
    )
    captured: list[dict[str, Any]] = []

    async def fake_acompletion(**kwargs):
        captured.append(kwargs)
        return {
            "choices": [{"message": {"content": "ok", "tool_calls": []}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    router = ModelRouter(runtime, acompletion_fn=fake_acompletion)
    result = asyncio.run(
        router.call_with_tools(
            "GPT",
            [{"role": "user", "content": "hi"}],
            tools=[_web_search_tool_schema()],
        )
    )

    assert captured[0]["tools"][0]["function"]["name"] == "web_search"
    assert result["tool_calls"] == []


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


def test_model_router_call_lightweight_json(runtime_config: RuntimeModelConfig) -> None:
    called_models: list[str] = []

    async def fake_acompletion(**kwargs):
        called_models.append(kwargs["model"])
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"schedule_type":"once","timezone":"Asia/Shanghai"}'
                    )
                )
            ],
            usage=SimpleNamespace(prompt_tokens=2, completion_tokens=2, total_tokens=4),
        )

    router = ModelRouter(runtime_config, acompletion_fn=fake_acompletion)
    result = asyncio.run(router.call_lightweight_json("parse this", session_id="s1"))

    assert called_models == ["openai/ep-20251215171209-4z5qk"]
    assert result["schedule_type"] == "once"
    assert result["timezone"] == "Asia/Shanghai"


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
    assert len(emitted) == 1
    assert emitted[0]["event"] == "model_stream_success"
    assert emitted[0]["session_id"] == "s1"
    assert emitted[0]["requested_model"] == "Gemini3Pro"
    assert emitted[0]["resolved_model"] == "Gemini3Pro"
    assert emitted[0]["input_tokens"] == 3
    assert emitted[0]["output_tokens"] == 2
    assert emitted[0]["total_tokens"] == 5
    assert isinstance(emitted[0]["latency_ms"], float)
    assert emitted[0]["latency_ms"] >= 0


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


def test_model_router_call_passes_tools_to_acompletion(
    runtime_config: RuntimeModelConfig,
) -> None:
    captured: list[dict] = []
    tools = [
        {
            "type": "function",
            "function": {"name": "exec_command", "parameters": {"type": "object"}},
        }
    ]

    async def fake_acompletion(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )

    router = ModelRouter(runtime_config, acompletion_fn=fake_acompletion)
    text = asyncio.run(
        router.call(
            "Gemini3Pro",
            [{"role": "user", "content": "hi"}],
            tools=tools,
        )
    )

    assert text == "ok"
    assert captured[0]["tools"] == tools


def test_model_router_stream_passes_tools_to_acompletion(
    runtime_config: RuntimeModelConfig,
) -> None:
    captured: list[dict] = []
    tools = [
        {
            "type": "function",
            "function": {"name": "exec_command", "parameters": {"type": "object"}},
        }
    ]

    async def fake_acompletion(**kwargs):
        captured.append(kwargs)

        async def _gen():
            yield {"choices": [{"delta": {"content": "ok"}}]}

        return _gen()

    router = ModelRouter(runtime_config, acompletion_fn=fake_acompletion)

    async def _collect() -> list[str]:
        chunks: list[str] = []
        async for chunk in router.stream(
            "Gemini3Pro",
            [{"role": "user", "content": "hi"}],
            tools=tools,
        ):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(_collect())
    assert chunks == ["ok"]
    assert captured[0]["tools"] == tools


def test_model_router_call_with_tools_extracts_tool_calls(
    runtime_config: RuntimeModelConfig,
) -> None:
    async def fake_acompletion(**kwargs):
        del kwargs
        return {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "exec_command",
                                    "arguments": "{\"command\": \"echo hi\"}",
                                },
                            }
                        ],
                    }
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    router = ModelRouter(runtime_config, acompletion_fn=fake_acompletion)
    payload = asyncio.run(
        router.call_with_tools(
            "Gemini3Pro",
            [{"role": "user", "content": "hi"}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "exec_command",
                        "parameters": {"type": "object"},
                    },
                }
            ],
        )
    )

    assert payload["text"] == ""
    assert payload["tool_calls"][0]["function"]["name"] == "exec_command"


def test_model_router_call_with_tools_preserves_reasoning_content() -> None:
    runtime = RuntimeModelConfig.model_validate(
        {
            "default_model": "DeepSeekV4",
            "task_routing": {"reasoning": "DeepSeekV4"},
            "models": {
                "DeepSeekV4": {
                    "provider": "Deepseek",
                    "litellm_model": "deepseek/deepseek-v4-flash",
                    "fallback": None,
                    "api_base": "https://api.deepseek.com",
                    "api_key": "sk-deepseek",
                    "supports_tool_calling": True,
                }
            },
        }
    )

    async def fake_acompletion(**kwargs):
        del kwargs
        return {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "reasoning_content": "Need to call the tool first.",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "exec_command",
                                    "arguments": "{\"command\": \"echo hi\"}",
                                },
                            }
                        ],
                    }
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    router = ModelRouter(runtime, acompletion_fn=fake_acompletion)
    payload = asyncio.run(
        router.call_with_tools(
            "DeepSeekV4",
            [{"role": "user", "content": "hi"}],
            task_type="reasoning",
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "exec_command",
                        "parameters": {"type": "object"},
                    },
                }
            ],
        )
    )

    assert payload["assistant_message"] == {
        "role": "assistant",
        "content": "",
        "reasoning_content": "Need to call the tool first.",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "exec_command",
                    "arguments": "{\"command\": \"echo hi\"}",
                },
            }
        ],
    }


def test_model_router_call_with_tools_extracts_legacy_function_call(
    runtime_config: RuntimeModelConfig,
) -> None:
    async def fake_acompletion(**kwargs):
        del kwargs
        return {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "function_call": {
                            "name": "exec_command",
                            "arguments": {"command": "echo hello"},
                        },
                    }
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    router = ModelRouter(runtime_config, acompletion_fn=fake_acompletion)
    payload = asyncio.run(
        router.call_with_tools(
            "Gemini3Pro",
            [{"role": "user", "content": "hi"}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "exec_command",
                        "parameters": {"type": "object"},
                    },
                }
            ],
        )
    )

    assert payload["tool_calls"][0]["function"]["name"] == "exec_command"
    assert "\"command\": \"echo hello\"" in payload["tool_calls"][0]["function"]["arguments"]


def test_model_router_call_with_tools_extracts_gemini_content_tool_part(
    runtime_config: RuntimeModelConfig,
) -> None:
    async def fake_acompletion(**kwargs):
        del kwargs
        return {
            "choices": [
                {
                    "message": {
                        "content": [
                            {
                                "type": "function_call",
                                "id": "gemini_1",
                                "name": "exec_command",
                                "arguments": {"command": "echo gemini"},
                            }
                        ]
                    }
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    router = ModelRouter(runtime_config, acompletion_fn=fake_acompletion)
    payload = asyncio.run(
        router.call_with_tools(
            "Gemini3Pro",
            [{"role": "user", "content": "hi"}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "exec_command",
                        "parameters": {"type": "object"},
                    },
                }
            ],
        )
    )

    assert payload["tool_calls"][0]["id"] == "gemini_1"
    assert payload["tool_calls"][0]["function"]["name"] == "exec_command"


def test_model_router_call_with_tools_extracts_text_embedded_json_tool_call(
    runtime_config: RuntimeModelConfig,
) -> None:
    async def fake_acompletion(**kwargs):
        del kwargs
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"name": "update_persona_memory", '
                            '"arguments": {"key": "回复风格", "value": "简洁"}}'
                        )
                    }
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    router = ModelRouter(runtime_config, acompletion_fn=fake_acompletion)
    payload = asyncio.run(
        router.call_with_tools(
            "Gemini3Pro",
            [{"role": "user", "content": "hi"}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "update_persona_memory",
                        "parameters": {"type": "object"},
                    },
                }
            ],
        )
    )

    assert payload["tool_calls"][0]["function"]["name"] == "update_persona_memory"
    assert '"key": "回复风格"' in payload["tool_calls"][0]["function"]["arguments"]


def test_model_router_call_with_tools_extracts_text_embedded_dsml_tool_call(
    runtime_config: RuntimeModelConfig,
) -> None:
    async def fake_acompletion(**kwargs):
        del kwargs
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            '<｜DSML｜tool_calls> <｜DSML｜invoke name="write_file"> '
                            '<｜DSML｜parameter name="path" string="true">'
                            "/home/heyx/Hypo-Agent/memory/knowledge/belongings.md"
                            "</｜DSML｜parameter> "
                            '<｜DSML｜parameter name="content" string="true">'
                            "# 重要物品存放位置\n学生证: 工位左手边抽屉"
                            "</｜DSML｜parameter> "
                            "</｜DSML｜invoke> </｜DSML｜tool_calls>"
                        )
                    }
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    router = ModelRouter(runtime_config, acompletion_fn=fake_acompletion)
    payload = asyncio.run(
        router.call_with_tools(
            "Gemini3Pro",
            [{"role": "user", "content": "hi"}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "parameters": {"type": "object"},
                    },
                }
            ],
        )
    )

    assert payload["text"] == ""
    assert payload["tool_calls"][0]["function"]["name"] == "write_file"
    assert payload["tool_calls"][0]["function"]["arguments"] == (
        '{"path": "/home/heyx/Hypo-Agent/memory/knowledge/belongings.md", '
        '"content": "# 重要物品存放位置\\n学生证: 工位左手边抽屉"}'
    )


def test_model_router_call_with_tools_strips_non_executable_dsml_payload(
    runtime_config: RuntimeModelConfig,
) -> None:
    async def fake_acompletion(**kwargs):
        del kwargs
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            "搜索完成。\n"
                            "<｜DSML｜tool_calls>"
                            "<｜DSML｜invoke>"
                            "<｜DSML｜parameter name=\"query\">Hypo Agent</｜DSML｜parameter>"
                            "</｜DSML｜invoke>"
                            "</｜DSML｜tool_calls>\n"
                            "请参考上面的结果。"
                        )
                    }
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    router = ModelRouter(runtime_config, acompletion_fn=fake_acompletion)
    payload = asyncio.run(
        router.call_with_tools(
            "Gemini3Pro",
            [{"role": "user", "content": "hi"}],
            tools=[_web_search_tool_schema()],
        )
    )

    assert payload["tool_calls"] == []
    assert payload["text"] == "搜索完成。\n\n请参考上面的结果。"
    assert "DSML" not in payload["text"]


def test_model_router_stream_strips_split_dsml_tool_payload(
    runtime_config: RuntimeModelConfig,
) -> None:
    async def fake_acompletion(**kwargs):
        assert kwargs["stream"] is True

        async def _gen():
            yield {"choices": [{"delta": {"content": "搜索完成。\n<｜DS"}}]}
            yield {
                "choices": [
                    {
                        "delta": {
                            "content": (
                                "ML｜tool_calls><｜DSML｜invoke name=\"web_search\">"
                                "<｜DSML｜parameter name=\"query\">Hypo Agent"
                            )
                        }
                    }
                ]
            }
            yield {
                "choices": [
                    {
                        "delta": {
                            "content": (
                                "</｜DSML｜parameter></｜DSML｜invoke>"
                                "</｜DSML｜tool_calls>\n最终答案。"
                            )
                        }
                    }
                ]
            }

        return _gen()

    router = ModelRouter(runtime_config, acompletion_fn=fake_acompletion)

    async def _collect() -> list[str]:
        chunks: list[str] = []
        async for chunk in router.stream(
            "Gemini3Pro",
            [{"role": "user", "content": "hi"}],
            tools=[_web_search_tool_schema()],
        ):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(_collect())
    assert "".join(chunks) == "搜索完成。\n\n最终答案。"
    assert "DSML" not in "".join(chunks)


def test_model_router_does_not_pass_api_base_when_missing() -> None:
    runtime = RuntimeModelConfig.model_validate(
        {
            "default_model": "MiniMaxM2",
            "task_routing": {"chat": "MiniMaxM2"},
            "models": {
                "MiniMaxM2": {
                    "provider": "minimax",
                    "litellm_model": "minimax/MiniMax-M2",
                    "fallback": None,
                    "api_base": None,
                    "api_key": "mini-key",
                }
            },
        }
    )
    captured: list[dict] = []

    async def fake_acompletion(**kwargs):
        captured.append(kwargs)
        return {
            "choices": [{"message": {"content": "ok", "tool_calls": []}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    router = ModelRouter(runtime, acompletion_fn=fake_acompletion)
    result = asyncio.run(router.call("MiniMaxM2", [{"role": "user", "content": "hi"}]))

    assert result == "ok"
    assert "api_base" not in captured[0]
    assert captured[0]["api_key"] == "mini-key"


def test_model_router_get_model_for_task_uses_task_routing(
    runtime_config: RuntimeModelConfig,
) -> None:
    async def fake_acompletion(**kwargs):
        del kwargs
        return {
            "choices": [{"message": {"content": "ok", "tool_calls": []}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    router = ModelRouter(runtime_config, acompletion_fn=fake_acompletion)

    assert router.get_model_for_task("chat") == "Gemini3Pro"
    assert router.get_model_for_task("lightweight") == "DeepseekV3_2"
    assert router.get_model_for_task("missing_task") == "Gemini3Pro"


def test_model_router_get_model_for_task_falls_back_when_routed_model_missing() -> None:
    runtime = RuntimeModelConfig.model_validate(
        {
            "default_model": "Gemini3Pro",
            "task_routing": {"lightweight": "DeepseekV3_2"},
            "models": {
                "Gemini3Pro": {
                    "provider": "Hiapi",
                    "litellm_model": "openai/gemini-2.5-pro",
                    "fallback": None,
                    "api_base": "https://hiapi.online/v1",
                    "api_key": "sk-hiapi",
                }
            },
        }
    )

    async def fake_acompletion(**kwargs):
        del kwargs
        return {
            "choices": [{"message": {"content": "{\"ok\": true}", "tool_calls": []}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    router = ModelRouter(runtime, acompletion_fn=fake_acompletion)

    assert router.get_model_for_task("lightweight") == "Gemini3Pro"


def test_model_router_call_with_tools_emits_success_event_with_latency(
    runtime_config: RuntimeModelConfig,
) -> None:
    emitted: list[dict] = []

    async def on_stream_success(event: dict) -> None:
        emitted.append(event)

    async def fake_acompletion(**kwargs):
        del kwargs
        return {
            "choices": [{"message": {"content": "ok", "tool_calls": []}}],
            "usage": {"prompt_tokens": 9, "completion_tokens": 4, "total_tokens": 13},
        }

    router = ModelRouter(
        runtime_config,
        acompletion_fn=fake_acompletion,
        on_stream_success=on_stream_success,
    )

    payload = asyncio.run(
        router.call_with_tools(
            "Gemini3Pro",
            [{"role": "user", "content": "hi"}],
            session_id="s-latency",
        )
    )

    assert payload["text"] == "ok"
    assert emitted[0]["event"] == "model_call_success"
    assert emitted[0]["session_id"] == "s-latency"
    assert emitted[0]["requested_model"] == "Gemini3Pro"
    assert emitted[0]["resolved_model"] == "Gemini3Pro"
    assert emitted[0]["input_tokens"] == 9
    assert emitted[0]["output_tokens"] == 4
    assert emitted[0]["total_tokens"] == 13
    assert isinstance(emitted[0]["latency_ms"], float)
    assert emitted[0]["latency_ms"] >= 0


def test_model_router_stream_emits_latency_ms(runtime_config: RuntimeModelConfig) -> None:
    emitted: list[dict] = []

    async def on_stream_success(event: dict) -> None:
        emitted.append(event)

    async def fake_acompletion(**kwargs):
        del kwargs

        async def _gen():
            yield {"choices": [{"delta": {"content": "ok"}}]}
            yield {
                "choices": [{"delta": {"content": "!"}}],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 2,
                    "total_tokens": 3,
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
            session_id="s-stream-latency",
        ):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(_collect())
    assert chunks == ["ok", "!"]
    assert emitted[0]["event"] == "model_stream_success"
    assert emitted[0]["session_id"] == "s-stream-latency"
    assert isinstance(emitted[0]["latency_ms"], float)
    assert emitted[0]["latency_ms"] >= 0


def test_model_router_embed_uses_embedding_task_model_and_batch_input(
    runtime_config: RuntimeModelConfig,
) -> None:
    captured: list[dict] = []

    async def fake_aembedding(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(
            data=[
                SimpleNamespace(embedding=[0.1, 0.2, 0.3]),
                {"embedding": [0.4, 0.5, 0.6]},
            ]
        )

    router = ModelRouter(
        runtime_config,
        acompletion_fn=lambda **_: None,
        aembedding_fn=fake_aembedding,
    )
    result = asyncio.run(router.embed(["hello", "world"]))

    assert result == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    assert captured[0]["model"] == "openai/doubao-embedding-text-240715"
    assert captured[0]["input"] == ["hello", "world"]
    assert captured[0]["api_base"] == "https://ark.cn-beijing.volces.com/api/v3"
    assert captured[0]["api_key"] == "volcano-key"


def test_model_router_embed_retries_before_success(
    runtime_config: RuntimeModelConfig,
) -> None:
    attempts: list[str] = []

    async def fake_aembedding(**kwargs):
        attempts.append(kwargs["model"])
        if len(attempts) < 3:
            raise RuntimeError("temporary embed failure")
        return SimpleNamespace(data=[{"embedding": [1.0, 0.0]}])

    router = ModelRouter(
        runtime_config,
        acompletion_fn=lambda **_: None,
        aembedding_fn=fake_aembedding,
        embed_retry_attempts=3,
        embed_retry_backoff_seconds=0.0,
    )
    result = asyncio.run(router.embed(["retry me"]))

    assert result == [[1.0, 0.0]]
    assert attempts == [
        "openai/doubao-embedding-text-240715",
        "openai/doubao-embedding-text-240715",
        "openai/doubao-embedding-text-240715",
    ]
