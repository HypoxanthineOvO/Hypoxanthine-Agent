from __future__ import annotations

import asyncio
from asyncio.subprocess import PIPE
import os
from pathlib import Path
import re
import shlex
import shutil
import sys
import tempfile
from typing import Any

import structlog

from hypo_agent.core.config_loader import get_agent_root
from hypo_agent.core.exec_profiles import ExecProfileRegistry
from hypo_agent.models import SkillOutput
from hypo_agent.skills.base import BaseSkill, DEFAULT_MAX_OUTPUT_CHARS

logger = structlog.get_logger("hypo_agent.skills.exec")
_TERMINATE_GRACE_SECONDS = 3


class ExecSkill(BaseSkill):
    name = "exec"
    description = (
        "Run one-shot shell commands or scripts in isolated subprocesses. "
        "Use exec_command for normal command execution. This is not a persistent terminal. "
        "Interactive commands will time out unless made non-interactive (for example `apt install -y`). "
        "If you need a persistent terminal session, use tmux_send and tmux_read instead."
    )
    required_permissions: list[str] = []

    def __init__(
        self,
        *,
        sandbox_dir: Path | str = "/tmp/hypo-agent-sandbox",
        default_timeout_seconds: int = 60,
        max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
        profiles_path: Path | str | None = None,
        exec_profiles_path: Path | str | None = None,
    ) -> None:
        self.sandbox_dir = Path(sandbox_dir).expanduser().resolve(strict=False)
        self.sandbox_dir.mkdir(parents=True, exist_ok=True)
        self.default_timeout_seconds = max(1, int(default_timeout_seconds))
        self.max_output_chars = max_output_chars
        self.default_workdir = self._resolve_default_workdir()
        effective_profiles_path = exec_profiles_path if exec_profiles_path is not None else profiles_path
        self.profile_registry = ExecProfileRegistry.from_yaml(effective_profiles_path)

    @property
    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "exec_command",
                    "description": (
                        "Run a one-shot shell command in a fresh subprocess and return stdout/stderr/exit_code. "
                        "This is not a persistent terminal session. "
                        "Interactive commands will time out unless you make them non-interactive "
                        "(for example `apt install -y` or `yes | ...`). "
                        "If you need a persistent terminal session or long-running service, use tmux_send."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {"type": "string"},
                            "timeout": {"type": "integer", "minimum": 1, "default": 60},
                            "workdir": {"type": "string"},
                        },
                        "required": ["command"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "exec_script",
                    "description": (
                        "Write code to a temporary file, execute it once, then delete the file. "
                        "Supports bash, python, and other languages when an interpreter binary is available."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "string"},
                            "language": {"type": "string", "default": "bash"},
                            "timeout": {"type": "integer", "minimum": 1, "default": 60},
                        },
                        "required": ["code"],
                    },
                },
            },
        ]

    async def execute(self, tool_name: str, params: dict[str, Any]) -> SkillOutput:
        if tool_name == "exec_command":
            return await self._exec_command(params)
        if tool_name == "exec_script":
            return await self._exec_script(params)
        return SkillOutput(
            status="error",
            error_info=f"Unsupported tool '{tool_name}' for exec skill",
        )

    async def _exec_command(self, params: dict[str, Any]) -> SkillOutput:
        command = str(params.get("command", "")).strip()
        if not command:
            return SkillOutput(status="error", error_info="command is required")

        timeout = self._coerce_timeout(params.get("timeout"))
        workdir = self._resolve_workdir(params.get("workdir"))
        if workdir is None:
            return SkillOutput(status="error", error_info="workdir does not exist")
        profile_name = str(params.get("exec_profile") or "default").strip() or "default"
        decision = self.profile_registry.evaluate(command, profile_name=profile_name)
        if not decision.allowed:
            error_prefix = (
                "Command denied by exec profile"
                if "deny prefix" in decision.reason
                else "Command not allowed by exec profile"
            )
            logger.warning(
                "exec.command.denied",
                tool_name="exec_command",
                exec_profile=decision.profile_name,
                command=decision.normalized_command,
                decision="denied",
                deny_reason=decision.reason,
            )
            return SkillOutput(
                status="error",
                error_info=(
                    f"{error_prefix} '{decision.profile_name}': "
                    f"{decision.reason} ({decision.normalized_command[:200]})"
                ),
            )
        cli_error = self._validate_cli_json_command(command, params, profile_name=decision.profile_name)
        if cli_error is not None:
            logger.warning(
                "exec.command.cli_json_denied",
                tool_name="exec_command",
                exec_profile=decision.profile_name,
                command=decision.normalized_command,
                decision="denied",
                deny_reason=cli_error,
            )
            return SkillOutput(status="error", error_info=cli_error)
        logger.info(
            "exec.command.allowed",
            tool_name="exec_command",
            exec_profile=decision.profile_name,
            command=decision.normalized_command,
            decision="allowed",
            deny_reason="",
        )

        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=PIPE,
                stderr=PIPE,
                cwd=str(workdir),
                env=os.environ.copy(),
            )
            result = await self._collect_process_result(process=process, timeout=timeout)
        finally:
            await self._ensure_process_reaped(process)
        return self._to_skill_output(
            result=result,
            metadata={
                "timeout_seconds": timeout,
                "workdir": str(workdir),
                "exec_profile": decision.profile_name,
            },
        )

    async def _exec_script(self, params: dict[str, Any]) -> SkillOutput:
        code = str(params.get("code", ""))
        if not code.strip():
            return SkillOutput(status="error", error_info="code is required")

        language = str(params.get("language") or "bash").strip().lower()
        timeout = self._coerce_timeout(params.get("timeout"))
        interpreter, suffix = self._resolve_interpreter(language)
        if interpreter is None:
            return SkillOutput(
                status="error",
                error_info=f"Unsupported language '{language}'",
            )
        profile_name = str(params.get("exec_profile") or "default").strip() or "default"
        if profile_name == "cli-json":
            return SkillOutput(
                status="error",
                error_info="Script execution is not allowed with exec profile 'cli-json'; use exec_command.",
            )
        validation_command = f"{Path(interpreter).name} <tempfile{suffix}>"
        decision = self.profile_registry.evaluate(validation_command, profile_name=profile_name)
        if not decision.allowed:
            error_prefix = (
                "Script execution denied by exec profile"
                if "deny prefix" in decision.reason
                else "Script execution not allowed by exec profile"
            )
            logger.warning(
                "exec.script.denied",
                tool_name="exec_script",
                exec_profile=decision.profile_name,
                command=decision.normalized_command,
                decision="denied",
                deny_reason=decision.reason,
            )
            return SkillOutput(
                status="error",
                error_info=(
                    f"{error_prefix} '{decision.profile_name}': "
                    f"{decision.reason} ({decision.normalized_command[:200]})"
                ),
            )
        logger.info(
            "exec.script.allowed",
            tool_name="exec_script",
            exec_profile=decision.profile_name,
            command=decision.normalized_command,
            decision="allowed",
            deny_reason="",
        )

        file_descriptor, raw_path = tempfile.mkstemp(
            suffix=suffix,
            dir=str(self.sandbox_dir),
            text=True,
        )
        os.close(file_descriptor)
        script_path = Path(raw_path).resolve(strict=False)
        try:
            script_path.write_text(code, encoding="utf-8")
            process = await asyncio.create_subprocess_exec(
                interpreter,
                str(script_path),
                stdout=PIPE,
                stderr=PIPE,
                cwd=str(self.default_workdir),
                env=os.environ.copy(),
            )
            result = await self._collect_process_result(process=process, timeout=timeout)
        finally:
            self._safe_unlink(script_path)

        return self._to_skill_output(
            result=result,
            metadata={
                "timeout_seconds": timeout,
                "language": language,
                "workdir": str(self.default_workdir),
                "exec_profile": decision.profile_name,
            },
        )

    def _resolve_default_workdir(self) -> Path:
        try:
            return get_agent_root().resolve(strict=False)
        except Exception:
            return Path.home().expanduser().resolve(strict=False)

    def _resolve_workdir(self, value: Any) -> Path | None:
        if value is None or str(value).strip() == "":
            return self.default_workdir
        candidate = Path(str(value)).expanduser().resolve(strict=False)
        if not candidate.exists() or not candidate.is_dir():
            return None
        return candidate

    def _coerce_timeout(self, value: Any) -> int:
        try:
            return max(1, int(value or self.default_timeout_seconds))
        except (TypeError, ValueError):
            return self.default_timeout_seconds

    def _validate_cli_json_command(
        self,
        command: str,
        params: dict[str, Any],
        *,
        profile_name: str,
    ) -> str | None:
        if profile_name != "cli-json":
            return None

        allowed_commands = [str(item).strip() for item in params.get("allowed_commands", []) if str(item).strip()]
        if not allowed_commands:
            return "Command not allowed by exec profile 'cli-json': missing allowed_commands metadata."

        try:
            tokens = shlex.split(command, posix=True)
        except ValueError as exc:
            return f"Command not allowed by exec profile 'cli-json': invalid shell quoting ({exc})."

        if not tokens:
            return "Command not allowed by exec profile 'cli-json': empty command."

        if "`" in command or "$(" in command:
            return "Command not allowed by exec profile 'cli-json': shell control operator is forbidden."

        shell_control_pattern = re.compile(r"^(?:\|\|?|&&|;|<<?|>>?|[0-9]+>>?|[0-9]+>|\&)$")
        if any(shell_control_pattern.match(token) for token in tokens):
            return "Command not allowed by exec profile 'cli-json': shell control operator is forbidden."

        command_index = 0
        env_assignment_pattern = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")
        while command_index < len(tokens) and env_assignment_pattern.match(tokens[command_index]):
            command_index += 1

        if command_index >= len(tokens):
            return "Command not allowed by exec profile 'cli-json': missing executable."

        executable = tokens[command_index]
        if "/" in executable:
            return (
                "Command not allowed by exec profile 'cli-json': executable must be a declared PATH command, "
                "not a filesystem path."
            )
        if executable not in allowed_commands:
            return (
                "Command not allowed by exec profile 'cli-json': "
                f"'{executable}' is not declared in cli_commands."
            )
        return None

    def _resolve_interpreter(self, language: str) -> tuple[str | None, str]:
        if language in {"bash", "shell", "sh"}:
            return shutil.which("bash") or "bash", ".sh"
        if language in {"python", "python3", "py"}:
            return sys.executable, ".py"

        interpreter = shutil.which(language)
        suffix = f".{language}" if language.isidentifier() else ".txt"
        return interpreter, suffix

    async def _collect_process_result(
        self,
        *,
        process: asyncio.subprocess.Process,
        timeout: int,
    ) -> dict[str, Any]:
        stdout_bytes = b""
        stderr_bytes = b""
        timed_out = False

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            timed_out = True
            process.terminate()
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(),
                    timeout=_TERMINATE_GRACE_SECONDS,
                )
            except asyncio.TimeoutError:
                process.kill()
                stdout_bytes, stderr_bytes = await process.communicate()

        stdout, stdout_truncated = self._truncate_output(
            stdout_bytes.decode("utf-8", errors="replace")
        )
        stderr, stderr_truncated = self._truncate_output(
            stderr_bytes.decode("utf-8", errors="replace")
        )
        return {
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": int(process.returncode if process.returncode is not None else -1),
            "timed_out": timed_out,
            "truncated": stdout_truncated or stderr_truncated,
        }

    def _to_skill_output(
        self,
        *,
        result: dict[str, Any],
        metadata: dict[str, Any],
    ) -> SkillOutput:
        timed_out = bool(result["timed_out"])
        exit_code = int(result["exit_code"])
        if timed_out:
            status = "timeout"
            error_info = f"Command timed out after {metadata['timeout_seconds']} seconds"
        elif exit_code == 0:
            status = "success"
            error_info = ""
        else:
            status = "error"
            error_info = f"Command exited with status {exit_code}"

        return SkillOutput(
            status=status,
            result={
                "stdout": result["stdout"],
                "stderr": result["stderr"],
                "exit_code": exit_code,
                "timed_out": timed_out,
            },
            metadata={**metadata, "truncated": bool(result["truncated"])},
            error_info=error_info,
        )

    def _truncate_output(self, text: str) -> tuple[str, bool]:
        if len(text) <= self.max_output_chars:
            return text, False
        suffix = f"\n[truncated to {self.max_output_chars} chars]"
        return text[: self.max_output_chars] + suffix, True

    def _safe_unlink(self, path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("exec.script.cleanup_failed", path=str(path))

    async def _ensure_process_reaped(self, process: asyncio.subprocess.Process | None) -> None:
        if process is None or process.returncode is not None:
            return
        process.kill()
        try:
            await process.communicate()
        except Exception:
            logger.warning("exec.process.cleanup_failed", exc_info=True)
