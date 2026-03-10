from __future__ import annotations

import asyncio
from pathlib import Path

from hypo_agent.models import SkillOutput
from hypo_agent.skills.code_run_skill import CodeRunSkill


class StubTmuxSkill:
    def __init__(self, response: SkillOutput | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.response = response or SkillOutput(
            status="success",
            result={"stdout": "ok\n", "stderr": "", "exit_code": 0},
        )

    async def execute(self, tool_name: str, params: dict) -> SkillOutput:
        self.calls.append((tool_name, params))
        return self.response


def test_code_run_skill_executes_python_via_tmux(tmp_path: Path) -> None:
    tmux = StubTmuxSkill()
    skill = CodeRunSkill(tmux_skill=tmux, sandbox_dir=tmp_path)

    output = asyncio.run(
        skill.execute(
            "run_code",
            {
                "code": "print('ok')",
                "language": "python",
            },
        )
    )

    assert output.status == "success"
    assert tmux.calls
    tool_name, params = tmux.calls[0]
    assert tool_name == "run_command"
    assert params["command"].startswith("python ")
    assert str(tmp_path) in params["command"]


def test_code_run_skill_executes_shell_via_tmux(tmp_path: Path) -> None:
    tmux = StubTmuxSkill()
    skill = CodeRunSkill(tmux_skill=tmux, sandbox_dir=tmp_path)

    output = asyncio.run(
        skill.execute(
            "run_code",
            {
                "code": "echo ok",
                "language": "shell",
            },
        )
    )

    assert output.status == "success"
    _, params = tmux.calls[0]
    assert params["command"].startswith("bash ")


def test_code_run_skill_rejects_unsupported_language(tmp_path: Path) -> None:
    tmux = StubTmuxSkill()
    skill = CodeRunSkill(tmux_skill=tmux, sandbox_dir=tmp_path)

    output = asyncio.run(
        skill.execute(
            "run_code",
            {
                "code": "console.log('x')",
                "language": "javascript",
            },
        )
    )

    assert output.status == "error"
    assert "Unsupported language" in output.error_info
    assert tmux.calls == []


def test_code_run_skill_propagates_tmux_timeout(tmp_path: Path) -> None:
    tmux = StubTmuxSkill(response=SkillOutput(status="timeout", error_info="timeout"))
    skill = CodeRunSkill(tmux_skill=tmux, sandbox_dir=tmp_path)

    output = asyncio.run(skill.execute("run_code", {"code": "print('ok')"}))
    assert output.status == "timeout"

