from __future__ import annotations

import asyncio
from typing import Any

from hypo_agent.skills.tmux_skill import TmuxSkill


class FakeProcess:
    def __init__(
        self,
        *,
        returncode: int = 0,
        stdout: bytes = b"",
        stderr: bytes = b"",
    ) -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr

    def kill(self) -> None:
        return None


def _build_fake_tmux_exec(
    *,
    capture_output: str = "server ready\n",
):
    state = {"session_exists": False, "windows": set(), "sent": []}

    async def fake_exec(*cmd: str, **kwargs: Any):  # noqa: ANN202
        del kwargs
        if cmd[:3] == ("tmux", "has-session", "-t"):
            return FakeProcess(returncode=0 if state["session_exists"] else 1)

        if cmd[:4] == ("tmux", "new-session", "-d", "-s"):
            state["session_exists"] = True
            window_name = "main"
            if "-n" in cmd:
                window_name = str(cmd[cmd.index("-n") + 1])
            state["windows"].add(window_name)
            return FakeProcess(returncode=0)

        if cmd[:3] == ("tmux", "list-windows", "-t"):
            stdout = "\n".join(sorted(state["windows"])).encode("utf-8")
            return FakeProcess(returncode=0, stdout=stdout)

        if cmd[:2] == ("tmux", "new-window"):
            if "-n" in cmd:
                state["windows"].add(str(cmd[cmd.index("-n") + 1]))
            return FakeProcess(returncode=0)

        if cmd[:2] == ("tmux", "send-keys"):
            state["sent"].append(cmd)
            return FakeProcess(returncode=0)

        if cmd[:2] == ("tmux", "capture-pane"):
            return FakeProcess(returncode=0, stdout=capture_output.encode("utf-8"))

        raise AssertionError(f"unexpected tmux command: {cmd}")

    fake_exec.state = state  # type: ignore[attr-defined]
    return fake_exec


def test_tmux_send_creates_session_and_sends_command() -> None:
    fake_exec = _build_fake_tmux_exec()
    skill = TmuxSkill(subprocess_exec=fake_exec)

    output = asyncio.run(
        skill.execute(
            "tmux_send",
            {
                "command": "tail -f /var/log/syslog",
                "session_name": "ops",
                "window_name": "logs",
            },
        )
    )

    assert output.status == "success"
    assert output.result == {
        "session_name": "ops",
        "window_name": "logs",
        "command": "tail -f /var/log/syslog",
    }
    assert fake_exec.state["session_exists"] is True  # type: ignore[attr-defined]
    assert "logs" in fake_exec.state["windows"]  # type: ignore[attr-defined]
    sent = fake_exec.state["sent"][0]  # type: ignore[attr-defined]
    assert sent[3] == "ops:logs"
    assert sent[4] == "tail -f /var/log/syslog"


def test_tmux_read_returns_recent_output() -> None:
    fake_exec = _build_fake_tmux_exec(capture_output="line1\nline2\n")
    fake_exec.state["session_exists"] = True  # type: ignore[attr-defined]
    fake_exec.state["windows"].add("main")  # type: ignore[attr-defined]
    skill = TmuxSkill(subprocess_exec=fake_exec)

    output = asyncio.run(
        skill.execute(
            "tmux_read",
            {
                "session_name": "ops",
                "window_name": "main",
                "lines": 50,
            },
        )
    )

    assert output.status == "success"
    assert output.result == {"output": "line1\nline2\n"}
    assert output.metadata["lines"] == 50


def test_tmux_read_errors_when_session_missing() -> None:
    skill = TmuxSkill(subprocess_exec=_build_fake_tmux_exec())

    output = asyncio.run(skill.execute("tmux_read", {"session_name": "missing"}))

    assert output.status == "error"
    assert "does not exist" in (output.error_info or "")


def test_tmux_send_rejects_dangerous_command() -> None:
    skill = TmuxSkill(subprocess_exec=_build_fake_tmux_exec())

    output = asyncio.run(skill.execute("tmux_send", {"command": "rm -rf /"}))

    assert output.status == "error"
    assert "dangerous" in (output.error_info or "").lower()
