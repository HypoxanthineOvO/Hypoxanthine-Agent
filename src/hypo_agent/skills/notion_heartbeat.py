from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Callable


class NotionTodoHeartbeatSource:
    def __init__(
        self,
        *,
        notion_client: Any,
        todo_database_id: str,
        now_fn: Callable[[], datetime],
        row_normalizer: Callable[[list[dict[str, Any]]], Any],
        today_matcher: Callable[..., bool],
        display_title_getter: Callable[[dict[str, Any]], str],
        today_match_mode_getter: Callable[[], str] | None = None,
    ) -> None:
        self._client = notion_client
        self._todo_database_id = str(todo_database_id or "").strip()
        self._now_fn = now_fn
        self._row_normalizer = row_normalizer
        self._today_matcher = today_matcher
        self._display_title_getter = display_title_getter
        self._today_match_mode_getter = today_match_mode_getter

    async def collect(self) -> dict[str, Any] | None:
        database_id = self._todo_database_id
        if not database_id:
            return None

        tz_cst = timezone(timedelta(hours=8))
        now_cst = self._now_fn().astimezone(tz_cst)
        rows = await self._client.query_database(database_id, filter=None, sorts=None, page_size=50)
        normalized_rows = self._row_normalizer(rows)
        if asyncio.iscoroutine(normalized_rows):
            normalized_rows = await normalized_rows
        match_mode = "cover_today"
        if self._today_match_mode_getter is not None:
            match_mode = str(self._today_match_mode_getter() or "").strip() or match_mode
        items: list[dict[str, str]] = []
        for row in normalized_rows:
            if not isinstance(row, dict) or bool(row.get("done")):
                continue
            if not self._today_matcher(row, today=now_cst.date(), match_mode=match_mode):
                continue
            title = self._display_title_getter(row) or str(row.get("id") or "")
            items.append({"title": f"{title}（今日相关）"})
        if not items:
            return None
        return {"items": items}
