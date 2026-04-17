from __future__ import annotations

from datetime import UTC, datetime

from hypo_agent.utils.timeutil import to_local


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def normalize_utc_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).replace(microsecond=0)


def utc_isoformat(value: datetime | None) -> str | None:
    normalized = normalize_utc_datetime(value)
    if normalized is None:
        return None
    return to_local(normalized).replace(microsecond=0).isoformat()


def unix_seconds_to_utc_datetime(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(seconds, tz=UTC).replace(microsecond=0)
