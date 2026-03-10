from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from hypo_agent.models import DirectoryWhitelist, WhitelistRule
from hypo_agent.security.permission_manager import PermissionManager
from hypo_agent.skills import code_run_skill as code_run_module
from hypo_agent.skills.code_run_skill import CodeRunSkill


class StubProcess:
    def __init__(self, *, stdout: bytes = b"ok\n", stderr: bytes = b"", returncode: int = 0) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr

    def kill(self) -> None:
        self.killed = True


def _permission_manager(tmp_path: Path) -> PermissionManager:
    writable = tmp_path / "projects"
    writable.mkdir(parents=True, exist_ok=True)
    readonly = tmp_path / "docs"
    readonly.mkdir(parents=True, exist_ok=True)
    return PermissionManager(
        DirectoryWhitelist(
            rules=[
                WhitelistRule(path=str(writable), permissions=["read", "write", "execute"]),
                WhitelistRule(path=str(readonly), permissions=["read"]),
            ],
            default_policy="readonly",
        )
    )


def test_build_bwrap_command_includes_rw_overrides(tmp_path: Path) -> None:
    manager = _permission_manager(tmp_path)
    skill = CodeRunSkill(permission_manager=manager, sandbox_dir=tmp_path / "sandbox")

    command = "python /tmp/hypo-agent-sandbox/run.py"
    bwrap_cmd = skill._build_bwrap_command(command)
    serialized = " ".join(bwrap_cmd)

    assert bwrap_cmd[0] == "bwrap"
    assert "--ro-bind / /" in serialized
    assert "--dev /dev" in serialized
    assert "--proc /proc" in serialized
    assert "--unshare-all" in serialized
    assert "--share-net" in serialized
    assert f"--bind {tmp_path / 'projects'} {tmp_path / 'projects'}" in serialized
    assert f"--bind {tmp_path / 'sandbox'} {tmp_path / 'sandbox'}" in serialized
    assert bwrap_cmd[-3:] == ["bash", "-lc", command]


def test_code_run_skill_executes_with_bwrap_when_available(tmp_path: Path, monkeypatch) -> None:
    manager = _permission_manager(tmp_path)
    calls: list[tuple[str, ...]] = []
    events: list[str] = []

    class LogRecorder:
        def info(self, event: str, **kwargs) -> None:
            del kwargs
            events.append(event)

        def warning(self, event: str, **kwargs) -> None:
            del kwargs
            events.append(event)

    monkeypatch.setattr(code_run_module, "logger", LogRecorder())

    async def fake_subprocess_exec(*cmd: str, **kwargs: Any) -> StubProcess:
        del kwargs
        calls.append(tuple(cmd))
        return StubProcess(stdout=b"result\n", stderr=b"", returncode=0)

    skill = CodeRunSkill(
        permission_manager=manager,
        sandbox_dir=tmp_path / "sandbox",
        subprocess_exec=fake_subprocess_exec,
        which_fn=lambda _: "/usr/bin/bwrap",
    )

    output = asyncio.run(
        skill.execute(
            "run_code",
            {
                "code": "print('ok')",
                "language": "python",
            },
        )
    )

    assert output.status == "success"
    assert calls
    assert calls[0][0] == "bwrap"
    assert output.metadata["sandbox_backend"] == "bwrap"
    assert "code_run.bwrap.exec" in events


def test_code_run_skill_falls_back_when_bwrap_missing(tmp_path: Path, monkeypatch) -> None:
    manager = _permission_manager(tmp_path)
    calls: list[tuple[str, ...]] = []
    events: list[str] = []

    class LogRecorder:
        def info(self, event: str, **kwargs) -> None:
            del kwargs
            events.append(event)

        def warning(self, event: str, **kwargs) -> None:
            del kwargs
            events.append(event)

    monkeypatch.setattr(code_run_module, "logger", LogRecorder())

    async def fake_subprocess_exec(*cmd: str, **kwargs: Any) -> StubProcess:
        del kwargs
        calls.append(tuple(cmd))
        return StubProcess(stdout=b"ok\n", stderr=b"", returncode=0)

    skill = CodeRunSkill(
        permission_manager=manager,
        sandbox_dir=tmp_path / "sandbox",
        subprocess_exec=fake_subprocess_exec,
        which_fn=lambda _: None,
    )

    output = asyncio.run(skill.execute("run_code", {"code": "echo ok", "language": "shell"}))

    assert output.status == "success"
    assert calls
    assert calls[0][0] == "bash"
    assert calls[0][1] == "-lc"
    assert output.metadata["sandbox_backend"] == "fallback"
    assert "code_run.bwrap.fallback" in events


def test_code_run_skill_rejects_unsupported_language(tmp_path: Path) -> None:
    manager = _permission_manager(tmp_path)
    skill = CodeRunSkill(permission_manager=manager, sandbox_dir=tmp_path / "sandbox")

    output = asyncio.run(
        skill.execute(
            "run_code",
            {
                "code": "console.log('x')",
                "language": "javascript",
            },
        )
    )

    assert output.status == "error"
    assert "Unsupported language" in output.error_info


def test_code_run_skill_runtime_falls_back_when_bwrap_exec_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manager = _permission_manager(tmp_path)
    calls: list[tuple[str, ...]] = []
    events: list[str] = []

    class LogRecorder:
        def info(self, event: str, **kwargs) -> None:
            del kwargs
            events.append(event)

        def warning(self, event: str, **kwargs) -> None:
            del kwargs
            events.append(event)

    monkeypatch.setattr(code_run_module, "logger", LogRecorder())

    async def fake_subprocess_exec(*cmd: str, **kwargs: Any) -> StubProcess:
        del kwargs
        calls.append(tuple(cmd))
        if len(calls) == 1:
            return StubProcess(
                stdout=b"",
                stderr=b"bwrap: setting up uid map: Permission denied\n",
                returncode=1,
            )
        return StubProcess(stdout=b"ok\n", stderr=b"", returncode=0)

    skill = CodeRunSkill(
        permission_manager=manager,
        sandbox_dir=tmp_path / "sandbox",
        subprocess_exec=fake_subprocess_exec,
        which_fn=lambda _: "/usr/bin/bwrap",
    )

    output = asyncio.run(skill.execute("run_code", {"code": "echo ok", "language": "shell"}))

    assert output.status == "success"
    assert len(calls) == 2
    assert calls[0][0] == "bwrap"
    assert calls[1][0] == "bash"
    assert calls[1][1] == "-lc"
    assert output.metadata["sandbox_backend"] == "fallback"
    assert "code_run.bwrap.runtime_fallback" in events


def test_code_run_skill_default_max_output_chars_is_256k(tmp_path: Path) -> None:
    manager = _permission_manager(tmp_path)
    skill = CodeRunSkill(permission_manager=manager, sandbox_dir=tmp_path / "sandbox")
    assert skill.max_output_chars == 262144
