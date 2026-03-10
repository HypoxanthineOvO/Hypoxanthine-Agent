from __future__ import annotations

import asyncio
from asyncio.subprocess import PIPE
from pathlib import Path
import shlex
from typing import Any
from uuid import uuid4

from hypo_agent.models import SkillOutput
from hypo_agent.skills.base import BaseSkill


class TmuxSkill(BaseSkill):
    name = "tmux"
    description = "Run shell commands inside a tmux session."
    required_permissions: list[str] = []

    def __init__(
        self,
        *,
        default_timeout_seconds: int = 30,
        max_output_chars: int = 8000,
        sandbox_dir: Path | str = "/tmp/hypo-agent-sandbox",
        subprocess_exec=None,
    ) -> None:
        self.default_timeout_seconds = default_timeout_seconds
        self.max_output_chars = max_output_chars
        self.sandbox_dir = Path(sandbox_dir)
        self.sandbox_dir.mkdir(parents=True, exist_ok=True)
        self._subprocess_exec = subprocess_exec or asyncio.create_subprocess_exec

    @property
    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "run_command",
                    "description": "Run a shell command in a tmux session",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {"type": "string"},
                            "session_name": {"type": "string"},
                            "timeout": {"type": "integer", "minimum": 1},
                        },
                        "required": ["command"],
                    },
                },
            }
        ]

    async def execute(self, tool_name: str, params: dict[str, Any]) -> SkillOutput:
        if tool_name != "run_command":
            return SkillOutput(
                status="error",
                error_info=f"Unsupported tool '{tool_name}' for tmux skill",
            )

        command = str(params.get("command", "")).strip()
        if not command:
            return SkillOutput(status="error", error_info="command is required")

        session_name = str(params.get("session_name") or "hypo-agent")
        timeout = int(params.get("timeout") or self.default_timeout_seconds)
        timeout = max(1, timeout)

        token = uuid4().hex
        stdout_path = self.sandbox_dir / f"{token}.stdout"
        stderr_path = self.sandbox_dir / f"{token}.stderr"
        exit_path = self.sandbox_dir / f"{token}.exit"
        window_id = ""

        try:
            has_session = await self._run_process(
                ["tmux", "has-session", "-t", session_name],
                timeout=max(5, timeout),
            )
            if has_session["returncode"] != 0:
                create_session = await self._run_process(
                    ["tmux", "new-session", "-d", "-s", session_name],
                    timeout=max(5, timeout),
                )
                if create_session["returncode"] != 0:
                    return SkillOutput(
                        status="error",
                        error_info=create_session["stderr"] or "Failed to create tmux session",
                    )

            wrapped_command = (
                f"{command} > {shlex.quote(str(stdout_path))} "
                f"2> {shlex.quote(str(stderr_path))}; "
                f"printf '%s' $? > {shlex.quote(str(exit_path))}; "
                f"tmux wait-for -S {token}"
            )
            new_window = await self._run_process(
                [
                    "tmux",
                    "new-window",
                    "-d",
                    "-P",
                    "-F",
                    "#{window_id}",
                    "-t",
                    session_name,
                    f"bash -lc {shlex.quote(wrapped_command)}",
                ],
                timeout=max(5, timeout),
            )
            if new_window["returncode"] != 0:
                return SkillOutput(
                    status="error",
                    error_info=new_window["stderr"] or "Failed to start tmux window",
                )
            window_id = new_window["stdout"].strip()

            await self._run_process(
                ["tmux", "wait-for", token],
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            if window_id:
                await self._run_process(
                    ["tmux", "kill-window", "-t", window_id],
                    timeout=5,
                )
            return SkillOutput(
                status="timeout",
                error_info=f"Command timed out after {timeout} seconds",
                metadata={"timeout_seconds": timeout, "session_name": session_name},
            )

        if window_id:
            await self._run_process(["tmux", "kill-window", "-t", window_id], timeout=5)

        stdout = stdout_path.read_text(encoding="utf-8") if stdout_path.exists() else ""
        stderr = stderr_path.read_text(encoding="utf-8") if stderr_path.exists() else ""
        exit_code_text = exit_path.read_text(encoding="utf-8").strip() if exit_path.exists() else "1"
        try:
            exit_code = int(exit_code_text)
        except ValueError:
            exit_code = 1

        truncated = False
        stdout, stdout_truncated = self._truncate_output(stdout)
        stderr, stderr_truncated = self._truncate_output(stderr)
        truncated = stdout_truncated or stderr_truncated

        self._safe_unlink(stdout_path)
        self._safe_unlink(stderr_path)
        self._safe_unlink(exit_path)

        status = "success" if exit_code == 0 else "error"
        return SkillOutput(
            status=status,
            result={
                "stdout": stdout,
                "stderr": stderr,
                "exit_code": exit_code,
            },
            metadata={
                "session_name": session_name,
                "timeout_seconds": timeout,
                "truncated": truncated,
            },
            error_info="" if exit_code == 0 else f"Command exited with status {exit_code}",
        )

    async def _run_process(
        self,
        cmd: list[str],
        *,
        timeout: int,
    ) -> dict[str, Any]:
        process = await self._subprocess_exec(*cmd, stdout=PIPE, stderr=PIPE)
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            raise

        return {
            "returncode": process.returncode,
            "stdout": stdout_bytes.decode("utf-8", errors="replace"),
            "stderr": stderr_bytes.decode("utf-8", errors="replace"),
        }

    def _truncate_output(self, text: str) -> tuple[str, bool]:
        if len(text) <= self.max_output_chars:
            return text, False

        suffix = f"\n[truncated to {self.max_output_chars} chars]"
        truncated_text = text[: self.max_output_chars] + suffix
        return truncated_text, True

    def _safe_unlink(self, path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

