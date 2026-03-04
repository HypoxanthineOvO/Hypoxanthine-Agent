from __future__ import annotations

import asyncio

from hypo_agent.core.skill_manager import SkillManager
from hypo_agent.models import CircuitBreakerConfig, SkillOutput
from hypo_agent.security.circuit_breaker import CircuitBreaker
from hypo_agent.skills.base import BaseSkill


class EchoSkill(BaseSkill):
    name = "echo"
    description = "Echo input text"
    required_permissions = []

    @property
    def tools(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "echo",
                    "description": "Echo user text",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                        },
                        "required": ["text"],
                    },
                },
            }
        ]

    async def execute(self, tool_name: str, params: dict) -> SkillOutput:
        return SkillOutput(status="success", result={"echo": params["text"]})


def test_skill_manager_registers_tools_schema() -> None:
    manager = SkillManager()
    manager.register(EchoSkill())

    tools = manager.get_tools_schema()
    assert len(tools) == 1
    assert tools[0]["function"]["name"] == "echo"


def test_skill_manager_invokes_registered_tool() -> None:
    manager = SkillManager()
    manager.register(EchoSkill())

    result = asyncio.run(
        manager.invoke("echo", {"text": "hello"}, session_id="s1"),
    )
    assert result.status == "success"
    assert result.result == {"echo": "hello"}


def test_skill_manager_loads_enabled_skills_from_yaml(tmp_path) -> None:
    config = tmp_path / "skills.yaml"
    config.write_text(
        """
default_timeout_seconds: 30
skills:
  tmux:
    enabled: true
  code_run:
    enabled: false
""".strip(),
        encoding="utf-8",
    )

    enabled = SkillManager.find_enabled_skills(config)
    assert enabled == {"tmux"}


def test_skill_manager_invoke_checks_circuit_breaker_before_execution() -> None:
    class BlockedCircuitBreaker:
        def can_execute(self, tool_name: str, session_id: str | None):
            assert tool_name == "echo"
            assert session_id == "s1"
            return False, "blocked for test"

        def record_success(self, tool_name: str, session_id: str | None) -> None:
            raise AssertionError("record_success should not be called when blocked")

        def record_failure(self, tool_name: str, session_id: str | None) -> None:
            raise AssertionError("record_failure should not be called when blocked")

    manager = SkillManager(circuit_breaker=BlockedCircuitBreaker())
    manager.register(EchoSkill())
    output = asyncio.run(manager.invoke("echo", {"text": "hello"}, session_id="s1"))
    assert output.status == "error"
    assert "blocked for test" in output.error_info


def test_skill_manager_records_circuit_breaker_success_and_failure() -> None:
    class FailingSkill(EchoSkill):
        name = "fail_skill"

        @property
        def tools(self) -> list[dict]:
            payload = super().tools[0]
            payload["function"]["name"] = "always_fail"
            return [payload]

        async def execute(self, tool_name: str, params: dict) -> SkillOutput:
            raise RuntimeError("boom")

    class RecorderCircuitBreaker:
        def __init__(self) -> None:
            self.successes: list[tuple[str, str | None]] = []
            self.failures: list[tuple[str, str | None]] = []

        def can_execute(self, tool_name: str, session_id: str | None):
            return True, ""

        def record_success(self, tool_name: str, session_id: str | None) -> None:
            self.successes.append((tool_name, session_id))

        def record_failure(self, tool_name: str, session_id: str | None) -> None:
            self.failures.append((tool_name, session_id))

    breaker = RecorderCircuitBreaker()
    manager = SkillManager(circuit_breaker=breaker)
    manager.register(EchoSkill())
    manager.register(FailingSkill())

    success = asyncio.run(manager.invoke("echo", {"text": "ok"}, session_id="s1"))
    failure = asyncio.run(manager.invoke("always_fail", {}, session_id="s1"))

    assert success.status == "success"
    assert failure.status == "error"
    assert breaker.successes == [("echo", "s1")]
    assert breaker.failures == [("always_fail", "s1")]


def test_skill_manager_respects_global_kill_switch() -> None:
    breaker = CircuitBreaker(CircuitBreakerConfig(global_kill_switch=False))
    manager = SkillManager(circuit_breaker=breaker)
    manager.register(EchoSkill())

    breaker.set_global_kill_switch(True)
    output = asyncio.run(manager.invoke("echo", {"text": "hello"}, session_id="s1"))

    assert output.status == "error"
    assert "kill switch" in output.error_info.lower()
