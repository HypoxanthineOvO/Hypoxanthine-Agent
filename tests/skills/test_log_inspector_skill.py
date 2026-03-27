from __future__ import annotations

import asyncio
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from hypo_agent.memory.structured_store import StructuredStore
from hypo_agent.skills.log_inspector_skill import LogInspectorSkill


def _seed_tool_invocation_rows(db_path: Path) -> None:
    async def _run() -> None:
        store = StructuredStore(db_path=db_path)
        await store.init()
        await store.record_tool_invocation(
            session_id="s-alpha",
            tool_name="run_command",
            skill_name="tmux",
            params_json=json.dumps({"command": "uptime"}, ensure_ascii=False),
            status="success",
            result_summary="load average: 0.42",
            duration_ms=12.5,
            error_info="",
        )
        await store.record_tool_invocation(
            session_id="s-beta",
            tool_name="scan_emails",
            skill_name="email_scanner",
            params_json=json.dumps({"unread_only": True}, ensure_ascii=False),
            status="error",
            result_summary="",
            duration_ms=88.0,
            error_info="imap timeout",
        )
    asyncio.run(_run())


def _write_session(
    sessions_dir: Path,
    session_id: str,
    messages: list[dict[str, Any]],
) -> None:
    sessions_dir.mkdir(parents=True, exist_ok=True)
    session_file = sessions_dir / f"{session_id}.jsonl"
    session_file.write_text(
        "\n".join(json.dumps(message, ensure_ascii=False) for message in messages) + "\n",
        encoding="utf-8",
    )


class DummyCompletedProcess:
    def __init__(self, *, stdout: str, returncode: int = 0, stderr: str = "") -> None:
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


def _build_skill(tmp_path: Path) -> tuple[LogInspectorSkill, StructuredStore]:
    db_path = tmp_path / "hypo.db"
    _seed_tool_invocation_rows(db_path)
    sessions_dir = tmp_path / "sessions"
    now = datetime.now(UTC)
    _write_session(
        sessions_dir,
        "s-alpha",
        [
            {
                "text": "最近半小时有没有报错？",
                "sender": "user",
                "session_id": "s-alpha",
                "timestamp": (now - timedelta(minutes=20)).isoformat(),
            },
            {
                "text": "我来检查一下。",
                "sender": "assistant",
                "session_id": "s-alpha",
                "timestamp": (now - timedelta(minutes=19)).isoformat(),
            },
        ],
    )
    _write_session(
        sessions_dir,
        "s-beta",
        [
            {
                "text": "帮我诊断一下最近的错误",
                "sender": "user",
                "session_id": "s-beta",
                "timestamp": (now - timedelta(minutes=10)).isoformat(),
            }
        ],
    )
    store = StructuredStore(db_path=db_path)
    return (
        LogInspectorSkill(
            structured_store=store,
            sessions_dir=sessions_dir,
            service_name="hypo-agent",
        ),
        store,
    )


def test_log_inspector_get_recent_logs_parses_and_filters_levels(
    tmp_path: Path,
    monkeypatch,
) -> None:
    skill, _ = _build_skill(tmp_path)
    lines = "\n".join(
        [
            json.dumps(
                {
                    "timestamp": "2026-03-26T10:00:00Z",
                    "level": "info",
                    "event": "heartbeat.start",
                    "session_id": "main",
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "timestamp": "2026-03-26T10:01:00Z",
                    "level": "error",
                    "event": "heartbeat.failed",
                    "error": "tool timeout",
                },
                ensure_ascii=False,
            ),
        ]
    )

    def fake_run(*args, **kwargs):
        del args, kwargs
        return DummyCompletedProcess(stdout=lines)

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = asyncio.run(skill.get_recent_logs(minutes=30, level="error", limit=20))

    assert result["available"] is True
    assert result["count"] == 1
    assert result["items"][0]["event"] == "heartbeat.failed"
    assert result["items"][0]["level"] == "error"
    assert result["items"][0]["context"]["error"] == "tool timeout"


def test_log_inspector_get_recent_logs_degrades_when_journalctl_missing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    skill, _ = _build_skill(tmp_path)

    def fake_run(*args, **kwargs):
        del args, kwargs
        raise FileNotFoundError("journalctl not found")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = asyncio.run(skill.get_recent_logs(minutes=30))

    assert result["available"] is False
    assert result["items"] == []
    assert "journalctl" in result["warning"].lower()


def test_log_inspector_get_tool_history_filters_skill_and_success(tmp_path: Path) -> None:
    skill, _ = _build_skill(tmp_path)

    result = asyncio.run(
        skill.get_tool_history(skill_name="email_scanner", success=False, hours=24, limit=10)
    )

    assert result["count"] == 1
    item = result["items"][0]
    assert item["skill_name"] == "email_scanner"
    assert item["tool_name"] == "scan_emails"
    assert item["success"] is False
    assert "imap timeout" in item["error_info"]


def test_log_inspector_get_error_summary_combines_logs_and_tool_failures(
    tmp_path: Path,
    monkeypatch,
) -> None:
    skill, _ = _build_skill(tmp_path)
    lines = "\n".join(
        [
            json.dumps(
                {
                    "timestamp": "2026-03-26T10:01:00Z",
                    "level": "error",
                    "event": "heartbeat.failed",
                    "error": "tool timeout",
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "timestamp": "2026-03-26T10:02:00Z",
                    "level": "warning",
                    "event": "heartbeat.skipped_overlap",
                },
                ensure_ascii=False,
            ),
        ]
    )

    def fake_run(*args, **kwargs):
        del args, kwargs
        return DummyCompletedProcess(stdout=lines)

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = asyncio.run(skill.get_error_summary(hours=6))

    assert result["hours"] == 6
    assert result["counts"]["logs"] == 1
    assert result["counts"]["tool_failures"] == 1
    assert result["error_types"]["log:heartbeat.failed"] == 1
    assert result["error_types"]["tool:email_scanner.scan_emails"] == 1
    assert len(result["recent_errors"]) == 2


def test_log_inspector_get_session_history_lists_recent_sessions(tmp_path: Path) -> None:
    skill, _ = _build_skill(tmp_path)

    result = asyncio.run(skill.get_session_history(hours=24))

    assert result["count"] == 2
    assert result["sessions"][0]["session_id"] in {"s-alpha", "s-beta"}
    assert result["sessions"][0]["message_count"] >= 1


def test_log_inspector_get_session_history_returns_session_message_summary(tmp_path: Path) -> None:
    skill, _ = _build_skill(tmp_path)

    result = asyncio.run(skill.get_session_history(session_id="s-alpha", hours=24))

    assert result["session_id"] == "s-alpha"
    assert result["message_count"] == 2
    assert result["messages"][0]["sender"] == "user"
    assert "最近半小时" in result["messages"][0]["summary"]


def test_log_inspector_session_history_uses_safe_session_file_encoding(tmp_path: Path) -> None:
    skill, _ = _build_skill(tmp_path)
    outside_file = tmp_path / "escape.jsonl"
    outside_file.write_text("should not be read\n", encoding="utf-8")

    result = asyncio.run(skill.get_session_history(session_id="../escape", hours=24))

    assert result["session_id"] == "../escape"
    assert result["message_count"] == 0
    assert result["messages"] == []


def test_log_inspector_execute_rejects_invalid_level_without_shell_call(
    tmp_path: Path,
    monkeypatch,
) -> None:
    skill, _ = _build_skill(tmp_path)
    called = False

    def fake_run(*args, **kwargs):
        nonlocal called
        called = True
        return DummyCompletedProcess(stdout="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    output = asyncio.run(skill.execute("get_recent_logs", {"level": "debug"}))

    assert output.status == "error"
    assert "level" in output.error_info
    assert called is False
