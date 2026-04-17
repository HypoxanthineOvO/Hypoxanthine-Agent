from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("Asia/Shanghai")


def now_local() -> datetime:
    """Return the current Asia/Shanghai time with timezone info."""
    return datetime.now(LOCAL_TZ).replace(microsecond=0)


def to_local(dt: datetime) -> datetime:
    """Convert any datetime to Asia/Shanghai, treating naive values as UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(LOCAL_TZ)


def format_local(dt: datetime, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """Format a datetime in Asia/Shanghai."""
    return to_local(dt).strftime(fmt)


def now_iso() -> str:
    """Return the current Asia/Shanghai time in ISO 8601 format."""
    return now_local().isoformat()


def parse_datetime(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def localize_iso(value: Any) -> str | None:
    parsed = parse_datetime(value)
    if parsed is None:
        cleaned = str(value or "").strip()
        return cleaned or None
    return to_local(parsed).replace(microsecond=0).isoformat()
