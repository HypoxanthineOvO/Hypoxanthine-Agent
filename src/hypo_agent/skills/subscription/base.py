from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
from typing import Any, Protocol


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(slots=True)
class NormalizedItem:
    platform: str
    subscription_id: str
    item_id: str
    item_type: str
    title: str
    summary: str
    url: str
    author_id: str
    author_name: str
    published_at: datetime | None
    raw_payload: dict[str, Any]
    content_hash: str = field(default="")

    def __post_init__(self) -> None:
        if not self.content_hash:
            self.content_hash = self.compute_content_hash()

    def compute_content_hash(self) -> str:
        payload = {
            "platform": self.platform,
            "subscription_id": self.subscription_id,
            "item_id": self.item_id,
            "item_type": self.item_type,
            "title": self.title,
            "summary": self.summary,
            "url": self.url,
            "author_id": self.author_id,
            "author_name": self.author_name,
            "published_at": self.published_at.isoformat() if self.published_at else None,
        }
        return hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()

    @classmethod
    def from_payload(
        cls,
        *,
        platform: str,
        subscription_id: str,
        item_id: str,
        item_type: str,
        title: str,
        summary: str,
        url: str,
        author_id: str,
        author_name: str,
        published_at: datetime | None,
        raw_payload: dict[str, Any],
        content_hash: str = "",
    ) -> "NormalizedItem":
        return cls(
            platform=platform,
            subscription_id=subscription_id,
            item_id=str(item_id),
            item_type=str(item_type),
            title=str(title),
            summary=str(summary),
            url=str(url),
            author_id=str(author_id),
            author_name=str(author_name),
            published_at=published_at,
            raw_payload=dict(raw_payload),
            content_hash=str(content_hash),
        )


@dataclass(slots=True)
class FetchResult:
    ok: bool
    items: list[NormalizedItem]
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = True
    auth_stale: bool = False


class BaseFetcher(Protocol):
    platform: str

    async def fetch_latest(self, subscription: dict[str, Any]) -> FetchResult: ...

    def diff(
        self,
        stored_items: list[dict[str, Any]],
        fetched_items: list[NormalizedItem],
    ) -> list[NormalizedItem]: ...

    def format_notification(self, item: NormalizedItem) -> str: ...

    def classify_error(
        self,
        payload: dict[str, Any] | Exception,
    ) -> tuple[str, bool, bool]: ...
