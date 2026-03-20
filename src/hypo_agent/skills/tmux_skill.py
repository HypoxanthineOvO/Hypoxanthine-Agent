from __future__ import annotations

import asyncio
from asyncio.subprocess import PIPE
from pathlib import Path
import re
import shlex
from typing import Any
from uuid import uuid4

import structlog

from hypo_agent.models import SkillOutput
from hypo_agent.security.permission_manager import PermissionManager
from hypo_agent.skills.base import BaseSkill

logger = structlog.get_logger("hypo_agent.skills.tmux")


_INTERACTIVE_COMMANDS = {
    "top",
    "htop",
    "iotop",
    "iftop",
    "dstat",
    "watch",
    "less",
    "more",
    "man",
    "vim",
    "vi",
    "nano",
    "emacs",
}
_STREAMING_COMMANDS = {"yes"}
_FOLLOW_FLAGS = {"-f", "-F", "--follow", "--follow=name", "--follow=descriptor"}
_SEGMENT_SEPARATORS = {";", "&&", "||", "|"}
_SEGMENT_PREFIX_COMMANDS = {"sudo", "env", "command", "timeout", "nohup"}
_DANGEROUS_SEGMENT_COMMANDS = {"shutdown", "reboot", "poweroff", "halt"}
_SYSTEM_SNAPSHOT_NON_GPU_HINTS = (
    "free",
    "uptime",
    "ps ",
    "df ",
    "/proc/loadavg",
)
_SYSTEM_SNAPSHOT_OUTPUT_MARKERS = (
    "Mem:",
    "Filesystem",
    "load average",
    "USER ",
    " PID ",
    "KiB Mem",
)

class TmuxSkill(BaseSkill):
    name = "tmux"
    description = "Run shell commands inside a tmux session."
    required_permissions: list[str] = []

    def __init__(
        self,
        *,
        default_timeout_seconds: int = 30,
        max_output_chars: int = 262144,
        sandbox_dir: Path | str = "/tmp/hypo-agent-sandbox",
        permission_manager: PermissionManager | None = None,
        subprocess_exec=None,
    ) -> None:
        self.default_timeout_seconds = default_timeout_seconds
        self.max_output_chars = max_output_chars
        self.sandbox_dir = Path(sandbox_dir)
        self.sandbox_dir.mkdir(parents=True, exist_ok=True)
        self.permission_manager = permission_manager
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
        if any(token in _INTERACTIVE_COMMANDS for token in lower_tokens):
            return (
                "Command rejected: interactive commands are not allowed in run_command. "
                "Use a non-interactive snapshot such as `ps aux --sort=-%cpu | head -50`."
            )

        if any(token in _STREAMING_COMMANDS for token in lower_tokens):
            return (
                "Command rejected: streaming commands are not allowed in run_command. "
                "Use a bounded command that exits on its own."
            )

        if self._has_follow_mode(lower_tokens):
            return (
                "Command rejected: streaming commands are not allowed in run_command. "
                "Use bounded alternatives like `tail -n 200` or `journalctl -n 200`."
            )

        if "ping" in lower_tokens and "-c" not in lower_tokens:
            return (
                "Command rejected: streaming commands are not allowed in run_command. "
                "For ping, pass a bounded count such as `ping -c 4 host`."
            )

        if self._is_dangerous_command(command, lower_tokens):
            return (
                "Command rejected: dangerous commands are not allowed in run_command. "
                "Use read-only inspection commands instead."
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

    def _has_follow_mode(self, lower_tokens: list[str]) -> bool:
        for index, token in enumerate(lower_tokens):
            if token == "tail" and any(flag in _FOLLOW_FLAGS for flag in lower_tokens[index + 1:]):
                return True
            if token == "journalctl" and any(
                flag in {"-f", "--follow"} for flag in lower_tokens[index + 1:]
            ):
                return True
            if token == "docker":
                trailing = lower_tokens[index + 1:]
                if "logs" in trailing and any(flag in {"-f", "--follow"} for flag in trailing):
                    return True
            if token == "kubectl":
                trailing = lower_tokens[index + 1:]
                if "logs" in trailing and any(flag in {"-f", "--follow"} for flag in trailing):
                    return True
        return False

    def _build_wrapped_command(
        self,
        *,
        command: str,
        stdout_path: Path,
        stderr_path: Path,
        exit_path: Path,
        token: str,
    ) -> str:
        return (
            f"( {command} ) > {shlex.quote(str(stdout_path))} "
            f"2> {shlex.quote(str(stderr_path))}; "
            f"status=$?; "
            f"printf '%s' \"$status\" > {shlex.quote(str(exit_path))}; "
            f"tmux wait-for -S {token}"
        )

    def _looks_like_suspect_partial_capture(
        self,
        *,
        command: str,
        stdout: str,
    ) -> bool:
        lowered = command.lower()
        has_gpu_query = "nvidia-smi" in lowered
        has_snapshot_separator = any(separator in command for separator in ("&&", ";", "||"))
        has_non_gpu_snapshot = any(hint in lowered for hint in _SYSTEM_SNAPSHOT_NON_GPU_HINTS)
        if not (has_gpu_query and has_snapshot_separator and has_non_gpu_snapshot):
            return False

        if not stdout.strip():
            return False

        if any(marker in stdout for marker in _SYSTEM_SNAPSHOT_OUTPUT_MARKERS):
            return False

        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        if not lines:
            return False

        return all("%" in line and (("MiB" in line) or ("GiB" in line)) and "," in line for line in lines)

    def _log_if_suspect_partial_capture(self, *, command: str, stdout: str) -> bool:
        suspect_partial_capture = self._looks_like_suspect_partial_capture(
            command=command,
            stdout=stdout,
        )
        if suspect_partial_capture:
            logger.warning(
                "tmux.run_command.suspect_partial_capture",
                command=command,
                stdout_line_count=len([line for line in stdout.splitlines() if line.strip()]),
                stdout_preview=stdout[:500],
            )
        return suspect_partial_capture

    async def execute(self, tool_name: str, params: dict[str, Any]) -> SkillOutput:
        if tool_name != "run_command":
            return SkillOutput(
                status="error",
                error_info=f"Unsupported tool '{tool_name}' for tmux skill",
            )

        command = str(params.get("command", "")).strip()
        if not command:
            return SkillOutput(status="error", error_info="command is required")

        safety_error = self._validate_command_safety(command)
        if safety_error is not None:
            return SkillOutput(status="error", error_info=safety_error)

        allowed, reason = self._scan_command(command)
        if not allowed:
            return SkillOutput(
                status="error",
                error_info=f"Permission denied: {reason}",
            )

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

            wrapped_command = self._build_wrapped_command(
                command=command,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                exit_path=exit_path,
                token=token,
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

        raw_stdout = stdout_path.read_text(encoding="utf-8") if stdout_path.exists() else ""
        stderr = stderr_path.read_text(encoding="utf-8") if stderr_path.exists() else ""
        exit_code_text = exit_path.read_text(encoding="utf-8").strip() if exit_path.exists() else "1"
        try:
            exit_code = int(exit_code_text)
        except ValueError:
            exit_code = 1

        suspect_partial_capture = self._log_if_suspect_partial_capture(
            command=command,
            stdout=raw_stdout,
        )

        truncated = False
        stdout, stdout_truncated = self._truncate_output(raw_stdout)
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
                "suspect_partial_capture": suspect_partial_capture,
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
