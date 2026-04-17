from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
import json
import logging
from threading import Lock
from typing import Any, Literal

from hypo_agent.utils.timeutil import now_iso, to_local

RecentLogLevel = Literal["error", "warning"]

_MAX_RECENT_LOGS = 100
_recent_logs: deque[dict[str, Any]] = deque(maxlen=_MAX_RECENT_LOGS)
_recent_logs_lock = Lock()


def _normalize_level(levelname: str | None) -> RecentLogLevel | None:
    normalized = str(levelname or "").strip().lower()
    if normalized in {"warning", "warn"}:
        return "warning"
    if normalized in {"error", "critical", "exception", "fatal"}:
        return "error"
    return None


def _truncate(value: str, *, limit: int = 4000) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1].rstrip()}…"


def _coerce_message(raw: str) -> tuple[str, str]:
    text = str(raw or "").strip()
    if not text:
        return "", ""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return _truncate(text), _truncate(text)

    if not isinstance(payload, dict):
        normalized = _truncate(text)
        return normalized, normalized

    event = str(payload.get("event") or payload.get("message") or text).strip()
    detail = _truncate(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return _truncate(event), detail


def record_recent_log(
    *,
    timestamp: str | None = None,
    level: str,
    message: str,
    detail: str | None = None,
    source: str = "",
) -> None:
    normalized_level = _normalize_level(level)
    if normalized_level is None:
        return
    entry = {
        "timestamp": timestamp
        or now_iso(),
        "level": normalized_level,
        "message": _truncate(message, limit=240),
        "detail": _truncate(detail or message),
        "source": str(source or "").strip(),
    }
    with _recent_logs_lock:
        _recent_logs.appendleft(entry)


def get_recent_logs(*, level: str = "all", limit: int = 10) -> list[dict[str, Any]]:
    normalized_filter = str(level or "all").strip().lower()
    allowed_levels = {"error", "warning"} if normalized_filter == "all" else {normalized_filter}
    with _recent_logs_lock:
        rows = list(_recent_logs)
    filtered = [row.copy() for row in rows if row.get("level") in allowed_levels]
    return filtered[: max(1, int(limit))]


def clear_recent_logs() -> None:
    with _recent_logs_lock:
        _recent_logs.clear()


class RecentLogBufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        normalized_level = _normalize_level(record.levelname)
        if normalized_level is None:
            return
        raw_message = record.getMessage()
        message, fallback_detail = _coerce_message(raw_message)
        if record.exc_info:
            detail = self.format(record)
        else:
            detail = fallback_detail
        record_recent_log(
            timestamp=to_local(datetime.fromtimestamp(record.created, tz=UTC)).replace(microsecond=0).isoformat(),
            level=normalized_level,
            message=message or raw_message,
            detail=detail or raw_message,
            source=record.name,
        )


def install_recent_log_handler() -> None:
    root_logger = logging.getLogger()
    if any(isinstance(handler, RecentLogBufferHandler) for handler in root_logger.handlers):
        return
    root_logger.addHandler(RecentLogBufferHandler(level=logging.WARNING))
