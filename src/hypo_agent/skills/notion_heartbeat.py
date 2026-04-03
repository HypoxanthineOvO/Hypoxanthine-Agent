from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable


class NotionTodoHeartbeatSource:
    def __init__(
        self,
        *,
        notion_client: Any,
        todo_database_id: str,
        now_fn: Callable[[], datetime],
        title_getter: Callable[[dict[str, Any]], str],
    ) -> None:
        self._client = notion_client
        self._todo_database_id = str(todo_database_id or "").strip()
        self._now_fn = now_fn
        self._title_getter = title_getter

    async def collect(self) -> dict[str, Any] | None:
        database_id = self._todo_database_id
        if not database_id:
            return None

        tz_cst = timezone(timedelta(hours=8))
        now_cst = self._now_fn().astimezone(tz_cst)
        today_start = now_cst.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        tomorrow_start = (
            now_cst.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        ).isoformat()
        notion_filter = {
            "and": [
                {"property": "日期", "date": {"on_or_after": today_start}},
                {"property": "日期", "date": {"before": tomorrow_start}},
                {"property": "已完成", "checkbox": {"equals": False}},
            ]
        }
        rows = await self._client.query_database(database_id, filter=notion_filter, page_size=50)
        items: list[dict[str, str]] = []
        for row in rows:
            title = self._title_getter(row) or str(row.get("id") or "")
            items.append({"title": f"{title}（截止今天）"})
        if not items:
            return None
        return {"items": items}
