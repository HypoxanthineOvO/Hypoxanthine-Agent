from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import re
from typing import Any


@dataclass(slots=True)
class PlanItem:
    title: str
    done: bool
    important: bool = False


@dataclass(slots=True)
class PlanSummary:
    total: int
    done_count: int
    undone_count: int
    done_items: list[PlanItem] = field(default_factory=list)
    undone_items: list[PlanItem] = field(default_factory=list)
    important_items: list[PlanItem] = field(default_factory=list)

    @property
    def completion_rate(self) -> str:
        return f"{self.done_count}/{self.total}" if self.total else "0/0"

    def to_payload(self) -> dict[str, Any]:
        return {
            "available": True,
            "total": self.total,
            "done_count": self.done_count,
            "undone_count": self.undone_count,
            "completion_rate": self.completion_rate,
            "done_items": [_item_payload(item) for item in self.done_items],
            "undone_items": [_item_payload(item) for item in self.undone_items],
            "important_items": [_item_payload(item) for item in self.important_items],
            "human_summary": render_plan_summary(self),
        }


def render_plan_summary(summary: PlanSummary) -> str:
    lines = [f"今日计划通完成率：{summary.completion_rate}"]
    if summary.important_items:
        lines.append("")
        lines.append("重要提醒：")
        lines.extend(f"- {item.title}" for item in summary.important_items)
    if summary.undone_items:
        lines.append("")
        lines.append("未完成：")
        lines.extend(f"- {item.title}" for item in summary.undone_items)
    if summary.done_items:
        lines.append("")
        lines.append("已完成：")
        lines.extend(f"- {item.title}" for item in summary.done_items)
    return "\n".join(lines).strip()


def _item_payload(item: PlanItem) -> dict[str, Any]:
    return {"title": item.title, "done": item.done, "important": item.important}


class NotionPlanReader:
    def __init__(
        self,
        *,
        notion_client: Any,
        plan_page_id: str = "",
        root_title: str = "",
        plan_title: str = "HYX的计划通",
        semester_title: str = "",
        max_scan_pages: int = 40,
    ) -> None:
        self._client = notion_client
        self.plan_page_id = str(plan_page_id or "").strip()
        self.root_title = root_title
        self.plan_title = plan_title
        self.semester_title = semester_title
        self.max_scan_pages = max(1, int(max_scan_pages))

    async def read_today(self, *, today: date) -> PlanSummary:
        plan_id = await self._resolve_plan_page_id()
        plan_blocks = await self._client.get_page_content(plan_id)
        semester_id = (
            self._find_child_page_id_in_blocks(plan_blocks, self.semester_title)
            if self.semester_title
            else ""
        )
        if semester_id:
            month_id = await self._find_month_page(semester_id, today, plan_id=plan_id)
        else:
            month_id = self._find_month_page_in_semester_section(plan_blocks, today)
            if not month_id:
                month_id = await self._find_latest_month_page_with_today_heading(
                    plan_blocks,
                    today,
                )
            if not month_id:
                month_id = await self._discover_page_with_today_heading(plan_id, plan_blocks, today)
        blocks = await self._client.get_page_content(month_id)
        today_blocks = self._blocks_under_today_heading(blocks, today)
        items = [item for block in today_blocks if (item := self._item_from_block(block)) is not None]
        done_items = [item for item in items if item.done]
        undone_items = [item for item in items if not item.done]
        important_items = [item for item in items if item.important]
        return PlanSummary(
            total=len(items),
            done_count=len(done_items),
            undone_count=len(undone_items),
            done_items=done_items,
            undone_items=undone_items,
            important_items=important_items,
        )

    async def _resolve_plan_page_id(self) -> str:
        if self.plan_page_id:
            return self.plan_page_id
        if self.root_title:
            root_id = await self._find_root_page_id()
            if root_id:
                child_id = self._find_child_page_id_in_blocks(
                    await self._client.get_page_content(root_id),
                    self.plan_title,
                )
                if child_id:
                    return child_id
        return await self._find_page_id(self.plan_title)

    async def _find_root_page_id(self) -> str:
        if not self.root_title:
            return ""
        rows = await self._client.search(self.root_title, object_type="page", page_size=10)
        for row in rows:
            title = _page_title(row)
            if _norm(title) == _norm(self.root_title):
                return str(row.get("id") or "").strip()
        return ""

    async def _find_page_id(self, title: str) -> str:
        rows = await self._client.search(title, object_type="page", page_size=10)
        for row in rows:
            row_title = _page_title(row)
            if _norm(row_title) == _norm(title):
                return str(row.get("id") or "").strip()
        raise ValueError(f"Notion page not found: {title}")

    async def _find_child_page(self, page_id: str, title: str) -> str:
        child_id = self._find_child_page_id_in_blocks(await self._client.get_page_content(page_id), title)
        if child_id:
            return child_id
        raise ValueError(f"Notion child page not found: {title}")

    async def _find_month_page(self, page_id: str, today: date, *, plan_id: str = "") -> str:
        month_tokens = _month_title_tokens(today)
        blocks = await self._client.get_page_content(page_id)
        for block in blocks:
            if str(block.get("type") or "") != "child_page":
                continue
            child = block.get("child_page") if isinstance(block.get("child_page"), dict) else {}
            title = str(child.get("title") or "")
            if any(token in title for token in month_tokens):
                return str(block.get("id") or "").strip()
        if plan_id:
            return await self._discover_page_with_today_heading(plan_id, await self._client.get_page_content(plan_id), today)
        raise ValueError(self._diagnostic_error("Notion month page not found", scanned_pages=1))

    def _find_child_page_id_in_blocks(self, blocks: list[dict[str, Any]], title: str) -> str:
        for block in blocks:
            if str(block.get("type") or "") != "child_page":
                continue
            child = block.get("child_page") if isinstance(block.get("child_page"), dict) else {}
            if _norm(str(child.get("title") or "")) == _norm(title):
                return str(block.get("id") or "").strip()
        return ""

    def _find_month_page_in_semester_section(self, blocks: list[dict[str, Any]], today: date) -> str:
        if not self.semester_title:
            return ""
        collecting = False
        month_tokens = _month_title_tokens(today)
        for block in blocks:
            block_type = str(block.get("type") or "")
            if block_type.startswith("heading_"):
                text, _ = _rich_text_from_block(block)
                if _norm(text) == _norm(self.semester_title):
                    collecting = True
                    continue
                if collecting:
                    break
            if not collecting or block_type != "child_page":
                continue
            child = block.get("child_page") if isinstance(block.get("child_page"), dict) else {}
            title = str(child.get("title") or "")
            if any(token in title for token in month_tokens):
                return str(block.get("id") or "").strip()
        return ""

    async def _find_latest_month_page_with_today_heading(
        self,
        blocks: list[dict[str, Any]],
        today: date,
    ) -> str:
        candidates = _month_child_page_refs(blocks, today)
        for page_id, _title in reversed(candidates):
            page_blocks = await self._client.get_page_content(page_id)
            if self._blocks_under_today_heading(page_blocks, today):
                return page_id
        return ""

    async def _discover_page_with_today_heading(
        self,
        plan_id: str,
        plan_blocks: list[dict[str, Any]],
        today: date,
    ) -> str:
        sequence = 0
        queue: list[tuple[str, int, tuple[str, ...], int]] = []
        for page_id, title in _child_page_refs(plan_blocks):
            sequence += 1
            queue.append((page_id, 1, (title,), sequence))
        scanned = 0
        seen = {plan_id}
        best: tuple[tuple[int, int], str] | None = None
        while queue and scanned < self.max_scan_pages:
            page_id, depth, path_titles, order = queue.pop(0)
            if page_id in seen:
                continue
            seen.add(page_id)
            scanned += 1
            blocks = await self._client.get_page_content(page_id)
            if self._blocks_under_today_heading(blocks, today):
                archive_penalty = -1 if any(_is_archive_title(title) for title in path_titles) else 0
                score = (archive_penalty, order)
                if best is None or score > best[0]:
                    best = (score, page_id)
            if depth < 4:
                for child_id, title in _child_page_refs(blocks):
                    sequence += 1
                    queue.append((child_id, depth + 1, (*path_titles, title), sequence))
        if best is not None:
            return best[1]
        raise ValueError(
            self._diagnostic_error(
                "Notion today plan section not found",
                scanned_pages=scanned,
            )
        )

    def _diagnostic_error(self, message: str, *, scanned_pages: int) -> str:
        hints = []
        if self.plan_title:
            hints.append(f"plan_title={self.plan_title}")
        if self.root_title:
            hints.append(f"root_title={self.root_title}")
        if self.semester_title:
            hints.append(f"semester_title={self.semester_title}")
        hints.append(f"scanned_pages={scanned_pages}")
        return f"{message} ({', '.join(hints)})"

    def _blocks_under_today_heading(self, blocks: list[dict[str, Any]], today: date) -> list[dict[str, Any]]:
        collecting = False
        output: list[dict[str, Any]] = []
        for block in blocks:
            block_type = str(block.get("type") or "")
            if block_type.startswith("heading_"):
                text, _ = _rich_text_from_block(block)
                if _matches_today_heading(text, today):
                    collecting = True
                    continue
                if collecting:
                    break
            elif collecting:
                output.append(block)
        return output

    def _item_from_block(self, block: dict[str, Any]) -> PlanItem | None:
        block_type = str(block.get("type") or "")
        if block_type not in {"to_do", "bulleted_list_item", "numbered_list_item", "paragraph"}:
            return None
        text, important = _rich_text_from_block(block)
        if not text:
            return None
        payload = block.get(block_type)
        done = bool(payload.get("checked")) if isinstance(payload, dict) and block_type == "to_do" else False
        return PlanItem(title=text, done=done, important=important)


def _rich_text_from_block(block: dict[str, Any]) -> tuple[str, bool]:
    block_type = str(block.get("type") or "")
    payload = block.get(block_type)
    if not isinstance(payload, dict):
        return "", False
    parts: list[str] = []
    important = False
    for item in payload.get("rich_text") or []:
        if not isinstance(item, dict):
            continue
        parts.append(str(item.get("plain_text") or ""))
        annotations = item.get("annotations")
        if isinstance(annotations, dict):
            color = str(annotations.get("color") or "default")
            if bool(annotations.get("bold")) or color not in {"", "default"}:
                important = True
    return "".join(parts).strip(), important


def _page_title(row: dict[str, Any]) -> str:
    props = row.get("properties") if isinstance(row.get("properties"), dict) else {}
    for value in props.values():
        if not isinstance(value, dict):
            continue
        title = value.get("title")
        if not isinstance(title, list):
            continue
        return "".join(str(item.get("plain_text") or "") for item in title if isinstance(item, dict)).strip()
    return ""


def _matches_today_heading(text: str, today: date) -> bool:
    compact = _norm(text)
    patterns = {
        f"{today.month}月{today.day}日",
        f"{today.month}/{today.day}",
        f"{today.month}-{today.day}",
        today.isoformat(),
    }
    normalized_patterns = {_norm(item) for item in patterns}
    return any(pattern in compact for pattern in normalized_patterns) or bool(
        re.search(rf"(^|[^0-9])0?{today.month}月0?{today.day}日", text)
    )


def _month_title_tokens(today: date) -> list[str]:
    chinese_months = {
        1: "一月",
        2: "二月",
        3: "三月",
        4: "四月",
        5: "五月",
        6: "六月",
        7: "七月",
        8: "八月",
        9: "九月",
        10: "十月",
        11: "十一月",
        12: "十二月",
    }
    return [
        f"{today.year}年{today.month}月",
        f"{today.month}月",
        today.strftime("%B"),
        today.strftime("%b"),
        chinese_months[today.month],
    ]


def _child_page_refs(blocks: list[dict[str, Any]]) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    for block in blocks:
        if str(block.get("type") or "") != "child_page":
            continue
        block_id = str(block.get("id") or "").strip()
        child = block.get("child_page") if isinstance(block.get("child_page"), dict) else {}
        title = str(child.get("title") or "").strip()
        if block_id:
            refs.append((block_id, title))
    return refs


def _month_child_page_refs(blocks: list[dict[str, Any]], today: date) -> list[tuple[str, str]]:
    tokens = _month_title_tokens(today)
    return [
        (page_id, title)
        for page_id, title in _child_page_refs(blocks)
        if _title_matches_any_month_token(title, tokens)
    ]


def _title_matches_any_month_token(title: str, tokens: list[str]) -> bool:
    return any(token in title for token in tokens)


def _is_archive_title(title: str) -> bool:
    normalized = _norm(title)
    return "归档" in normalized or "archive" in normalized


def _norm(value: str) -> str:
    return "".join(str(value or "").split()).casefold()
