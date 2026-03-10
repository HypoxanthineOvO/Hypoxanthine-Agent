from __future__ import annotations

import asyncio
from pathlib import Path

from hypo_agent.core.config_loader import RuntimeModelConfig
from hypo_agent.core.skill_manager import SkillManager
from hypo_agent.core.slash_commands import SlashCommandEntry, SlashCommandHandler
from hypo_agent.memory.session import SessionMemory
from hypo_agent.memory.structured_store import StructuredStore
from hypo_agent.models import CircuitBreakerConfig, Message, SkillOutput
from hypo_agent.security.circuit_breaker import CircuitBreaker
from hypo_agent.skills.base import BaseSkill


class StubRouter:
    def __init__(self, config: RuntimeModelConfig) -> None:
        self.config = config

    def get_model_for_task(self, task_type: str) -> str:
        return self.config.task_routing.get(task_type, self.config.default_model)

    def get_fallback_chain(self, start_model: str) -> list[str]:
        chain: list[str] = []
        seen: set[str] = set()
        current: str | None = start_model
        while current is not None and current not in seen:
            chain.append(current)
            seen.add(current)
            current = self.config.models[current].fallback
        return chain


class EchoSkill(BaseSkill):
    name = "echo"
    description = "Echo test skill"
    required_permissions: list[str] = []

    @property
    def tools(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "echo",
                    "description": "Echo input",
                    "parameters": {"type": "object", "properties": {"text": {"type": "string"}}},
                },
            }
        ]

    async def execute(self, tool_name: str, params: dict) -> SkillOutput:
        del tool_name
        return SkillOutput(status="success", result={"echo": params.get("text", "")})


async def _build_handler(tmp_path: Path) -> tuple[
    SlashCommandHandler,
    SessionMemory,
    StructuredStore,
    CircuitBreaker,
]:
    session_memory = SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20)
    store = StructuredStore(db_path=tmp_path / "hypo.db")
    await store.init()
    breaker = CircuitBreaker(CircuitBreakerConfig())
    skill_manager = SkillManager(circuit_breaker=breaker)
    skill_manager.register(EchoSkill())

    runtime = RuntimeModelConfig.model_validate(
        {
            "default_model": "KimiK25",
            "task_routing": {"chat": "KimiK25", "lightweight": "DeepseekV3_2"},
            "models": {
                "KimiK25": {
                    "provider": "volcengine_coding",
                    "litellm_model": "openai/kimi-k2.5",
                    "fallback": "DeepseekV3_2",
                    "description": "chat model",
                    "api_base": "https://ark.cn-beijing.volces.com/api/coding/v3",
                    "api_key": "volc-key",
                },
                "DeepseekV3_2": {
                    "provider": "Volcengine",
                    "litellm_model": "openai/deepseek-v3.2",
                    "fallback": None,
                    "description": "lightweight model",
                    "api_base": "https://ark.cn-beijing.volces.com/api/v3",
                    "api_key": "volc-key",
                },
                "DisabledModel": {
                    "provider": None,
                    "litellm_model": None,
                    "fallback": None,
                },
            },
        }
    )
    router = StubRouter(runtime)
    handler = SlashCommandHandler(
        router=router,
        session_memory=session_memory,
        structured_store=store,
        circuit_breaker=breaker,
        skill_manager=skill_manager,
    )
    return handler, session_memory, store, breaker


def test_slash_commands_returns_none_for_non_slash_message(tmp_path: Path) -> None:
    async def _run() -> None:
        handler, _, _, _ = await _build_handler(tmp_path)
        text = await handler.try_handle(Message(text="hello", sender="user", session_id="s1"))
        assert text is None

    asyncio.run(_run())


def test_help_contains_all_commands(tmp_path: Path) -> None:
    async def _run() -> None:
        handler, _, _, _ = await _build_handler(tmp_path)
        text = await handler.try_handle(Message(text="/help", sender="user", session_id="s1"))
        assert text is not None
        for entry in handler._registry:
            assert entry.command in text

    asyncio.run(_run())


def test_help_chinese(tmp_path: Path) -> None:
    async def _run() -> None:
        handler, _, _, _ = await _build_handler(tmp_path)
        text = await handler.try_handle(Message(text="/help", sender="user", session_id="s1"))
        assert text is not None
        assert "📋 可用斜杠指令" in text
        assert "显示所有可用斜杠指令" in text
        assert "查看模型路由、延迟、Token 消耗" in text

    asyncio.run(_run())


def test_registry_new_command(tmp_path: Path) -> None:
    async def _run() -> None:
        handler, _, _, _ = await _build_handler(tmp_path)
        handler._registry.append(
            SlashCommandEntry(
                command="/newcmd",
                aliases=[],
                description="新增测试指令",
                handler=lambda _: "ok",
            )
        )
        text = await handler.try_handle(Message(text="/help", sender="user", session_id="s1"))
        assert text is not None
        assert "/newcmd" in text
        assert "新增测试指令" in text

    asyncio.run(_run())


def test_slash_commands_unknown_command_returns_hint(tmp_path: Path) -> None:
    async def _run() -> None:
        handler, _, _, _ = await _build_handler(tmp_path)
        text = await handler.try_handle(Message(text="/unknown", sender="user", session_id="s1"))
        assert text is not None
        assert "未知斜杠指令" in text
        assert "/help" in text

    asyncio.run(_run())


def test_slash_commands_kill_toggles_global_state(tmp_path: Path) -> None:
    async def _run() -> None:
        handler, _, _, breaker = await _build_handler(tmp_path)
        first = await handler.try_handle(Message(text="/kill", sender="user", session_id="s1"))
        second = await handler.try_handle(Message(text="/kill", sender="user", session_id="s1"))

        assert first is not None and "开启" in first
        assert second is not None and "关闭" in second
        assert breaker.get_global_kill_switch() is False

    asyncio.run(_run())


def test_slash_commands_clear_clears_current_session(tmp_path: Path) -> None:
    async def _run() -> None:
        handler, session_memory, _, _ = await _build_handler(tmp_path)
        session_memory.append(Message(text="u1", sender="user", session_id="s1"))
        session_memory.append(Message(text="a1", sender="assistant", session_id="s1"))

        result = await handler.try_handle(Message(text="/clear", sender="user", session_id="s1"))

        assert result is not None
        assert "清空" in result
        assert session_memory.get_messages("s1") == []

    asyncio.run(_run())


def test_slash_commands_session_list_returns_sessions(tmp_path: Path) -> None:
    async def _run() -> None:
        handler, session_memory, _, _ = await _build_handler(tmp_path)
        session_memory.append(Message(text="u1", sender="user", session_id="s1"))
        session_memory.append(Message(text="u2", sender="user", session_id="s2"))

        result = await handler.try_handle(
            Message(text="/session list", sender="user", session_id="s1")
        )
        assert result is not None
        assert "s1" in result
        assert "s2" in result

    asyncio.run(_run())


def test_slash_commands_token_and_token_total_use_store_stats(tmp_path: Path) -> None:
    async def _run() -> None:
        handler, _, store, _ = await _build_handler(tmp_path)
        await store.record_token_usage(
            session_id="s1",
            requested_model="KimiK25",
            resolved_model="KimiK25",
            input_tokens=1200,
            output_tokens=800,
            total_tokens=2000,
            latency_ms=100.0,
        )
        await store.record_token_usage(
            session_id="s2",
            requested_model="DeepseekV3_2",
            resolved_model="DeepseekV3_2",
            input_tokens=3,
            output_tokens=4,
            total_tokens=7,
            latency_ms=80.0,
        )

        token_text = await handler.try_handle(
            Message(text="/token", sender="user", session_id="s1")
        )
        total_text = await handler.try_handle(
            Message(text="/token total", sender="user", session_id="s1")
        )

        assert token_text is not None
        assert "s1" in token_text
        assert "KimiK25" in token_text

        assert total_text is not None
        assert "KimiK25" in total_text
        assert "DeepseekV3_2" in total_text
        assert "2007" in total_text

    asyncio.run(_run())


def test_model_status_markdown_table(tmp_path: Path) -> None:
    async def _run() -> None:
        handler, _, store, _ = await _build_handler(tmp_path)
        await store.record_token_usage(
            session_id="s1",
            requested_model="KimiK25",
            resolved_model="KimiK25",
            input_tokens=5,
            output_tokens=6,
            total_tokens=11,
            latency_ms=350.0,
        )

        text = await handler.try_handle(
            Message(text="/model status", sender="user", session_id="s1")
        )
        assert text is not None
        assert "## 🤖 模型状态" in text
        assert "| 任务类型 | 模型 |" in text
        assert "| 模型 | Provider | Fallback | Token (入/出/总) | 平均延迟 |" in text
        assert "|" in text and "---" in text

    asyncio.run(_run())


def test_model_status_token_format(tmp_path: Path) -> None:
    async def _run() -> None:
        handler, _, store, _ = await _build_handler(tmp_path)
        await store.record_token_usage(
            session_id="s1",
            requested_model="KimiK25",
            resolved_model="KimiK25",
            input_tokens=1200,
            output_tokens=800,
            total_tokens=2000,
            latency_ms=350.0,
        )
        text = await handler.try_handle(
            Message(text="/model status", sender="user", session_id="s1")
        )
        assert text is not None
        assert "1.2K/800/2.0K" in text

    asyncio.run(_run())


def test_skills_markdown_table(tmp_path: Path) -> None:
    async def _run() -> None:
        handler, _, _, _ = await _build_handler(tmp_path)
        text = await handler.try_handle(
            Message(text="/skills", sender="user", session_id="s1")
        )
        assert text is not None
        assert "## 🔧 已注册技能" in text
        assert "| 技能 | 状态 | 熔断器 | 工具 | 说明 |" in text

    asyncio.run(_run())


def test_skills_chinese(tmp_path: Path) -> None:
    async def _run() -> None:
        handler, _, _, _ = await _build_handler(tmp_path)
        text = await handler.try_handle(
            Message(text="/skills", sender="user", session_id="s1")
        )
        assert text is not None
        assert "回显测试工具" in text
        assert "✅ 启用" in text
        assert "🟢 正常" in text

    asyncio.run(_run())


def test_skills_kill_switch_status(tmp_path: Path) -> None:
    async def _run() -> None:
        handler, _, _, breaker = await _build_handler(tmp_path)
        breaker.set_global_kill_switch(True)
        text = await handler.try_handle(
            Message(text="/skills", sender="user", session_id="s1")
        )
        assert text is not None
        assert "⚡ Kill Switch: 开启" in text
        assert "🔴 熔断" in text

    asyncio.run(_run())
