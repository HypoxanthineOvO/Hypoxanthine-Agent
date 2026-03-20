from __future__ import annotations

import asyncio
import re
import shlex
from pathlib import Path
from typing import Any

import hypo_agent.skills.tmux_skill as tmux_skill_module
from hypo_agent.models import DirectoryWhitelist
from hypo_agent.security.permission_manager import PermissionManager
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


class RecordingLogger:
    def __init__(self) -> None:
        self.warning_calls: list[tuple[str, dict[str, Any]]] = []

    def warning(self, event: str, **kwargs: Any) -> None:
        self.warning_calls.append((event, kwargs))


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


def _build_shell_running_tmux_exec():
    async def fake_exec(*cmd, **kwargs):  # noqa: ANN003, ANN202
        del kwargs
        if cmd[:3] == ("tmux", "has-session", "-t"):
            return FakeProcess(returncode=1, stderr=b"no such session")

        if cmd[:3] == ("tmux", "new-session", "-d"):
            return FakeProcess(returncode=0)

        if len(cmd) >= 2 and cmd[0] == "tmux" and cmd[1] == "new-window":
            script = str(cmd[-1])
            wrapped_command = shlex.split(script)[2]
            wrapped_command = re.sub(r"tmux wait-for -S [A-Za-z0-9]+", ":", wrapped_command)
            process = await asyncio.create_subprocess_exec(
                "bash",
                "-lc",
                wrapped_command,
            )
            await process.communicate()
            return FakeProcess(returncode=0, stdout=b"@12\n")

        if len(cmd) >= 2 and cmd[0] == "tmux" and cmd[1] == "wait-for":
            return FakeProcess(returncode=0)

        if len(cmd) >= 2 and cmd[0] == "tmux" and cmd[1] == "kill-window":
            return FakeProcess(returncode=0)

        return FakeProcess(returncode=0)

    return fake_exec


async def _unexpected_exec(*cmd, **kwargs):  # noqa: ANN003, ANN202
    del kwargs
    raise AssertionError(f"unexpected subprocess execution: {cmd}")


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


def test_tmux_skill_captures_all_stdout_from_compound_shell_command(tmp_path: Path) -> None:
    skill = TmuxSkill(
        sandbox_dir=tmp_path,
        subprocess_exec=_build_shell_running_tmux_exec(),
    )

    output = asyncio.run(
        skill.execute(
            "run_command",
            {
                "command": "printf 'FREE\\n'; printf 'UPTIME\\n'; printf 'GPU\\n'",
                "session_name": "s1",
            },
        )
    )

    assert output.status == "success"
    assert output.result["stdout"] == "FREE\nUPTIME\nGPU\n"


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


def test_tmux_skill_rejects_interactive_commands_before_execution() -> None:
    skill = TmuxSkill(subprocess_exec=_unexpected_exec)

    output = asyncio.run(
        skill.execute(
            "run_command",
            {
                "command": "top",
            },
        )
    )

    assert output.status == "error"
    assert "interactive" in (output.error_info or "").lower()


def test_tmux_skill_rejects_streaming_commands_before_execution() -> None:
    skill = TmuxSkill(subprocess_exec=_unexpected_exec)

    output = asyncio.run(
        skill.execute(
            "run_command",
            {
                "command": "tail -f /var/log/system.log",
            },
        )
    )

    assert output.status == "error"
    assert "streaming" in (output.error_info or "").lower()


def test_tmux_skill_truncates_long_output() -> None:
    long_text = "a" * 270000
    skill = TmuxSkill(subprocess_exec=_build_fake_tmux_exec(command_stdout=long_text))
    output = asyncio.run(skill.execute("run_command", {"command": "python -c 'print(1)'"}))

    assert output.status == "success"
    assert output.metadata["truncated"] is True
    assert len(output.result["stdout"]) <= 262200
    assert "[truncated" in output.result["stdout"]


def test_tmux_skill_default_max_output_chars_is_256k() -> None:
    skill = TmuxSkill(subprocess_exec=_build_fake_tmux_exec(command_stdout="ok"))
    assert skill.max_output_chars == 262144


def test_tmux_blocks_blocked_path(tmp_path: Path) -> None:
    manager = PermissionManager(
        DirectoryWhitelist(rules=[], default_policy="readonly", blocked_paths=["/etc/passwd"])
    )
    skill = TmuxSkill(
        permission_manager=manager,
        subprocess_exec=_build_fake_tmux_exec(command_stdout="ok"),
    )

    output = asyncio.run(skill.execute("run_command", {"command": "cat /etc/passwd"}))

    assert output.status == "error"
    assert "permission denied" in (output.error_info or "").lower()


def test_tmux_safe_command_no_scan(tmp_path: Path) -> None:
    manager = PermissionManager(
        DirectoryWhitelist(rules=[], default_policy="readonly", blocked_paths=["/etc/passwd"])
    )
    skill = TmuxSkill(
        permission_manager=manager,
        subprocess_exec=_build_fake_tmux_exec(command_stdout="ok"),
    )

    output = asyncio.run(skill.execute("run_command", {"command": "ps aux"}))

    assert output.status == "success"


def test_tmux_allows_system_commands_with_absolute_paths_outside_whitelist() -> None:
    manager = PermissionManager(
        DirectoryWhitelist(rules=[], default_policy="readonly", blocked_paths=["/etc/passwd"])
    )
    skill = TmuxSkill(
        permission_manager=manager,
        subprocess_exec=_build_fake_tmux_exec(command_stdout="Filesystem\n"),
    )

    output = asyncio.run(skill.execute("run_command", {"command": "df -h /"}))

    assert output.status == "success"


def test_tmux_allows_non_blocked_procfs_reads_outside_whitelist() -> None:
    manager = PermissionManager(
        DirectoryWhitelist(rules=[], default_policy="readonly", blocked_paths=["/etc/passwd"])
    )
    skill = TmuxSkill(
        permission_manager=manager,
        subprocess_exec=_build_fake_tmux_exec(command_stdout="0.10 0.20 0.30 1/123 456\n"),
    )

    output = asyncio.run(skill.execute("run_command", {"command": "cat /proc/loadavg"}))

    assert output.status == "success"


def test_tmux_logs_warning_for_gpu_only_output_in_system_snapshot_command(monkeypatch) -> None:
    logger = RecordingLogger()
    monkeypatch.setattr(tmux_skill_module, "logger", logger)
    skill = TmuxSkill(
        subprocess_exec=_build_fake_tmux_exec(
            command_stdout=(
                "0 %, 18 MiB, 32607 MiB, 28\n"
                "98 %, 25251 MiB, 32607 MiB, 59\n"
            )
        )
    )

    output = asyncio.run(
        skill.execute(
            "run_command",
            {
                "command": (
                    "free -h && uptime && ps aux --sort=-%cpu | head -5 && "
                    "df -h / && "
                    "nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu "
                    "--format=csv,noheader"
                )
            },
        )
    )

    assert output.status == "success"
    assert logger.warning_calls[-1][0] == "tmux.run_command.suspect_partial_capture"


def test_tmux_rejects_dangerous_rm_rf_root_command() -> None:
    skill = TmuxSkill(subprocess_exec=_unexpected_exec)

    output = asyncio.run(skill.execute("run_command", {"command": "rm -rf /"}))

    assert output.status == "error"
    assert "dangerous" in (output.error_info or "").lower()


def test_tmux_rejects_shutdown_and_dd_commands() -> None:
    skill = TmuxSkill(subprocess_exec=_unexpected_exec)

    shutdown_output = asyncio.run(skill.execute("run_command", {"command": "shutdown now"}))
    dd_output = asyncio.run(
        skill.execute("run_command", {"command": "dd if=/dev/zero of=/tmp/x bs=1M count=1"})
    )

    assert shutdown_output.status == "error"
    assert "dangerous" in (shutdown_output.error_info or "").lower()
    assert dd_output.status == "error"
    assert "dangerous" in (dd_output.error_info or "").lower()
