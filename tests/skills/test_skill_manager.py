from __future__ import annotations

import asyncio
from pathlib import Path

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


class FileReadSkill(BaseSkill):
    name = "filesystem"
    description = "Read files"
    required_permissions = ["filesystem"]

    def __init__(self) -> None:
        self.calls = 0

    @property
    def tools(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read a file",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                        },
                        "required": ["path"],
                    },
                },
            }
        ]

    async def execute(self, tool_name: str, params: dict) -> SkillOutput:
        self.calls += 1
        return SkillOutput(status="success", result={"path": params["path"]})


def test_skill_manager_registers_tools_schema() -> None:
    manager = SkillManager()
    manager.register(EchoSkill())

    tools = manager.get_tools_schema()
    assert len(tools) == 1
    assert tools[0]["function"]["name"] == "echo"


def test_skill_manager_get_tools_schema_prepends_builtin_tools() -> None:
    manager = SkillManager()

    async def handler(params: dict, *, session_id: str | None = None) -> SkillOutput:
        del params, session_id
        return SkillOutput(status="success", result={})

    manager.register_builtin_tool(
        {
            "type": "function",
            "function": {
                "name": "update_persona_memory",
                "description": "Persist persona memory.",
                "parameters": {"type": "object"},
            },
        },
        handler,
        source="builtin",
    )
    manager.register(EchoSkill())

    tools = manager.get_tools_schema()

    assert [tool["function"]["name"] for tool in tools] == [
        "update_persona_memory",
        "echo",
    ]


def test_skill_manager_invokes_registered_tool() -> None:
    manager = SkillManager()
    manager.register(EchoSkill())

    result = asyncio.run(
        manager.invoke("echo", {"text": "hello"}, session_id="s1"),
    )
    assert result.status == "success"
    assert result.result == {"echo": "hello"}


def test_skill_manager_aclose_closes_registered_skills() -> None:
    class ClosableSkill(EchoSkill):
        def __init__(self) -> None:
            self.closed = 0

        async def close(self) -> None:
            self.closed += 1

    skill = ClosableSkill()
    manager = SkillManager()
    manager.register(skill)

    asyncio.run(manager.aclose())

    assert skill.closed == 1


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


def test_skill_manager_loads_enabled_email_scanner_from_yaml(tmp_path) -> None:
    config = tmp_path / "skills.yaml"
    config.write_text(
        """
default_timeout_seconds: 30
skills:
  email_scanner:
    enabled: true
  tmux:
    enabled: false
""".strip(),
        encoding="utf-8",
    )
    enabled = SkillManager.find_enabled_skills(config)
    assert enabled == {"email_scanner"}


def test_repo_skills_config_enables_memory() -> None:
    config = Path(__file__).resolve().parents[2] / "config" / "skills.yaml"

    enabled = SkillManager.find_enabled_skills(config)

    assert "exec" in enabled
    assert "memory" in enabled
    assert "qq" not in enabled


def test_repo_skills_config_enables_probe() -> None:
    config = Path(__file__).resolve().parents[2] / "config" / "skills.yaml"

    enabled = SkillManager.find_enabled_skills(config)

    assert "probe" in enabled


def test_skill_manager_invoke_checks_circuit_breaker_before_execution() -> None:
    class BlockedCircuitBreaker:
        def can_execute(
            self,
            tool_name: str,
            session_id: str | None,
            skill_name: str | None = None,
        ):
            assert tool_name == "echo"
            assert session_id == "s1"
            assert skill_name == "echo"
            return False, "blocked for test"

        def record_success(
            self,
            tool_name: str,
            session_id: str | None,
            skill_name: str | None = None,
        ) -> None:
            raise AssertionError("record_success should not be called when blocked")

        def record_failure(
            self,
            tool_name: str,
            session_id: str | None,
            skill_name: str | None = None,
        ) -> None:
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
            self.successes: list[tuple[str, str | None, str | None]] = []
            self.failures: list[tuple[str, str | None, str | None]] = []

        def can_execute(
            self,
            tool_name: str,
            session_id: str | None,
            skill_name: str | None = None,
        ):
            del skill_name
            return True, ""

        def record_success(
            self,
            tool_name: str,
            session_id: str | None,
            skill_name: str | None = None,
        ) -> None:
            self.successes.append((tool_name, session_id, skill_name))

        def record_failure(
            self,
            tool_name: str,
            session_id: str | None,
            skill_name: str | None = None,
        ) -> None:
            self.failures.append((tool_name, session_id, skill_name))

    breaker = RecorderCircuitBreaker()
    manager = SkillManager(circuit_breaker=breaker)
    manager.register(EchoSkill())
    manager.register(FailingSkill())

    success = asyncio.run(manager.invoke("echo", {"text": "ok"}, session_id="s1"))
    failure = asyncio.run(manager.invoke("always_fail", {}, session_id="s1"))

    assert success.status == "success"
    assert failure.status == "error"
    assert breaker.successes == [("echo", "s1", "echo")]
    assert breaker.failures == [("always_fail", "s1", "fail_skill")]


def test_skill_manager_respects_global_kill_switch() -> None:
    breaker = CircuitBreaker(CircuitBreakerConfig(global_kill_switch=False))
    manager = SkillManager(circuit_breaker=breaker)
    manager.register(EchoSkill())

    breaker.set_global_kill_switch(True)
    output = asyncio.run(manager.invoke("echo", {"text": "hello"}, session_id="s1"))

    assert output.status == "error"
    assert "kill switch" in output.error_info.lower()


def test_skill_manager_blocks_when_permission_denied() -> None:
    class AllowBreaker:
        def can_execute(
            self,
            tool_name: str,
            session_id: str | None,
            skill_name: str | None = None,
        ):
            del skill_name
            return True, ""

        def record_success(
            self,
            tool_name: str,
            session_id: str | None,
            skill_name: str | None = None,
        ) -> None:
            return None

        def record_failure(
            self,
            tool_name: str,
            session_id: str | None,
            skill_name: str | None = None,
        ) -> None:
            return None

    class DeniedPermissionManager:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def check_permission(self, path: str, operation: str):
            self.calls.append((path, operation))
            return False, "permission denied for test"

    pm = DeniedPermissionManager()
    skill = FileReadSkill()
    manager = SkillManager(circuit_breaker=AllowBreaker(), permission_manager=pm)
    manager.register(skill)

    output = asyncio.run(manager.invoke("read_file", {"path": "/tmp/test.txt"}, session_id="s1"))
    assert output.status == "error"
    assert "permission" in output.error_info.lower()
    assert skill.calls == 0
    assert pm.calls == [("/tmp/test.txt", "read")]


def test_skill_manager_allows_when_permission_granted() -> None:
    class AllowBreaker:
        def can_execute(
            self,
            tool_name: str,
            session_id: str | None,
            skill_name: str | None = None,
        ):
            del skill_name
            return True, ""

        def record_success(
            self,
            tool_name: str,
            session_id: str | None,
            skill_name: str | None = None,
        ) -> None:
            return None

        def record_failure(
            self,
            tool_name: str,
            session_id: str | None,
            skill_name: str | None = None,
        ) -> None:
            return None

    class AllowedPermissionManager:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def check_permission(self, path: str, operation: str):
            self.calls.append((path, operation))
            return True, ""

    pm = AllowedPermissionManager()
    skill = FileReadSkill()
    manager = SkillManager(circuit_breaker=AllowBreaker(), permission_manager=pm)
    manager.register(skill)

    output = asyncio.run(manager.invoke("read_file", {"path": "/tmp/test.txt"}, session_id="s1"))
    assert output.status == "success"
    assert skill.calls == 1
    assert pm.calls == [("/tmp/test.txt", "read")]


def test_skill_manager_skips_permission_check_for_skills_without_permissions() -> None:
    class PermissionManagerThatMustNotBeCalled:
        def check_permission(self, path: str, operation: str):
            raise AssertionError("check_permission should not be called")

    manager = SkillManager(permission_manager=PermissionManagerThatMustNotBeCalled())
    manager.register(EchoSkill())

    output = asyncio.run(manager.invoke("echo", {"text": "ok"}, session_id="s1"))
    assert output.status == "success"


def test_skill_manager_infers_scan_directory_as_read_operation() -> None:
    manager = SkillManager()
    assert manager._infer_operation("scan_directory") == "read"
    assert manager._infer_operation("update_directory_description") == "read"


def test_skill_manager_records_tool_invocations() -> None:
    class RecordingStructuredStore:
        def __init__(self) -> None:
            self.records: list[dict] = []

        async def record_tool_invocation(self, **kwargs) -> int:
            self.records.append(kwargs)
            return 7

    store = RecordingStructuredStore()
    manager = SkillManager(structured_store=store)
    manager.register(EchoSkill())

    output = asyncio.run(manager.invoke("echo", {"text": "hello"}, session_id="s1"))
    assert output.status == "success"

    assert len(store.records) == 1
    record = store.records[0]
    assert record["session_id"] == "s1"
    assert record["tool_name"] == "echo"
    assert record["skill_name"] == "echo"
    assert record["params_json"] == '{"text": "hello"}'
    assert record["status"] == "success"
    assert record["duration_ms"] >= 0
    assert isinstance(record["result_summary"], str)
    assert record["compressed_meta_json"] is None
    assert output.metadata["invocation_id"] == 7


def test_skill_manager_records_blocked_tool_invocations() -> None:
    class BlockedCircuitBreaker:
        def can_execute(
            self,
            tool_name: str,
            session_id: str | None,
            skill_name: str | None = None,
        ):
            del skill_name
            return False, "blocked for test"

        def record_success(
            self,
            tool_name: str,
            session_id: str | None,
            skill_name: str | None = None,
        ) -> None:
            raise AssertionError("record_success should not be called")

        def record_failure(
            self,
            tool_name: str,
            session_id: str | None,
            skill_name: str | None = None,
        ) -> None:
            raise AssertionError("record_failure should not be called")

    class RecordingStructuredStore:
        def __init__(self) -> None:
            self.records: list[dict] = []

        async def record_tool_invocation(self, **kwargs) -> int:
            self.records.append(kwargs)
            return 99

    store = RecordingStructuredStore()
    manager = SkillManager(circuit_breaker=BlockedCircuitBreaker(), structured_store=store)
    manager.register(EchoSkill())

    output = asyncio.run(manager.invoke("echo", {"text": "hello"}, session_id="s1"))
    assert output.status == "error"
    assert "blocked" in output.error_info

    assert len(store.records) == 1
    record = store.records[0]
    assert record["status"] == "blocked"
    assert record["error_info"] == "blocked for test"
    assert record["skill_name"] == "echo"
    assert output.metadata["invocation_id"] == 99


def test_skill_manager_passes_logical_skill_name_to_circuit_breaker() -> None:
    class RecordingCircuitBreaker:
        def __init__(self) -> None:
            self.can_execute_calls: list[tuple[str, str | None, str | None]] = []
            self.success_calls: list[tuple[str, str | None, str | None]] = []

        def can_execute(
            self,
            tool_name: str,
            session_id: str | None,
            skill_name: str | None = None,
        ):
            self.can_execute_calls.append((tool_name, session_id, skill_name))
            return True, ""

        def record_success(
            self,
            tool_name: str,
            session_id: str | None,
            skill_name: str | None = None,
        ) -> None:
            self.success_calls.append((tool_name, session_id, skill_name))

        def record_failure(
            self,
            tool_name: str,
            session_id: str | None,
            skill_name: str | None = None,
        ) -> None:
            raise AssertionError("record_failure should not be called")

    breaker = RecordingCircuitBreaker()
    manager = SkillManager(circuit_breaker=breaker)
    manager.register(EchoSkill())

    output = asyncio.run(
        manager.invoke(
            "echo",
            {"text": "hello"},
            session_id="s1",
            skill_name="git-workflow",
        )
    )

    assert output.status == "success"
    assert breaker.can_execute_calls == [("echo", "s1", "git-workflow")]
    assert breaker.success_calls == [("echo", "s1", "git-workflow")]


def test_skill_manager_invokes_registered_builtin_tool() -> None:
    manager = SkillManager()

    async def handler(params: dict, *, session_id: str | None = None) -> SkillOutput:
        return SkillOutput(status="success", result={"params": params, "session_id": session_id})

    manager.register_builtin_tool(
        {
            "type": "function",
            "function": {
                "name": "update_persona_memory",
                "description": "Persist persona memory.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "value": {"type": "string"},
                    },
                    "required": ["key", "value"],
                },
            },
        },
        handler,
        source="builtin",
    )

    output = asyncio.run(
        manager.invoke(
            "update_persona_memory",
            {"key": "response_style", "value": "简洁"},
            session_id="s1",
        )
    )

    assert output.status == "success"
    assert output.result["session_id"] == "s1"
    assert output.result["params"]["key"] == "response_style"
