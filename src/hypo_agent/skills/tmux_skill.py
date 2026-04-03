from __future__ import annotations

import asyncio
from asyncio.subprocess import PIPE
from pathlib import Path
import re
import shlex
from typing import Any

import structlog

from hypo_agent.models import SkillOutput
from hypo_agent.security.permission_manager import PermissionManager
from hypo_agent.skills.base import BaseSkill, DEFAULT_MAX_OUTPUT_CHARS

logger = structlog.get_logger("hypo_agent.skills.tmux")

_SEGMENT_SEPARATORS = {";", "&&", "||", "|"}
_SEGMENT_PREFIX_COMMANDS = {"sudo", "env", "command", "timeout", "nohup"}
_DANGEROUS_SEGMENT_COMMANDS = {"shutdown", "reboot", "poweroff", "halt"}


class TmuxSkill(BaseSkill):
    name = "tmux"
    description = (
        "Manage persistent tmux sessions. Use tmux_send to send commands into an existing tmux "
        "window and tmux_read to capture recent pane output. "
        "For normal one-shot command execution, use exec_command instead."
    )
    required_permissions: list[str] = []

    def __init__(
        self,
        *,
        default_timeout_seconds: int = 30,
        max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
        permission_manager: PermissionManager | None = None,
        subprocess_exec=None,
    ) -> None:
        self.default_timeout_seconds = max(1, int(default_timeout_seconds))
        self.max_output_chars = max_output_chars
        self.permission_manager = permission_manager
        self._subprocess_exec = subprocess_exec or asyncio.create_subprocess_exec

    @property
    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "tmux_send",
                    "description": (
                        "Send a command to a persistent tmux window. "
                        "Use this only when you need a long-lived shell session or background process. "
                        "Use exec_command for normal one-shot commands."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {"type": "string"},
                            "session_name": {"type": "string"},
                            "window_name": {"type": "string"},
                            "create_session": {"type": "boolean", "default": True},
                        },
                        "required": ["command"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "tmux_read",
                    "description": "Capture recent output from a persistent tmux window.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "session_name": {"type": "string"},
                            "window_name": {"type": "string"},
                            "lines": {"type": "integer", "minimum": 1, "default": 200},
                        },
                    },
                },
            },
        ]

    async def execute(self, tool_name: str, params: dict[str, Any]) -> SkillOutput:
        if tool_name == "tmux_send":
            return await self._tmux_send(params)
        if tool_name == "tmux_read":
            return await self._tmux_read(params)
        return SkillOutput(
            status="error",
            error_info=f"Unsupported tool '{tool_name}' for tmux skill",
        )

    async def _tmux_send(self, params: dict[str, Any]) -> SkillOutput:
        command = str(params.get("command", "")).strip()
        if not command:
            return SkillOutput(status="error", error_info="command is required")

        safety_error = self._validate_command_safety(command)
        if safety_error is not None:
            return SkillOutput(status="error", error_info=safety_error)

        allowed, reason = self._scan_command(command)
        if not allowed:
            return SkillOutput(status="error", error_info=f"Permission denied: {reason}")

        session_name = str(params.get("session_name") or "hypo-agent").strip() or "hypo-agent"
        window_name = str(params.get("window_name") or "main").strip() or "main"
        create_session = bool(params.get("create_session", True))

        ensured, error = await self._ensure_session_window(
            session_name=session_name,
            window_name=window_name,
            create_session=create_session,
        )
        if not ensured:
            return SkillOutput(status="error", error_info=error or "Failed to prepare tmux session")

        result = await self._run_process(
            [
                "tmux",
                "send-keys",
                "-t",
                self._window_target(session_name, window_name),
                command,
                "C-m",
            ],
            timeout=self.default_timeout_seconds,
        )
        if result["returncode"] != 0:
            return SkillOutput(
                status="error",
                error_info=result["stderr"] or "Failed to send command to tmux",
            )

        return SkillOutput(
            status="success",
            result={
                "session_name": session_name,
                "window_name": window_name,
                "command": command,
            },
            metadata={
                "persistent_session": True,
                "timeout_seconds": self.default_timeout_seconds,
            },
        )

    async def _tmux_read(self, params: dict[str, Any]) -> SkillOutput:
        session_name = str(params.get("session_name") or "hypo-agent").strip() or "hypo-agent"
        window_name = str(params.get("window_name") or "main").strip() or "main"
        lines = max(1, int(params.get("lines") or 200))

        exists = await self._run_process(
            ["tmux", "has-session", "-t", session_name],
            timeout=self.default_timeout_seconds,
        )
        if exists["returncode"] != 0:
            return SkillOutput(
                status="error",
                error_info=f"tmux session '{session_name}' does not exist",
            )

        output = await self._run_process(
            [
                "tmux",
                "capture-pane",
                "-p",
                "-S",
                f"-{lines}",
                "-t",
                self._window_target(session_name, window_name),
            ],
            timeout=self.default_timeout_seconds,
        )
        if output["returncode"] != 0:
            return SkillOutput(
                status="error",
                error_info=output["stderr"] or "Failed to read tmux output",
            )

        content, truncated = self._truncate_output(output["stdout"])
        return SkillOutput(
            status="success",
            result={"output": content},
            metadata={
                "session_name": session_name,
                "window_name": window_name,
                "lines": lines,
                "truncated": truncated,
            },
        )

    async def _ensure_session_window(
        self,
        *,
        session_name: str,
        window_name: str,
        create_session: bool,
    ) -> tuple[bool, str | None]:
        has_session = await self._run_process(
            ["tmux", "has-session", "-t", session_name],
            timeout=self.default_timeout_seconds,
        )
        if has_session["returncode"] != 0:
            if not create_session:
                return False, f"tmux session '{session_name}' does not exist"
            created = await self._run_process(
                ["tmux", "new-session", "-d", "-s", session_name, "-n", window_name],
                timeout=self.default_timeout_seconds,
            )
            if created["returncode"] != 0:
                return False, created["stderr"] or "Failed to create tmux session"
            return True, None

        window_exists = await self._run_process(
            ["tmux", "list-windows", "-t", session_name, "-F", "#{window_name}"],
            timeout=self.default_timeout_seconds,
        )
        if window_exists["returncode"] != 0:
            return False, window_exists["stderr"] or "Failed to inspect tmux windows"
        existing_windows = {line.strip() for line in window_exists["stdout"].splitlines() if line.strip()}
        if window_name in existing_windows:
            return True, None

        created_window = await self._run_process(
            ["tmux", "new-window", "-d", "-t", session_name, "-n", window_name],
            timeout=self.default_timeout_seconds,
        )
        if created_window["returncode"] != 0:
            return False, created_window["stderr"] or "Failed to create tmux window"
        return True, None

    def _looks_like_path(self, token: str) -> bool:
        return token.startswith("/") or token.startswith("~") or token.startswith("./") or token.startswith("../")

    def _normalize_path_token(self, token: str) -> str:
        return token.strip(";,")

    def _scan_command(self, command: str) -> tuple[bool, str]:
        if self.permission_manager is None:
            return True, ""
        try:
            tokens = shlex.split(command)
        except ValueError:
            tokens = command.split()
        path_tokens = [self._normalize_path_token(t) for t in tokens if self._looks_like_path(t)]
        if not path_tokens:
            return True, ""
        for token in path_tokens:
            allowed, reason = self.permission_manager.check_permission(
                token,
                "read",
                log_allowed=False,
            )
            if not allowed and "blocked" in reason.lower():
                return False, f"{token} is in blocked_paths"
        return True, ""

    def _validate_command_safety(self, command: str) -> str | None:
        try:
            tokens = shlex.split(command)
        except ValueError:
            tokens = command.split()
        lower_tokens = [token.lower() for token in tokens]
        if self._is_dangerous_command(command, lower_tokens):
            return (
                "Command rejected: dangerous commands are not allowed in tmux_send. "
                "Use read-only inspection commands or an explicit deployment workflow instead."
            )
        return None

    def _is_dangerous_command(self, command: str, lower_tokens: list[str]) -> bool:
        for segment in self._split_command_segments(lower_tokens):
            leader = self._segment_leader(segment)
            if not leader:
                continue
            if leader in _DANGEROUS_SEGMENT_COMMANDS:
                return True
            if leader == "mkfs" or leader.startswith("mkfs."):
                return True
            if leader == "dd" and any(token.startswith("if=") for token in segment[1:]):
                return True
            if leader == "rm" and self._is_rm_rf_root(segment[1:]):
                return True

        lowered_raw = command.lower()
        if re.search(r"\brm\s+-[A-Za-z]*[rf][A-Za-z]*\s+/\s*($|[;|&])", lowered_raw):
            return True
        if re.search(r"\bdd\s+[^;&|]*\bif=", lowered_raw):
            return True
        if re.search(r"\bmkfs(?:\.[A-Za-z0-9_+-]+)?\b", lowered_raw):
            return True
        if re.search(r"\b(shutdown|reboot|poweroff|halt)\b", lowered_raw):
            return True
        return False

    def _split_command_segments(self, lower_tokens: list[str]) -> list[list[str]]:
        segments: list[list[str]] = []
        current: list[str] = []
        for token in lower_tokens:
            if token in _SEGMENT_SEPARATORS:
                if current:
                    segments.append(current)
                    current = []
                continue
            current.append(token)
        if current:
            segments.append(current)
        return segments

    def _segment_leader(self, segment: list[str]) -> str:
        index = 0
        while index < len(segment):
            token = segment[index]
            if token in _SEGMENT_PREFIX_COMMANDS:
                index += 1
                continue
            if "=" in token and not token.startswith(("/", "./", "../", "~")):
                index += 1
                continue
            return token
        return ""

    def _is_rm_rf_root(self, tokens: list[str]) -> bool:
        flags = [token for token in tokens if token.startswith("-")]
        has_recursive = any("r" in token for token in flags)
        has_force = any("f" in token for token in flags)
        if not (has_recursive and has_force):
            return False
        targets = [self._normalize_path_token(token) for token in tokens if not token.startswith("-")]
        return any(target in {"/", "/*"} for target in targets)

    async def _run_process(self, cmd: list[str], *, timeout: int) -> dict[str, Any]:
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
            "returncode": int(process.returncode or 0),
            "stdout": stdout_bytes.decode("utf-8", errors="replace"),
            "stderr": stderr_bytes.decode("utf-8", errors="replace"),
        }

    def _truncate_output(self, text: str) -> tuple[str, bool]:
        if len(text) <= self.max_output_chars:
            return text, False
        suffix = f"\n[truncated to {self.max_output_chars} chars]"
        return text[: self.max_output_chars] + suffix, True

    def _window_target(self, session_name: str, window_name: str) -> str:
        return f"{session_name}:{window_name}"
