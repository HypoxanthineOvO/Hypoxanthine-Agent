from __future__ import annotations

import asyncio
import re

from hypo_agent.skills.tmux_skill import TmuxSkill


class FakeProcess:
    def __init__(
        self,
        *,
        returncode: int = 0,
        stdout: bytes = b"",
        stderr: bytes = b"",
        delay_seconds: float = 0.0,
    ) -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self._delay_seconds = delay_seconds
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        if self._delay_seconds:
            await asyncio.sleep(self._delay_seconds)
        return self._stdout, self._stderr

    def kill(self) -> None:
        self.killed = True


def _build_fake_tmux_exec(
    *,
    command_stdout: str = "ok\\n",
    command_stderr: str = "",
    exit_code: str = "0",
    wait_for_delay: float = 0.0,
):
    state: dict[str, str] = {}

    async def fake_exec(*cmd, **kwargs):  # noqa: ANN003, ANN202
        del kwargs
        if cmd[:3] == ("tmux", "has-session", "-t"):
            return FakeProcess(returncode=1, stderr=b"no such session")

        if cmd[:3] == ("tmux", "new-session", "-d"):
            return FakeProcess(returncode=0)

        if len(cmd) >= 2 and cmd[0] == "tmux" and cmd[1] == "new-window":
            script = cmd[-1]
            matches = re.findall(r"(/tmp/hypo-agent-sandbox/[^\s';]+)", script)
            if len(matches) >= 3:
                state["stdout_path"] = matches[0]
                state["stderr_path"] = matches[1]
                state["exit_path"] = matches[2]
            return FakeProcess(returncode=0, stdout=b"@12\n")

        if len(cmd) >= 2 and cmd[0] == "tmux" and cmd[1] == "wait-for":
            if wait_for_delay:
                return FakeProcess(returncode=0, delay_seconds=wait_for_delay)

            with open(state["stdout_path"], "w", encoding="utf-8") as f:
                f.write(command_stdout)
            with open(state["stderr_path"], "w", encoding="utf-8") as f:
                f.write(command_stderr)
            with open(state["exit_path"], "w", encoding="utf-8") as f:
                f.write(exit_code)
            return FakeProcess(returncode=0)

        if len(cmd) >= 2 and cmd[0] == "tmux" and cmd[1] == "kill-window":
            return FakeProcess(returncode=0)

        return FakeProcess(returncode=0)

    return fake_exec


def test_tmux_skill_run_command_returns_stdout_and_stderr() -> None:
    skill = TmuxSkill(subprocess_exec=_build_fake_tmux_exec(command_stdout="hi\\n"))
    output = asyncio.run(
        skill.execute(
            "run_command",
            {
                "command": "echo hi",
                "session_name": "s1",
            },
        )
    )

    assert output.status == "success"
    assert output.result["stdout"] == "hi\\n"
    assert output.result["stderr"] == ""
    assert output.result["exit_code"] == 0


def test_tmux_skill_times_out_when_wait_exceeds_timeout() -> None:
    skill = TmuxSkill(
        default_timeout_seconds=1,
        subprocess_exec=_build_fake_tmux_exec(wait_for_delay=2.0),
    )
    output = asyncio.run(
        skill.execute(
            "run_command",
            {
                "command": "sleep 5",
            },
        )
    )

    assert output.status == "timeout"


def test_tmux_skill_truncates_long_output() -> None:
    long_text = "a" * 9005
    skill = TmuxSkill(subprocess_exec=_build_fake_tmux_exec(command_stdout=long_text))
    output = asyncio.run(skill.execute("run_command", {"command": "python -c 'print(1)'"}))

    assert output.status == "success"
    assert output.metadata["truncated"] is True
    assert len(output.result["stdout"]) <= 8050
    assert "[truncated" in output.result["stdout"]
