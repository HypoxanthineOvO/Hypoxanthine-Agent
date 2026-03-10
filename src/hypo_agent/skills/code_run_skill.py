from __future__ import annotations

import asyncio
from asyncio.subprocess import PIPE
from pathlib import Path
import shlex
import shutil
from typing import Any, Callable
from uuid import uuid4

import structlog

from hypo_agent.models import SkillOutput
from hypo_agent.security.permission_manager import PermissionManager
from hypo_agent.skills.base import BaseSkill

logger = structlog.get_logger()


class CodeRunSkill(BaseSkill):
    name = "code_run"
    description = "Run Python or shell code in a sandboxed temp file."
    required_permissions: list[str] = []

    def __init__(
        self,
        *,
        permission_manager: PermissionManager | None = None,
        sandbox_dir: Path | str = "/tmp/hypo-agent-sandbox",
        default_timeout_seconds: int = 30,
        max_output_chars: int = 262144,
        subprocess_exec=None,
        which_fn: Callable[[str], str | None] | None = None,
    ) -> None:
        self.permission_manager = permission_manager
        self.sandbox_dir = Path(sandbox_dir).expanduser().resolve(strict=False)
        self.sandbox_dir.mkdir(parents=True, exist_ok=True)
        self.default_timeout_seconds = default_timeout_seconds
        self.max_output_chars = max_output_chars
        self._subprocess_exec = subprocess_exec or asyncio.create_subprocess_exec
        self._which_fn = which_fn or shutil.which

    @property
    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "run_code",
                    "description": "Run code in Python or shell",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "string"},
                            "language": {
                                "type": "string",
                                "enum": ["python", "shell"],
                            },
                            "timeout": {"type": "integer", "minimum": 1},
                        },
                        "required": ["code"],
                    },
                },
            }
        ]

    async def execute(self, tool_name: str, params: dict[str, Any]) -> SkillOutput:
        if tool_name != "run_code":
            return SkillOutput(
                status="error",
                error_info=f"Unsupported tool '{tool_name}' for code_run skill",
            )

        code = str(params.get("code", "")).strip()
        if not code:
            return SkillOutput(status="error", error_info="code is required")

        language = str(params.get("language") or "python").strip().lower()
        if language not in {"python", "shell"}:
            return SkillOutput(
                status="error",
                error_info=f"Unsupported language '{language}'",
            )

        timeout = max(1, int(params.get("timeout") or self.default_timeout_seconds))
        suffix = ".py" if language == "python" else ".sh"
        file_path = self.sandbox_dir / f"{uuid4().hex}{suffix}"
        file_path.write_text(code, encoding="utf-8")

        if language == "python":
            command = f"python {shlex.quote(str(file_path))}"
        else:
            command = f"bash {shlex.quote(str(file_path))}"

        backend = "bwrap"
        if self._which_fn("bwrap"):
            exec_cmd = self._build_bwrap_command(command)
            logger.info(
                "code_run.bwrap.exec",
                command=command,
                sandbox_dir=str(self.sandbox_dir),
            )
        else:
            backend = "fallback"
            exec_cmd = ["bash", "-lc", command]
            logger.warning(
                "code_run.bwrap.fallback",
                command=command,
                reason="bwrap not found",
            )

        try:
            process_result = await self._run_process(exec_cmd, timeout=timeout)
        except asyncio.TimeoutError:
            self._safe_unlink(file_path)
            return SkillOutput(
                status="timeout",
                error_info=f"Command timed out after {timeout} seconds",
                metadata={
                    "timeout_seconds": timeout,
                    "language": language,
                    "file_path": str(file_path),
                    "sandbox_backend": backend,
                },
            )

        if (
            backend == "bwrap"
            and int(process_result["returncode"]) != 0
            and process_result["stderr"].lstrip().startswith("bwrap:")
        ):
            logger.warning(
                "code_run.bwrap.runtime_fallback",
                command=command,
                returncode=process_result["returncode"],
                stderr=process_result["stderr"],
            )
            backend = "fallback"
            try:
                process_result = await self._run_process(
                    ["bash", "-lc", command],
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                self._safe_unlink(file_path)
                return SkillOutput(
                    status="timeout",
                    error_info=f"Command timed out after {timeout} seconds",
                    metadata={
                        "timeout_seconds": timeout,
                        "language": language,
                        "file_path": str(file_path),
                        "sandbox_backend": backend,
                    },
                )

        self._safe_unlink(file_path)

        stdout, stdout_truncated = self._truncate_output(process_result["stdout"])
        stderr, stderr_truncated = self._truncate_output(process_result["stderr"])
        truncated = stdout_truncated or stderr_truncated
        exit_code = int(process_result["returncode"])

        status = "success" if exit_code == 0 else "error"
        return SkillOutput(
            status=status,
            result={
                "stdout": stdout,
                "stderr": stderr,
                "exit_code": exit_code,
            },
            error_info="" if exit_code == 0 else f"Command exited with status {exit_code}",
            metadata={
                "language": language,
                "file_path": str(file_path),
                "timeout_seconds": timeout,
                "truncated": truncated,
                "sandbox_backend": backend,
            },
        )

    def _build_bwrap_command(self, command: str) -> list[str]:
        writable_paths = self._collect_writable_paths()
        args: list[str] = [
            "bwrap",
            "--ro-bind",
            "/",
            "/",
        ]
        for path in writable_paths:
            path_str = str(path)
            args.extend(["--bind", path_str, path_str])
        args.extend(
            [
                "--dev",
                "/dev",
                "--proc",
                "/proc",
                "--unshare-all",
                "--share-net",
                "bash",
                "-lc",
                command,
            ]
        )
        return args

    def _collect_writable_paths(self) -> list[Path]:
        writable: list[Path] = [self.sandbox_dir]
        if self.permission_manager is not None:
            writable.extend(self.permission_manager.writable_paths())

        unique: list[Path] = []
        seen: set[Path] = set()
        for path in writable:
            resolved = path.expanduser().resolve(strict=False)
            if resolved not in seen:
                if not resolved.exists():
                    try:
                        resolved.mkdir(parents=True, exist_ok=True)
                    except OSError:
                        logger.warning(
                            "code_run.bwrap.bind.skip",
                            path=str(resolved),
                            reason="failed to create bind path",
                        )
                        continue
                unique.append(resolved)
                seen.add(resolved)
        return unique

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
        return text[: self.max_output_chars] + suffix, True

    def _safe_unlink(self, path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
