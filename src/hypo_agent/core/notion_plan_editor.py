from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import json
from pathlib import Path
import re
from typing import Any

_STRUCTURE_VERSION = 2


@dataclass(slots=True)
class ParsedPlanItem:
    raw_text: str
    title: str
    start_date: date
    end_date: date | None
    start_time: str
    end_time: str
    display_time_range: str
    target_date: date
    sort_key: tuple[int, int, int]
    confidence: float = 1.0
    warnings: list[str] = field(default_factory=list)

    @property
    def display_text(self) -> str:
        return f"{self.display_time_range} {self.title}".strip() if self.display_time_range else self.title

    @property
    def user_text(self) -> str:
        return f"{self.target_date.month}/{self.target_date.day} {self.display_text}".strip()


@dataclass(slots=True)
class PlanParseResult:
    items: list[ParsedPlanItem] = field(default_factory=list)
    failed_items: list[dict[str, str]] = field(default_factory=list)


@dataclass(slots=True)
class PlannedInsertion:
    item: ParsedPlanItem
    target_month_title: str
    target_month_page_id: str
    month_needs_create: bool
    target_date_heading: str
    date_block_id: str
    date_needs_create: bool
    insert_after_block_id: str
    insert_after_title: str
    insert_before_title: str
    existing_titles: list[str] = field(default_factory=list)
    skipped_existing: bool = False

    @property
    def insertion_text(self) -> str:
        if self.skipped_existing:
            return "已存在，跳过重复写入"
        if self.insert_after_title and self.insert_before_title:
            return f"位于 {self.insert_after_title} 与 {self.insert_before_title} 之间"
        if self.insert_after_title:
            return f"位于 {self.insert_after_title} 之后"
        if self.insert_before_title:
            return f"位于 {self.insert_before_title} 之前"
        return "位于当天列表开头"


@dataclass(slots=True)
class PlanPreviewResult:
    planned: list[PlannedInsertion] = field(default_factory=list)
    failed_items: list[dict[str, str]] = field(default_factory=list)

    @property
    def human_summary(self) -> str:
        lines = [
            f"{plan.item.user_text} -> {plan.target_month_title} / {plan.target_date_heading} / {plan.insertion_text}"
            for plan in self.planned
        ]
        lines.extend(
            f"解析失败：{item.get('raw_text') or '-'}（{item.get('reason') or '无法解析'}）"
            for item in self.failed_items
        )
        return "\n".join(lines).strip()


@dataclass(slots=True)
class PlanAddResult:
    planned: list[PlannedInsertion] = field(default_factory=list)
    failed_items: list[dict[str, str]] = field(default_factory=list)
    success_count: int = 0
    skipped_count: int = 0

    @property
    def failure_count(self) -> int:
        return len(self.failed_items)

    @property
    def human_summary(self) -> str:
        sections: list[str] = []
        for plan in self.planned:
            if plan.skipped_existing:
                sections.append(f"计划通已存在，跳过重复：{plan.item.user_text}")
            else:
                sections.append(f"已加入计划通：{plan.item.user_text}")
            sections.append(f"插入位置：\n{plan.target_month_title} / {plan.target_date_heading} / {plan.insertion_text}")
            if plan.existing_titles:
                sections.append("当天日程：\n" + "\n".join(f"- {title}" for title in plan.existing_titles))
        if self.failed_items:
            failures = [
                f"- {item.get('raw_text') or '-'}：{item.get('reason') or '无法解析'}"
                for item in self.failed_items
            ]
            sections.append("失败摘要：\n" + "\n".join(failures))
        return "\n\n".join(sections).strip()


_DATE_PREFIX_RE = re.compile(
    r"^\s*(?:(?P<year>\d{4})[/-])?(?P<month>\d{1,2})(?:[/-]|月)(?P<day>\d{1,2})(?:日)?\s*(?P<rest>.*)$"
)
_TIME_RANGE_RE = re.compile(
    r"^(?P<start>\d{1,2}:\d{2})\s*[-~～]\s*(?:(?P<end_month>\d{1,2})(?:[/-]|月)(?P<end_day>\d{1,2})(?:日)?\s*)?(?P<end>\d{1,2}:\d{2})\s*(?P<title>.+)$"
)
_TIME_RE = re.compile(r"(?P<hour>\d{1,2}):(?P<minute>\d{2})")


def parse_plan_items(text: str, *, default_year: int) -> PlanParseResult:
    result = PlanParseResult()
    for line in _split_item_lines(text):
        if _looks_like_instruction(line):
            continue
        item = _parse_plan_item_line(line, default_year=default_year)
        if item is None:
            result.failed_items.append({"raw_text": line, "reason": "无法识别日期项目"})
            continue
        result.items.append(item)
    return result


def _split_item_lines(text: str) -> list[str]:
    normalized = str(text or "").replace("；", "\n").replace(";", "\n")
    return [line.strip() for line in normalized.splitlines() if line.strip()]


def _looks_like_instruction(line: str) -> bool:
    return "计划通" in line and _DATE_PREFIX_RE.match(line) is None


def _parse_plan_item_line(line: str, *, default_year: int) -> ParsedPlanItem | None:
    match = _DATE_PREFIX_RE.match(line)
    if match is None:
        return None
    year = int(match.group("year") or default_year)
    month = int(match.group("month"))
    day = int(match.group("day"))
    try:
        start_date = date(year, month, day)
    except ValueError:
        return None
    rest = str(match.group("rest") or "").strip()
    if not rest:
        return None

    start_time = ""
    end_time = ""
    end_date: date | None = None
    display_time = ""
    title = rest
    time_match = _TIME_RANGE_RE.match(rest)
    if time_match is not None:
        start_time = _normalize_time(str(time_match.group("start") or ""))
        end_time = _normalize_time(str(time_match.group("end") or ""))
        end_month = time_match.group("end_month")
        end_day = time_match.group("end_day")
        if end_month and end_day:
            end_date = date(year, int(end_month), int(end_day))
            display_time = f"{start_time}-{int(end_month)}/{int(end_day)} {end_time}"
        else:
            end_date = start_date
            display_time = f"{start_time}-{end_time}"
        title = str(time_match.group("title") or "").strip()
    if not title:
        return None
    return ParsedPlanItem(
        raw_text=line,
        title=title,
        start_date=start_date,
        end_date=end_date,
        start_time=start_time,
        end_time=end_time,
        display_time_range=display_time,
        target_date=start_date,
        sort_key=_sort_key(start_time, index_hint=0),
    )


def _normalize_time(value: str) -> str:
    match = _TIME_RE.match(str(value or "").strip())
    if match is None:
        return str(value or "").strip()
    return f"{int(match.group('hour')):02d}:{int(match.group('minute')):02d}"


def _sort_key(time_text: str, *, index_hint: int) -> tuple[int, int, int]:
    match = _TIME_RE.match(str(time_text or ""))
    if match is None:
        return (24 * 60 + 1, index_hint, 0)
    return (int(match.group("hour")) * 60 + int(match.group("minute")), index_hint, 0)


class NotionPlanEditor:
    def __init__(
        self,
        *,
        notion_client: Any,
        plan_page_id: str,
        default_year: int,
        semester_title: str = "",
        structure: dict[str, Any] | None = None,
        structure_path: Path | str | None = None,
    ) -> None:
        self._client = notion_client
        self.plan_page_id = str(plan_page_id or "").strip()
        self.default_year = int(default_year)
        self.semester_title = str(semester_title or "").strip()
        self.structure_path = Path(structure_path) if structure_path is not None else None
        self.structure = dict(structure or self._load_structure())
        self._written_keys: set[tuple[str, str]] = set()

    async def preview_add_items(self, items: list[ParsedPlanItem]) -> PlanPreviewResult:
        for index, item in enumerate(items):
            item.sort_key = _sort_key(item.start_time, index_hint=index)
        planned = [await self._plan_insertion(item) for item in sorted(items, key=lambda entry: entry.sort_key)]
        return PlanPreviewResult(planned=planned)

    async def add_items(self, parse_result: PlanParseResult) -> PlanAddResult:
        preview = await self.preview_add_items(parse_result.items)
        output = PlanAddResult(failed_items=list(parse_result.failed_items))
        for plan in preview.planned:
            if plan.skipped_existing:
                output.skipped_count += 1
                output.planned.append(plan)
                continue
            try:
                if plan.month_needs_create:
                    created = await self._create_month_page(plan.item.target_date)
                    plan.target_month_page_id = str(created.get("id") or plan.target_month_page_id)
                    plan.month_needs_create = False
                if plan.date_needs_create:
                    await self._client.append_blocks(
                        plan.target_month_page_id,
                        [_heading_block(plan.target_date_heading)],
                        after=plan.insert_after_block_id or None,
                    )
                await self._client.append_blocks(
                    plan.target_month_page_id,
                    [_todo_block(plan.item.display_text)],
                    after=plan.insert_after_block_id or None,
                )
                self._written_keys.add((plan.item.target_date.isoformat(), _normalize_item_text(plan.item.display_text)))
                output.success_count += 1
                output.planned.append(plan)
            except Exception as exc:  # noqa: BLE001
                output.failed_items.append({"raw_text": plan.item.raw_text, "reason": str(exc)})
        return output

    async def discover_structure(self) -> dict[str, Any]:
        plan_blocks = await self._client.get_page_content(self.plan_page_id)
        anchors = self._academic_anchors()
        month_pages = []
        current_semester = ""
        current_start: date | None = None
        for block in plan_blocks:
            block_type = str(block.get("type") or "")
            if block_type.startswith("heading_"):
                text = _block_text(block)
                start = _academic_term_start(text, anchors)
                if start is not None:
                    current_semester = text
                    current_start = start
                continue
            if block_type != "child_page":
                continue
            page_id = str(block.get("id") or "").strip()
            title = str((block.get("child_page") or {}).get("title") or "").strip()
            child_start = _academic_term_start(title, anchors)
            if child_start is not None and page_id:
                month_pages.extend(
                    await self._discover_month_pages_in_semester(
                        page_id,
                        semester=title,
                        semester_start=child_start,
                        order_offset=len(month_pages),
                    )
                )
                continue
            parsed = _parse_month_title(title, default_year=self.default_year, term_start=current_start)
            if parsed is None:
                continue
            month_pages.append(
                {
                    "title": title,
                    "page_id": page_id,
                    "year": parsed[0],
                    "month": parsed[1],
                    "semester": current_semester,
                    "semester_start": _year_month_text(current_start),
                    "order": len(month_pages),
                }
            )
        self.structure.update(
            {
                "structure_version": _STRUCTURE_VERSION,
                "plan_page_id": self.plan_page_id,
                "month_pages": month_pages,
                "date_heading_format": self.structure.get("date_heading_format") or "{month}月{day}日",
                "academic_anchors": anchors,
            }
        )
        return dict(self.structure)

    def write_knowledge(self, knowledge_dir: Path | str) -> dict[str, Path]:
        target = Path(knowledge_dir)
        target.mkdir(parents=True, exist_ok=True)
        json_path = target / "structure.json"
        md_path = target / "structure.md"
        json_path.write_text(json.dumps(self.structure, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        lines = [
            "# Notion Plan Structure",
            "",
            f"- plan_page_id: {self.structure.get('plan_page_id') or self.plan_page_id}",
            f"- structure_version: {self.structure.get('structure_version') or _STRUCTURE_VERSION}",
            f"- date_heading_format: {self.structure.get('date_heading_format') or '{month}月{day}日'}",
            "- academic_anchors:",
        ]
        anchors = self.structure.get("academic_anchors") if isinstance(self.structure.get("academic_anchors"), dict) else {}
        lines.extend(f"  - {key}: {value}" for key, value in anchors.items())
        lines.extend(["", "## Month Pages", ""])
        month_lines = [
            f"- {item.get('year')}-{int(item.get('month')):02d}: {item.get('title')}"
            f"{_semester_suffix(item)} ({item.get('page_id')})"
            for item in self.structure.get("month_pages", [])
            if isinstance(item, dict) and item.get("month")
        ]
        lines.extend(month_lines or ["- 暂无发现"])
        md_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
        return {"json": json_path, "markdown": md_path}

    async def _plan_insertion(self, item: ParsedPlanItem) -> PlannedInsertion:
        month_info = self._month_info(item.target_date)
        month_page_id = str(month_info.get("page_id") or "")
        month_title = str(month_info.get("title") or f"{item.target_date.year}年{item.target_date.month}月")
        blocks = await self._client.get_page_content(month_page_id) if month_page_id else []
        date_block_id, day_blocks = _blocks_under_date_heading(blocks, item.target_date)
        existing = [_block_text(block) for block in day_blocks if _block_text(block)]
        normalized_key = (item.target_date.isoformat(), _normalize_item_text(item.display_text))
        after_id, after_title, before_title = _insert_position(day_blocks, item)
        date_needs_create = not date_block_id
        if date_needs_create:
            after_id = str(blocks[-1].get("id") or "") if blocks else ""
            after_title = ""
            before_title = ""
        elif not after_id:
            after_id = date_block_id
        return PlannedInsertion(
            item=item,
            target_month_title=month_title,
            target_month_page_id=month_page_id,
            month_needs_create=not month_page_id,
            target_date_heading=self._date_heading(item.target_date),
            date_block_id=date_block_id,
            date_needs_create=date_needs_create,
            insert_after_block_id=after_id,
            insert_after_title=after_title,
            insert_before_title=before_title,
            existing_titles=existing,
            skipped_existing=normalized_key in self._written_keys
            or any(_normalize_item_text(title) == normalized_key[1] for title in existing),
        )

    async def _create_month_page(self, target: date) -> dict[str, Any]:
        create = getattr(self._client, "create_child_page", None)
        if callable(create):
            return await create(self.plan_page_id, f"{target.year}年{target.month}月")
        return {}

    def _month_info(self, target: date) -> dict[str, Any]:
        candidates: list[dict[str, Any]] = []
        for item in self.structure.get("month_pages", []) or []:
            if isinstance(item, dict) and int(item.get("year") or 0) == target.year and int(item.get("month") or 0) == target.month:
                candidates.append(dict(item))
        if self.semester_title:
            for item in reversed(candidates):
                if _norm(str(item.get("semester") or "")) == _norm(self.semester_title):
                    return item
        if candidates:
            return candidates[-1]
        return {"title": f"{target.year}年{target.month}月", "page_id": ""}

    async def _discover_month_pages_in_semester(
        self,
        page_id: str,
        *,
        semester: str,
        semester_start: date,
        order_offset: int,
    ) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for block in await self._client.get_page_content(page_id):
            if str(block.get("type") or "") != "child_page":
                continue
            title = str((block.get("child_page") or {}).get("title") or "").strip()
            parsed = _parse_month_title(title, default_year=self.default_year, term_start=semester_start)
            if parsed is None:
                continue
            output.append(
                {
                    "title": title,
                    "page_id": str(block.get("id") or "").strip(),
                    "year": parsed[0],
                    "month": parsed[1],
                    "semester": semester,
                    "semester_start": _year_month_text(semester_start),
                    "order": order_offset + len(output),
                }
            )
        return output

    def _academic_anchors(self) -> dict[str, str]:
        anchors = self.structure.get("academic_anchors") if isinstance(self.structure.get("academic_anchors"), dict) else {}
        merged = {"大一上": "2021-09", "研一上": "2025-09"}
        merged.update({str(key): str(value) for key, value in anchors.items()})
        return merged

    def _date_heading(self, target: date) -> str:
        return str(self.structure.get("date_heading_format") or "{month}月{day}日").format(
            year=target.year, month=target.month, day=target.day
        )

    def _load_structure(self) -> dict[str, Any]:
        if self.structure_path is None or not self.structure_path.exists():
            return {"month_pages": [], "date_heading_format": "{month}月{day}日", "academic_anchors": {"大一上": "2021-09", "研一上": "2025-09"}}
        payload = json.loads(self.structure_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return {}
        if int(payload.get("structure_version") or 0) < _STRUCTURE_VERSION:
            return {
                "month_pages": [],
                "date_heading_format": payload.get("date_heading_format") or "{month}月{day}日",
                "academic_anchors": payload.get("academic_anchors") or {"大一上": "2021-09", "研一上": "2025-09"},
            }
        return payload


def _parse_month_title(title: str, *, default_year: int, term_start: date | None = None) -> tuple[int, int] | None:
    match = re.search(r"(?:(?P<year>\d{4})年)?(?P<month>\d{1,2})月", title)
    if match is not None:
        month = int(match.group("month"))
        if match.group("year"):
            return int(match.group("year")), month
        return _month_year(month, default_year=default_year, term_start=term_start), month
    for token, month in {"一月": 1, "二月": 2, "三月": 3, "四月": 4, "五月": 5, "六月": 6, "七月": 7, "八月": 8, "九月": 9, "十月": 10, "十一月": 11, "十二月": 12}.items():
        if token in title:
            return _month_year(month, default_year=default_year, term_start=term_start), month
    return None


def _blocks_under_date_heading(blocks: list[dict[str, Any]], target: date) -> tuple[str, list[dict[str, Any]]]:
    collecting = False
    heading_id = ""
    output: list[dict[str, Any]] = []
    for block in blocks:
        block_type = str(block.get("type") or "")
        if block_type.startswith("heading_"):
            if _matches_date_heading(_block_text(block), target):
                collecting = True
                heading_id = str(block.get("id") or "").strip()
                continue
            if collecting:
                break
        elif collecting:
            output.append(block)
    return heading_id, output


def _matches_date_heading(text: str, target: date) -> bool:
    compact = "".join(str(text or "").split()).casefold()
    return any(
        "".join(candidate.split()).casefold() in compact
        for candidate in {f"{target.month}月{target.day}日", f"{target.month}/{target.day}", f"{target.month}-{target.day}", target.isoformat()}
    )


def _month_year(month: int, *, default_year: int, term_start: date | None) -> int:
    if term_start is None:
        return default_year
    if term_start.month >= 9 and month < term_start.month:
        return term_start.year + 1
    return term_start.year


def _year_month_text(value: date | None) -> str:
    if value is None:
        return ""
    return f"{value.year:04d}-{value.month:02d}"


def _semester_suffix(item: dict[str, Any]) -> str:
    semester = str(item.get("semester") or "").strip()
    return f" [{semester}]" if semester else ""


def _academic_term_start(title: str, anchors: dict[str, str]) -> date | None:
    parsed = _parse_academic_term(title)
    if parsed is None:
        return None
    target_track, target_year, target_half = parsed
    for anchor_title, anchor_month in anchors.items():
        anchor = _parse_academic_term(anchor_title)
        if anchor is None:
            continue
        anchor_track, anchor_year, anchor_half = anchor
        if anchor_track != target_track:
            continue
        match = re.match(r"^(?P<year>\d{4})-(?P<month>\d{1,2})$", str(anchor_month or "").strip())
        if match is None:
            continue
        year = int(match.group("year"))
        month = int(match.group("month"))
        anchor_base_year = year - (1 if anchor_half == "下" and month <= 8 else 0)
        base_year = anchor_base_year + (target_year - anchor_year)
        if target_half == "上":
            return date(base_year, 9, 1)
        return date(base_year + 1, 3, 1)
    return None


def _parse_academic_term(title: str) -> tuple[str, int, str] | None:
    match = re.search(r"(?P<track>[大研])(?P<year>[一二三四五六七八九十]+)(?P<half>[上下])", str(title or ""))
    if match is None:
        return None
    year = _chinese_number(match.group("year"))
    if year <= 0:
        return None
    return match.group("track"), year, match.group("half")


def _chinese_number(text: str) -> int:
    digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    value = str(text or "").strip()
    if value == "十":
        return 10
    if value.startswith("十"):
        return 10 + digits.get(value[-1], 0)
    if "十" in value:
        left, right = value.split("十", 1)
        return digits.get(left, 0) * 10 + (digits.get(right, 0) if right else 0)
    return digits.get(value, 0)


def _insert_position(blocks: list[dict[str, Any]], item: ParsedPlanItem) -> tuple[str, str, str]:
    previous_id = ""
    previous_title = ""
    item_sort = _sort_key(item.start_time, index_hint=0)[0]
    for block in blocks:
        title = _block_text(block)
        if not title:
            continue
        if item_sort < _time_sort_from_text(title):
            return previous_id, previous_title, title
        previous_id = str(block.get("id") or "")
        previous_title = title
    return previous_id, previous_title, ""


def _time_sort_from_text(text: str) -> int:
    match = _TIME_RE.search(text)
    if match is None:
        return 24 * 60 + 1
    return int(match.group("hour")) * 60 + int(match.group("minute"))


def _block_text(block: dict[str, Any]) -> str:
    block_type = str(block.get("type") or "")
    payload = block.get(block_type) if isinstance(block.get(block_type), dict) else {}
    if block_type == "child_page":
        return str(payload.get("title") or "").strip()
    parts = []
    for item in payload.get("rich_text") or []:
        if isinstance(item, dict):
            text_payload = item.get("text") if isinstance(item.get("text"), dict) else {}
            parts.append(str(item.get("plain_text") or text_payload.get("content") or ""))
    return "".join(parts).strip()


def _todo_block(text: str) -> dict[str, Any]:
    return {"object": "block", "type": "to_do", "to_do": {"checked": False, "rich_text": [_rich_text(text)]}}


def _heading_block(text: str) -> dict[str, Any]:
    return {"object": "block", "type": "heading_1", "heading_1": {"rich_text": [_rich_text(text)]}}


def _rich_text(text: str) -> dict[str, Any]:
    return {"type": "text", "text": {"content": text}, "plain_text": text, "annotations": {}}


def _normalize_item_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).casefold()


def contains_plan_item(text: str) -> bool:
    return any(_parse_plan_item_line(line, default_year=2026) is not None for line in _split_item_lines(text))
