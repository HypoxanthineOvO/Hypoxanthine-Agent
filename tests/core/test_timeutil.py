from __future__ import annotations

from datetime import UTC, datetime

from hypo_agent.utils.timeutil import LOCAL_TZ, format_local, now_iso, now_local, to_local


def test_timeutil_converts_utc_datetime_to_shanghai() -> None:
    original = datetime(2026, 4, 11, 8, 49, 1, tzinfo=UTC)

    converted = to_local(original)

    assert converted.tzinfo == LOCAL_TZ
    assert converted.isoformat() == "2026-04-11T16:49:01+08:00"


def test_timeutil_treats_naive_datetime_as_utc() -> None:
    original = datetime(2026, 4, 11, 8, 49, 1)

    converted = to_local(original)

    assert converted.tzinfo == LOCAL_TZ
    assert converted.isoformat() == "2026-04-11T16:49:01+08:00"


def test_timeutil_formats_local_time() -> None:
    original = datetime(2026, 4, 11, 8, 49, 1, tzinfo=UTC)

    assert format_local(original) == "2026-04-11 16:49:01"


def test_timeutil_now_helpers_return_shanghai_timezone() -> None:
    current = now_local()

    assert current.tzinfo == LOCAL_TZ
    assert now_iso().endswith("+08:00")
