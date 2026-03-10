from __future__ import annotations

from pathlib import Path
import shlex
from typing import Any
from uuid import uuid4

from hypo_agent.models import SkillOutput
from hypo_agent.skills.base import BaseSkill
from hypo_agent.skills.tmux_skill import TmuxSkill


class CodeRunSkill(BaseSkill):
    name = "code_run"
    description = "Run Python or shell code in a sandboxed temp file."
    required_permissions: list[str] = []

    def __init__(
        self,
        *,
        tmux_skill: TmuxSkill | None = None,
        sandbox_dir: Path | str = "/tmp/hypo-agent-sandbox",
    ) -> None:
        self.tmux_skill = tmux_skill or TmuxSkill(sandbox_dir=sandbox_dir)
        self.sandbox_dir = Path(sandbox_dir)
        self.sandbox_dir.mkdir(parents=True, exist_ok=True)

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

        suffix = ".py" if language == "python" else ".sh"
        file_path = self.sandbox_dir / f"{uuid4().hex}{suffix}"
        file_path.write_text(code, encoding="utf-8")

        if language == "python":
            command = f"python {shlex.quote(str(file_path))}"
        else:
            command = f"bash {shlex.quote(str(file_path))}"

        output = await self.tmux_skill.execute(
            "run_command",
            {
                "command": command,
                "session_name": "hypo-agent-code",
            },
        )

        metadata = dict(output.metadata)
        metadata.update({"language": language, "file_path": str(file_path)})
        return SkillOutput(
            status=output.status,
            result=output.result,
            error_info=output.error_info,
            metadata=metadata,
        )

