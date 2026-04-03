"""Log and tool history inspection skill for self-diagnosis.

Future HTTP API design notes (not implemented in this milestone):
- `GET /api/skills/log-inspector/logs?minutes=30&level=error&limit=100`
- `GET /api/skills/log-inspector/tools?skill_name=exec&success=false&hours=24&limit=50`
- `GET /api/skills/log-inspector/errors?hours=6`
- `GET /api/skills/log-inspector/sessions?hours=24`
- `GET /api/skills/log-inspector/sessions/{session_id}?hours=24`
"""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import subprocess
from typing import Any
from urllib.parse import quote

import aiosqlite
import structlog

from hypo_agent.core.config_loader import get_memory_dir
from hypo_agent.models import Message, SkillOutput
from hypo_agent.skills.base import BaseSkill

_VALID_LOG_LEVELS = {"warning", "error", "critical"}
logger = structlog.get_logger("hypo_agent.skills.log_inspector")
_LOG_INSPECTOR_ERRORS = (
    aiosqlite.Error,
    subprocess.SubprocessError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
    json.JSONDecodeError,
)


class LogInspectorSkill(BaseSkill):
    name = "log_inspector"
    description = "Inspect recent journald logs, tool invocation history, and session history."
    required_permissions: list[str] = []

    def __init__(
        self,
        *,
        structured_store: Any,
        permission_manager: Any | None = None,
        sessions_dir: Path | str | None = None,
        service_name: str = "hypo-agent",
        journalctl_bin: str = "journalctl",
    ) -> None:
        self.structured_store = structured_store
        self.permission_manager = permission_manager
        self.sessions_dir = Path(sessions_dir or (get_memory_dir() / "sessions"))
        self.service_name = str(service_name or "hypo-agent").strip() or "hypo-agent"
        self.journalctl_bin = str(journalctl_bin or "journalctl").strip() or "journalctl"

    @property
    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "get_recent_logs",
                    "description": "读取最近 journald structlog JSON 日志，可按级别过滤。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "minutes": {"type": "integer", "minimum": 1, "default": 30},
                            "level": {
                                "type": "string",
                                "enum": ["warning", "error", "critical"],
                            },
                            "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_tool_history",
                    "description": "查询最近的工具调用历史，可按 skill 和成功状态过滤。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "skill_name": {"type": "string"},
                            "success": {"type": "boolean"},
                            "hours": {"type": "integer", "minimum": 1, "default": 24},
                            "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_error_summary",
                    "description": "组合最近日志和工具失败记录，输出错误统计与最近错误详情。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "hours": {"type": "integer", "minimum": 1, "default": 6},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_session_history",
                    "description": "列出最近会话摘要，或返回指定会话的消息摘要。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "session_id": {"type": "string"},
                            "hours": {"type": "integer", "minimum": 1, "default": 24},
                        },
                    },
                },
            },
        ]

    async def execute(self, tool_name: str, params: dict[str, Any]) -> SkillOutput:
        try:
            if tool_name == "get_recent_logs":
                minutes = self._coerce_positive_int(params.get("minutes"), default=30, field="minutes")
                limit = self._coerce_positive_int(params.get("limit"), default=100, field="limit", max_value=500)
                level = self._normalize_level_filter(params.get("level"))
                return SkillOutput(
                    status="success",
                    result=await self.get_recent_logs(minutes=minutes, level=level, limit=limit),
                )

            if tool_name == "get_tool_history":
                hours = self._coerce_positive_int(params.get("hours"), default=24, field="hours")
                limit = self._coerce_positive_int(params.get("limit"), default=50, field="limit", max_value=500)
                success = params.get("success")
                if success is not None and not isinstance(success, bool):
                    raise ValueError("success must be a boolean when provided")
                return SkillOutput(
                    status="success",
                    result=await self.get_tool_history(
                        skill_name=str(params.get("skill_name") or "").strip() or None,
                        success=success,
                        hours=hours,
                        limit=limit,
                    ),
                )

            if tool_name == "get_error_summary":
                hours = self._coerce_positive_int(params.get("hours"), default=6, field="hours")
                return SkillOutput(
                    status="success",
                    result=await self.get_error_summary(hours=hours),
                )

            if tool_name == "get_session_history":
                hours = self._coerce_positive_int(params.get("hours"), default=24, field="hours")
                session_id = str(params.get("session_id") or "").strip() or None
                return SkillOutput(
                    status="success",
                    result=await self.get_session_history(session_id=session_id, hours=hours),
                )
        except _LOG_INSPECTOR_ERRORS as exc:
            logger.warning("log_inspector.execute.failed", tool_name=tool_name, error=str(exc))
            return SkillOutput(status="error", error_info=str(exc))

        return SkillOutput(status="error", error_info=f"Unsupported tool '{tool_name}'")

    async def get_recent_logs(
        self,
        *,
        minutes: int = 30,
        level: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        normalized_level = self._normalize_level_filter(level)
        try:
            rows = await asyncio.to_thread(
                self._read_journalctl_rows,
                minutes=minutes,
                limit=limit,
            )
        except FileNotFoundError:
            return {
                "available": False,
                "source": "journalctl",
                "minutes": minutes,
                "level": normalized_level,
                "count": 0,
                "items": [],
                "warning": f"{self.journalctl_bin} is not available in this environment",
            }
        except _LOG_INSPECTOR_ERRORS as exc:
            logger.warning("log_inspector.recent_logs.failed", error=str(exc))
            return {
                "available": False,
                "source": "journalctl",
                "minutes": minutes,
                "level": normalized_level,
                "count": 0,
                "items": [],
                "warning": f"failed to read journalctl: {exc}",
            }

        items = [item for item in rows if normalized_level is None or item["level"] == normalized_level]
        return {
            "available": True,
            "source": "journalctl",
            "minutes": minutes,
            "level": normalized_level,
            "count": len(items),
            "items": items[:limit],
        }

    async def get_tool_history(
        self,
        *,
        skill_name: str | None = None,
        success: bool | None = None,
        hours: int = 24,
        limit: int = 50,
    ) -> dict[str, Any]:
        db_path = self._store_db_path()
        self._ensure_readable(db_path)
        since = datetime.now(UTC) - timedelta(hours=hours)
        rows = await self._query_tool_history_rows(
            db_path=db_path,
            since=since,
            skill_name=skill_name,
            success=success,
            limit=limit,
        )
        return {
            "source": str(db_path),
            "hours": hours,
            "skill_name": skill_name,
            "success": success,
            "count": len(rows),
            "items": rows,
        }

    async def get_error_summary(self, *, hours: int = 6) -> dict[str, Any]:
        logs_result = await self.get_recent_logs(minutes=hours * 60, level="error", limit=200)
        tool_result = await self.get_tool_history(success=False, hours=hours, limit=200)

        counter: Counter[str] = Counter()
        recent_errors: list[dict[str, Any]] = []

        for item in logs_result.get("items", []):
            key = f"log:{item.get('event') or 'unknown'}"
            counter[key] += 1
            recent_errors.append(
                {
                    "source": "log",
                    "timestamp": item.get("timestamp"),
                    "type": key,
                    "summary": item.get("event") or item.get("raw") or "",
                    "detail": self._truncate(
                        json.dumps(item.get("context") or {}, ensure_ascii=False, sort_keys=True),
                        limit=240,
                    ),
                }
            )

        for item in tool_result.get("items", []):
            key = f"tool:{item.get('skill_name') or 'unknown'}.{item.get('tool_name') or 'unknown'}"
            counter[key] += 1
            recent_errors.append(
                {
                    "source": "tool",
                    "timestamp": item.get("created_at"),
                    "type": key,
                    "summary": item.get("tool_name") or "",
                    "detail": item.get("error_info") or item.get("input_summary") or "",
                }
            )

        recent_errors.sort(
            key=lambda item: str(item.get("timestamp") or ""),
            reverse=True,
        )

        return {
            "hours": hours,
            "counts": {
                "logs": len(logs_result.get("items", [])),
                "tool_failures": len(tool_result.get("items", [])),
                "total": len(recent_errors),
            },
            "error_types": dict(counter.most_common()),
            "recent_errors": recent_errors[:5],
            "log_source_available": bool(logs_result.get("available", False)),
            "log_warning": logs_result.get("warning"),
        }

    async def get_session_history(
        self,
        *,
        session_id: str | None = None,
        hours: int = 24,
    ) -> dict[str, Any]:
        self._ensure_readable(self.sessions_dir)
        if session_id:
            messages = await asyncio.to_thread(self._read_session_messages, session_id)
            return {
                "session_id": session_id,
                "hours": hours,
                "message_count": len(messages),
                "messages": [
                    {
                        "timestamp": message.get("timestamp"),
                        "sender": message.get("sender"),
                        "message_tag": message.get("message_tag"),
                        "summary": message.get("summary"),
                    }
                    for message in messages
                ],
            }

        cutoff = datetime.now(UTC) - timedelta(hours=hours)
        sessions = await asyncio.to_thread(self._list_recent_sessions, cutoff)
        return {
            "hours": hours,
            "count": len(sessions),
            "sessions": sessions,
        }

    def _read_journalctl_rows(self, *, minutes: int, limit: int) -> list[dict[str, Any]]:
        completed = subprocess.run(
            [
                self.journalctl_bin,
                "-u",
                self.service_name,
                "-o",
                "cat",
                "--since",
                f"{minutes} min ago",
                "-n",
                str(limit),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0 and not completed.stdout.strip():
            stderr = completed.stderr.strip() or f"{self.journalctl_bin} exited with {completed.returncode}"
            raise RuntimeError(stderr)

        items: list[dict[str, Any]] = []
        for raw_line in completed.stdout.splitlines():
            parsed = self._parse_log_line(raw_line)
            if parsed is not None:
                items.append(parsed)
        return items

    def _parse_log_line(self, raw_line: str) -> dict[str, Any] | None:
        text = str(raw_line or "").strip()
        if not text:
            return None
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return {
                "timestamp": None,
                "level": "unknown",
                "event": text,
                "logger": None,
                "context": {},
                "raw": self._truncate(text, limit=500),
            }

        if not isinstance(payload, dict):
            return {
                "timestamp": None,
                "level": "unknown",
                "event": self._truncate(text, limit=500),
                "logger": None,
                "context": {},
                "raw": self._truncate(text, limit=500),
            }

        level = self._normalize_log_level(payload.get("level"))
        event = str(payload.get("event") or payload.get("message") or "").strip() or "unknown"
        context = {
            key: value
            for key, value in payload.items()
            if key not in {"timestamp", "level", "event", "message", "logger"}
        }
        return {
            "timestamp": str(payload.get("timestamp") or payload.get("ts") or ""),
            "level": level,
            "event": event,
            "logger": str(payload.get("logger") or ""),
            "context": context,
            "raw": self._truncate(text, limit=500),
        }

    async def _query_tool_history_rows(
        self,
        *,
        db_path: Path,
        since: datetime,
        skill_name: str | None,
        success: bool | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("PRAGMA table_info(tool_invocations)") as cursor:
                columns = {str(row[1]) for row in await cursor.fetchall()}

            clauses = ["datetime(created_at) >= datetime(?)"]
            params: list[Any] = [since.isoformat()]

            skill_col = "skill_name" if "skill_name" in columns else None
            status_col = "status" if "status" in columns else None
            success_col = "success" if "success" in columns else None

            if skill_name and skill_col is not None:
                clauses.append(f"{skill_col} = ?")
                params.append(skill_name)
            if success is not None:
                if status_col is not None:
                    clauses.append(f"{status_col} {'=' if success else '!='} ?")
                    params.append("success")
                elif success_col is not None:
                    clauses.append(f"{success_col} = ?")
                    params.append(1 if success else 0)

            select_parts = [
                "id",
                "session_id",
                "tool_name",
                f"{skill_col or 'NULL'} AS skill_name",
                f"{'params_json' if 'params_json' in columns else ('input_json' if 'input_json' in columns else 'NULL')} AS input_json",
                f"{status_col or ('CASE WHEN success THEN \"success\" ELSE \"error\" END' if success_col else 'NULL')} AS status",
                f"{'result_summary' if 'result_summary' in columns else ('output_json' if 'output_json' in columns else 'NULL')} AS output_value",
                "duration_ms",
                f"{'error_info' if 'error_info' in columns else 'NULL'} AS error_info",
                "created_at",
            ]
            query = (
                f"SELECT {', '.join(select_parts)} FROM tool_invocations "
                f"WHERE {' AND '.join(clauses)} "
                "ORDER BY created_at DESC, id DESC LIMIT ?"
            )
            params.append(limit)

            async with db.execute(query, tuple(params)) as cursor:
                rows = await cursor.fetchall()

        return [self._normalize_tool_row(dict(row)) for row in rows]

    def _normalize_tool_row(self, row: dict[str, Any]) -> dict[str, Any]:
        status = str(row.get("status") or "").strip().lower()
        input_value = str(row.get("input_json") or "").strip()
        output_value = str(row.get("output_value") or "").strip()
        error_info = str(row.get("error_info") or "").strip()
        return {
            "id": row.get("id"),
            "session_id": str(row.get("session_id") or ""),
            "tool_name": str(row.get("tool_name") or ""),
            "skill_name": str(row.get("skill_name") or ""),
            "input_summary": self._summarize_jsonish(input_value, limit=200),
            "output_summary": self._summarize_jsonish(output_value, limit=200),
            "success": status == "success",
            "status": status or ("success" if not error_info else "error"),
            "duration_ms": row.get("duration_ms"),
            "error_info": self._truncate(error_info, limit=200),
            "created_at": str(row.get("created_at") or ""),
        }

    def _list_recent_sessions(self, cutoff: datetime) -> list[dict[str, Any]]:
        sessions: list[dict[str, Any]] = []
        if not self.sessions_dir.exists():
            return sessions
        for session_file in self.sessions_dir.glob("*.jsonl"):
            messages = self._read_session_file(session_file)
            if not messages:
                continue
            first_ts = messages[0].timestamp
            last_ts = messages[-1].timestamp
            normalized_last = self._normalize_dt(last_ts)
            if normalized_last is not None and normalized_last < cutoff:
                continue
            sessions.append(
                {
                    "session_id": messages[0].session_id,
                    "message_count": len(messages),
                    "created_at": self._format_dt(first_ts),
                    "updated_at": self._format_dt(last_ts),
                }
            )
        sessions.sort(
            key=lambda item: str(item.get("updated_at") or ""),
            reverse=True,
        )
        return sessions

    def _read_session_messages(self, session_id: str) -> list[dict[str, Any]]:
        session_file = self._session_file(session_id)
        messages = self._read_session_file(session_file)
        return [
            {
                "timestamp": self._format_dt(message.timestamp),
                "sender": message.sender,
                "message_tag": message.message_tag,
                "summary": self._summarize_message(message),
            }
            for message in messages
        ]

    def _read_session_file(self, session_file: Path) -> list[Message]:
        self._ensure_readable(session_file.parent)
        if not session_file.exists():
            return []
        self._ensure_readable(session_file)
        messages: list[Message] = []
        for line in session_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            messages.append(Message.model_validate_json(stripped))
        return messages

    def _session_file(self, session_id: str) -> Path:
        safe_session = quote(str(session_id or ""), safe="")
        return self.sessions_dir / f"{safe_session}.jsonl"

    def _summarize_message(self, message: Message) -> str:
        parts = [
            str(message.text or "").strip(),
            f"[image:{message.image}]" if message.image else "",
            f"[file:{message.file}]" if message.file else "",
            f"[audio:{message.audio}]" if message.audio else "",
        ]
        combined = " ".join(part for part in parts if part).strip()
        return self._truncate(combined or "<empty>", limit=200)

    def _store_db_path(self) -> Path:
        raw = getattr(self.structured_store, "db_path", None)
        if raw is None:
            raise ValueError("structured_store must expose db_path")
        return Path(raw)

    def _ensure_readable(self, path: Path) -> None:
        if self.permission_manager is None:
            return
        allowed, reason = self.permission_manager.check_permission(str(path), "read", log_allowed=False)
        if not allowed:
            raise PermissionError(reason)

    def _coerce_positive_int(
        self,
        raw: Any,
        *,
        default: int,
        field: str,
        max_value: int | None = None,
    ) -> int:
        if raw in (None, ""):
            value = default
        else:
            value = int(raw)
        if value <= 0:
            raise ValueError(f"{field} must be greater than 0")
        if max_value is not None:
            value = min(value, max_value)
        return value

    def _normalize_level_filter(self, raw: Any) -> str | None:
        if raw in (None, ""):
            return None
        normalized = self._normalize_log_level(raw)
        if normalized not in _VALID_LOG_LEVELS:
            raise ValueError("level must be one of: warning, error, critical")
        return normalized

    def _normalize_log_level(self, raw: Any) -> str:
        normalized = str(raw or "").strip().lower()
        if normalized in {"warn"}:
            return "warning"
        if normalized in {"fatal", "exception"}:
            return "critical"
        return normalized or "unknown"

    def _summarize_jsonish(self, raw: str, *, limit: int) -> str:
        text = str(raw or "").strip()
        if not text:
            return ""
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return self._truncate(text, limit=limit)
        return self._truncate(json.dumps(payload, ensure_ascii=False, sort_keys=True), limit=limit)

    def _truncate(self, raw: str, *, limit: int = 200) -> str:
        text = str(raw or "").strip()
        if len(text) <= limit:
            return text
        return f"{text[: limit - 1].rstrip()}…"

    def _format_dt(self, value: datetime | None) -> str | None:
        normalized = self._normalize_dt(value)
        return normalized.isoformat() if normalized is not None else None

    def _normalize_dt(self, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
