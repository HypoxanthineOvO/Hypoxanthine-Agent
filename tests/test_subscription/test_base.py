from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from hypo_agent.skills.subscription.base import FetchResult, NormalizedItem


def test_normalized_item_builds_stable_content_hash() -> None:
    item = NormalizedItem.from_payload(
        platform="bilibili",
        subscription_id="sub-1",
        item_id="video-1",
        item_type="video",
        title="test-title",
        summary="test-summary",
        url="https://example.com/video-1",
        author_id="42",
        author_name="author",
        published_at=datetime(2026, 4, 10, 8, 0, tzinfo=UTC),
        raw_payload={"id": "video-1"},
    )

    assert item.content_hash
    assert item.content_hash == item.compute_content_hash()


def test_fetch_result_defaults_to_retryable_success_shape() -> None:
    result = FetchResult(ok=True, items=[])

    assert result.ok is True
    assert result.items == []
    assert result.retryable is True
    assert result.auth_stale is False
    assert result.error_code is None


def test_subscription_files_are_ascii_safe() -> None:
    roots = [
        Path("src/hypo_agent/skills/subscription"),
        Path("tests/test_subscription"),
    ]
    non_ascii_files: list[str] = []
    for root in roots:
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if any(ord(char) > 127 for char in text):
                non_ascii_files.append(path.as_posix())

    assert non_ascii_files == []
