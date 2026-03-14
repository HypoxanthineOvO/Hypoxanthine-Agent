from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from hypo_agent.memory.email_store import EmailStore


def test_email_store_upsert_and_cache_body(tmp_path: Path) -> None:
    store = EmailStore(root=tmp_path / "emails")
    now = datetime.now(UTC).isoformat()

    store.upsert(
        {
            "message_id": "<msg-1@example.com>",
            "subject": "续费提醒",
            "from": "billing@example.com",
            "to": "hyx@example.com",
            "date": now,
            "snippet": "请在今天前完成续费",
            "labels": ["important"],
            "cached_at": now,
            "has_body": False,
        }
    )
    store.upsert_body("<msg-1@example.com>", "这是完整正文，里面有续费链接")

    recent = store.list_recent(hours_back=24, limit=10)

    assert len(recent) == 1
    assert recent[0]["message_id"] == "<msg-1@example.com>"
    assert recent[0]["has_body"] is True
    assert store.get_body("<msg-1@example.com>") == "这是完整正文，里面有续费链接"


def test_email_store_search_local_prefers_subject_over_body(tmp_path: Path) -> None:
    store = EmailStore(root=tmp_path / "emails")
    now = datetime.now(UTC)
    earlier = (now - timedelta(hours=1)).isoformat()

    store.upsert(
        {
            "message_id": "<msg-subject@example.com>",
            "subject": "乒乓球续费提醒",
            "from": "club@example.com",
            "to": "hyx@example.com",
            "date": earlier,
            "snippet": "本周社团通知",
            "labels": ["important"],
            "cached_at": earlier,
            "has_body": False,
        }
    )
    store.upsert(
        {
            "message_id": "<msg-body@example.com>",
            "subject": "普通社团通知",
            "from": "club@example.com",
            "to": "hyx@example.com",
            "date": now.isoformat(),
            "snippet": "无关键词",
            "labels": ["low_priority"],
            "cached_at": now.isoformat(),
            "has_body": False,
        }
    )
    store.upsert_body("<msg-body@example.com>", "正文里提到了乒乓球续费安排")

    results = store.search_local("乒乓球 续费", limit=10)

    assert [item["message_id"] for item in results[:2]] == [
        "<msg-subject@example.com>",
        "<msg-body@example.com>",
    ]


def test_email_store_cleanup_enforces_retention_and_max_entries(tmp_path: Path) -> None:
    store = EmailStore(root=tmp_path / "emails", max_entries=2, retention_days=30)
    now = datetime.now(UTC)

    store.upsert(
        {
            "message_id": "<old@example.com>",
            "subject": "过期邮件",
            "from": "old@example.com",
            "to": "hyx@example.com",
            "date": (now - timedelta(days=120)).isoformat(),
            "snippet": "旧内容",
            "labels": [],
            "cached_at": (now - timedelta(days=120)).isoformat(),
            "has_body": False,
        }
    )
    for idx in range(3):
        ts = now - timedelta(hours=idx)
        store.upsert(
            {
                "message_id": f"<recent-{idx}@example.com>",
                "subject": f"最近邮件 {idx}",
                "from": "recent@example.com",
                "to": "hyx@example.com",
                "date": ts.isoformat(),
                "snippet": "最近内容",
                "labels": [],
                "cached_at": ts.isoformat(),
                "has_body": False,
            }
        )

    stats = store.cleanup()
    remaining = store.list_recent(hours_back=24 * 365, limit=10)

    assert stats["removed_expired"] == 0
    assert stats["removed_overflow"] == 0
    assert [item["message_id"] for item in remaining] == [
        "<recent-0@example.com>",
        "<recent-1@example.com>",
    ]
