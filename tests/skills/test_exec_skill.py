from __future__ import annotations

import asyncio
from pathlib import Path

from hypo_agent.skills.exec_skill import ExecSkill


def _write_exec_profiles(path: Path) -> Path:
    path.write_text(
        """
profiles:
  git:
    allow_prefixes:
      - "git status"
      - "git diff"
      - "git push"
    deny_prefixes:
      - "git push --force"
  scripts:
    allow_prefixes:
      - "bash"
    deny_prefixes: []
  default:
    allow_prefixes:
      - "*"
    deny_prefixes:
      - "rm -rf /"
      - "shutdown"
""".strip(),
        encoding="utf-8",
    )
    return path


def test_exec_command_basic() -> None:
    skill = ExecSkill()

    output = asyncio.run(skill.execute("exec_command", {"command": "printf 'hello\\n'"}))

    assert output.status == "success"
    assert output.result["stdout"] == "hello\n"
    assert output.result["stderr"] == ""
    assert output.result["exit_code"] == 0
    assert output.result["timed_out"] is False


def test_exec_command_timeout() -> None:
    skill = ExecSkill(default_timeout_seconds=1)

    output = asyncio.run(
        skill.execute(
            "exec_command",
            {
                "command": "python -c \"import time; time.sleep(2)\"",
            },
        )
    )

    assert output.status == "timeout"
    assert output.result["timed_out"] is True


def test_exec_command_stderr() -> None:
    skill = ExecSkill()

    output = asyncio.run(
        skill.execute(
            "exec_command",
            {
                "command": "python -c \"import sys; sys.stderr.write('oops\\\\n'); sys.exit(3)\"",
            },
        )
    )

    assert output.status == "error"
    assert output.result["stderr"] == "oops\n"
    assert output.result["exit_code"] == 3


def test_exec_command_workdir(tmp_path: Path) -> None:
    skill = ExecSkill()

    output = asyncio.run(
        skill.execute(
            "exec_command",
            {
                "command": "pwd",
                "workdir": str(tmp_path),
            },
        )
    )

    assert output.status == "success"
    assert output.result["stdout"].strip() == str(tmp_path)


def test_exec_command_output_truncation() -> None:
    skill = ExecSkill()

    output = asyncio.run(
        skill.execute(
            "exec_command",
            {
                "command": "python -c \"print('x' * 270000, end='')\"",
            },
        )
    )

    assert output.status == "success"
    assert output.metadata["truncated"] is True
    assert "[truncated to 262144 chars]" in output.result["stdout"]


def test_exec_script_bash() -> None:
    skill = ExecSkill()

    output = asyncio.run(
        skill.execute(
            "exec_script",
            {
                "code": "printf 'from bash\\n'",
                "language": "bash",
            },
        )
    )

    assert output.status == "success"
    assert output.result["stdout"] == "from bash\n"


def test_exec_script_python() -> None:
    skill = ExecSkill()

    output = asyncio.run(
        skill.execute(
            "exec_script",
            {
                "code": "print('from python')",
                "language": "python",
            },
        )
    )

    assert output.status == "success"
    assert output.result["stdout"] == "from python\n"


def test_exec_command_profile_allows_whitelisted_command(tmp_path: Path) -> None:
    skill = ExecSkill(exec_profiles_path=_write_exec_profiles(tmp_path / "exec_profiles.yaml"))

    output = asyncio.run(
        skill.execute(
            "exec_command",
            {
                "command": "git status --short",
                "exec_profile": "git",
            },
        )
    )

    assert output.status == "success"
    assert output.metadata["exec_profile"] == "git"


def test_exec_command_profile_rejects_denied_command(tmp_path: Path) -> None:
    skill = ExecSkill(exec_profiles_path=_write_exec_profiles(tmp_path / "exec_profiles.yaml"))

    output = asyncio.run(
        skill.execute(
            "exec_command",
            {
                "command": "git push --force origin main",
                "exec_profile": "git",
            },
        )
    )

    assert output.status == "error"
    assert "denied by exec profile" in output.error_info.lower()
    assert "git" in output.error_info


def test_exec_command_unknown_command_uses_default_profile(tmp_path: Path) -> None:
    skill = ExecSkill(exec_profiles_path=_write_exec_profiles(tmp_path / "exec_profiles.yaml"))

    output = asyncio.run(
        skill.execute(
            "exec_command",
            {
                "command": "printf 'default-ok\\n'",
            },
        )
    )

    assert output.status == "success"
    assert output.metadata["exec_profile"] == "default"


def test_exec_script_uses_profile_validation(tmp_path: Path) -> None:
    skill = ExecSkill(exec_profiles_path=_write_exec_profiles(tmp_path / "exec_profiles.yaml"))

    output = asyncio.run(
        skill.execute(
            "exec_script",
            {
                "code": "printf 'blocked\\n'",
                "language": "bash",
                "exec_profile": "git",
            },
        )
    )

    assert output.status == "error"
    assert "not allowed by exec profile" in output.error_info.lower()
