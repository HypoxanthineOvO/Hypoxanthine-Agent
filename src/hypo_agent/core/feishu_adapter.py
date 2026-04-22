from __future__ import annotations

import json
import re

from hypo_agent.core.channel_adapter import BaseChannelAdapter
from hypo_agent.core.markdown_capability import FEISHU_CAPABILITY
from hypo_agent.core.markdown_splitter import BlockType, MarkdownBlock
from hypo_agent.core.qq_text_renderer import downgrade_headings
from hypo_agent.core.markdown_plaintext import markdown_to_plaintext
from hypo_agent.core.rich_response import RichResponse

FEISHU_CARD_CHAR_LIMIT = 30000


class FeishuAdapter(BaseChannelAdapter):
    _INLINE_MARKDOWN_RE = re.compile(
        r"(\*\*.+?\*\*|(?<!\*)\*(?!\s).+?(?<!\s)\*(?!\*)|~~.+?~~|`[^`\n]+`|\[[^\]]+\]\([^)]+\))"
    )

    def __init__(self) -> None:
        super().__init__(FEISHU_CAPABILITY)

    async def format(self, response: RichResponse) -> list[dict[str, str]]:
        return await super().format(response)

    async def render_blocks(self, blocks: list[MarkdownBlock]) -> list[dict[str, str]]:
        elements = self._blocks_to_elements(blocks)
        cards = self._pack_elements_into_cards(elements)
        return [{"msg_type": "interactive", "content": card} for card in cards]

    def _build_card_v2(self, elements: list[dict]) -> str:
        card = {
            "schema": "2.0",
            "body": {
                "elements": elements,
            },
        }
        return json.dumps(card, ensure_ascii=False, separators=(",", ":"))

    def _pack_elements_into_cards(self, elements: list[dict]) -> list[str]:
        if not elements:
            return [self._build_card_v2([{"tag": "markdown", "content": ""}])]

        cards: list[list[dict]] = []
        current: list[dict] = []
        current_cost = 0

        def flush() -> None:
            nonlocal current, current_cost
            if current:
                cards.append(current)
            current = []
            current_cost = 0

        for element in elements:
            element_cost = self._element_cost(element)
            if current and current_cost + element_cost > FEISHU_CARD_CHAR_LIMIT:
                flush()
            # Single element too big: split markdown content if possible.
            if element_cost > FEISHU_CARD_CHAR_LIMIT and str(element.get("tag")) == "markdown":
                for chunk in self._split_text(str(element.get("content") or "")):
                    chunk_el = {"tag": "markdown", "content": chunk}
                    if current and current_cost + len(chunk) > FEISHU_CARD_CHAR_LIMIT:
                        flush()
                    current.append(chunk_el)
                    current_cost += len(chunk)
                continue
            current.append(element)
            current_cost += element_cost

        flush()
        return [self._build_card_v2(card_elements) for card_elements in cards]

    def _element_cost(self, element: dict) -> int:
        tag = str(element.get("tag") or "").strip().lower()
        if tag == "markdown":
            return len(str(element.get("content") or ""))
        if tag == "table":
            columns = element.get("columns") if isinstance(element.get("columns"), list) else []
            rows = element.get("rows") if isinstance(element.get("rows"), list) else []
            cost = 0
            for col in columns:
                if isinstance(col, dict):
                    cost += len(str(col.get("display_name") or "")) + len(str(col.get("name") or ""))
            for row in rows:
                if not isinstance(row, dict):
                    continue
                for value in row.values():
                    cost += len(str(value or ""))
            return cost
        return 200

    def _split_text(self, text: str) -> list[str]:
        if not text:
            return [""]
        if len(text) <= FEISHU_CARD_CHAR_LIMIT:
            return [text]

        chunks: list[str] = []
        remaining = text
        while remaining:
            if len(remaining) <= FEISHU_CARD_CHAR_LIMIT:
                chunks.append(remaining)
                break
            candidate = remaining[:FEISHU_CARD_CHAR_LIMIT]
            split_at = candidate.rfind("\n")
            if split_at <= 0:
                split_at = FEISHU_CARD_CHAR_LIMIT
            else:
                split_at += 1
            chunks.append(remaining[:split_at])
            remaining = remaining[split_at:]
        return chunks

    _TABLE_ALIGN_RE = re.compile(r"^\s*\|?\s*:?[-]{3,}:?\s*(\|\s*:?[-]{3,}:?\s*)+\|?\s*$")

    def _blocks_to_elements(self, blocks: list[MarkdownBlock]) -> list[dict]:
        elements: list[dict] = []
        buffer: list[str] = []

        def flush_markdown() -> None:
            nonlocal buffer
            content = "\n".join(buffer).strip("\n")
            buffer = []
            if content.strip():
                elements.append({"tag": "markdown", "content": content})

        for block in blocks:
            if block.type is BlockType.TABLE:
                flush_markdown()
                table_el = self._table_element_from_block(block.content)
                if table_el is None:
                    buffer.append(block.content.rstrip("\n"))
                else:
                    elements.append(table_el)
                continue
            content = block.content
            if block.type is BlockType.TEXT:
                content = downgrade_headings(content, max_level=self.capability.heading_max_level)
            buffer.append(content.rstrip("\n"))

        flush_markdown()
        if not elements:
            elements.append({"tag": "markdown", "content": ""})
        # Drop trailing empty markdown blocks.
        while elements and str(elements[-1].get("tag")) == "markdown" and not str(elements[-1].get("content") or "").strip():
            if len(elements) == 1:
                break
            elements.pop()
        return elements

    def _split_table_cells(self, line: str) -> list[str]:
        stripped = line.strip()
        if stripped.startswith("|"):
            stripped = stripped[1:]
        if stripped.endswith("|"):
            stripped = stripped[:-1]
        return [cell.strip() for cell in stripped.split("|")]

    def _table_element_from_block(self, markdown: str) -> dict | None:
        lines = [line for line in str(markdown or "").splitlines() if line.strip()]
        if len(lines) < 2:
            return None
        header_line = lines[0]
        align_line = lines[1]
        if not self._TABLE_ALIGN_RE.match(align_line):
            return None
        row_lines = lines[2:]
        headers = self._split_table_cells(header_line)
        if not headers:
            return None
        # Limit to avoid huge cards; fall back to markdown if too wide.
        if len(headers) > 12:
            return None
        rows: list[list[str]] = [self._split_table_cells(line) for line in row_lines]
        if any(len(row) > len(headers) + 2 for row in rows):
            return None
        normalized_headers = [
            self._sanitize_table_header(h) or f"col_{idx+1}" for idx, h in enumerate(headers)
        ]
        column_types = [
            "lark_md" if self._column_contains_markdown(rows, idx) else "text"
            for idx in range(len(headers))
        ]
        columns = [
            {
                "name": f"c{idx}",
                "display_name": name,
                "data_type": column_types[idx],
            }
            for idx, name in enumerate(normalized_headers)
        ]
        row_objects: list[dict[str, str]] = []
        for row in rows:
            values = list(row) + [""] * max(0, len(headers) - len(row))
            row_objects.append(
                {
                    f"c{idx}": self._render_table_cell(values[idx], data_type=column_types[idx])
                    for idx in range(len(headers))
                }
            )
        return {
            "tag": "table",
            "page_size": 10,
            "columns": columns,
            "rows": row_objects,
        }

    def _column_contains_markdown(self, rows: list[list[str]], index: int) -> bool:
        for row in rows:
            if index < len(row) and self._contains_inline_markdown(row[index]):
                return True
        return False

    def _contains_inline_markdown(self, value: str) -> bool:
        return bool(self._INLINE_MARKDOWN_RE.search(str(value or "")))

    def _sanitize_table_header(self, value: str) -> str:
        return markdown_to_plaintext(str(value or "")).strip()

    def _render_table_cell(self, value: str, *, data_type: str) -> str:
        text = str(value or "")
        if data_type == "lark_md":
            return text
        return markdown_to_plaintext(text).strip()
