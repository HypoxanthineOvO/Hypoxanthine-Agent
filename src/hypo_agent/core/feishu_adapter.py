from __future__ import annotations

import json
import re

from hypo_agent.core.markdown_plaintext import markdown_to_plaintext
from hypo_agent.core.rich_response import RichResponse

FEISHU_CARD_CHAR_LIMIT = 30000


class FeishuAdapter:
    _INLINE_MARKDOWN_RE = re.compile(
        r"(\*\*.+?\*\*|(?<!\*)\*(?!\s).+?(?<!\s)\*(?!\*)|~~.+?~~|`[^`\n]+`|\[[^\]]+\]\([^)]+\))"
    )

    async def format(self, response: RichResponse) -> list[dict[str, str]]:
        text = str(response.text or "")
        elements = self._markdown_to_elements(text)
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

    def _markdown_to_elements(self, text: str) -> list[dict]:
        # Use card JSON 2.0 markdown rendering for most syntax (inline code, fenced code blocks, lists, etc.).
        # Convert GitHub-style pipe tables into a dedicated table component for better compatibility.
        lines = text.splitlines()
        elements: list[dict] = []
        buffer: list[str] = []

        def flush_markdown() -> None:
            nonlocal buffer
            content = "\n".join(buffer).strip("\n")
            buffer = []
            if content.strip():
                elements.append({"tag": "markdown", "content": content})

        i = 0
        while i < len(lines):
            line = lines[i]
            if self._looks_like_table_header(line, next_line=lines[i + 1] if i + 1 < len(lines) else ""):
                # Consume the full table block.
                flush_markdown()
                header_line = lines[i]
                align_line = lines[i + 1]
                i += 2
                row_lines: list[str] = []
                while i < len(lines) and self._looks_like_table_row(lines[i]):
                    row_lines.append(lines[i])
                    i += 1
                table_el = self._table_element_from_markdown(
                    header_line=header_line,
                    align_line=align_line,
                    row_lines=row_lines,
                )
                if table_el is None:
                    # Fallback: keep original markdown table block.
                    buffer.extend([header_line, align_line, *row_lines])
                else:
                    elements.append(table_el)
                continue

            buffer.append(line)
            i += 1

        flush_markdown()
        if not elements:
            elements.append({"tag": "markdown", "content": ""})
        # Drop trailing empty markdown blocks.
        while elements and str(elements[-1].get("tag")) == "markdown" and not str(elements[-1].get("content") or "").strip():
            if len(elements) == 1:
                break
            elements.pop()
        return elements

    def _looks_like_table_header(self, line: str, *, next_line: str) -> bool:
        if "|" not in line:
            return False
        if "|" not in next_line:
            return False
        if not self._TABLE_ALIGN_RE.match(next_line or ""):
            return False
        # Avoid matching fenced code blocks accidentally.
        if line.lstrip().startswith("```") or next_line.lstrip().startswith("```"):
            return False
        return True

    def _looks_like_table_row(self, line: str) -> bool:
        stripped = line.strip()
        if not stripped:
            return False
        if "|" not in stripped:
            return False
        if stripped.startswith("```"):
            return False
        return True

    def _split_table_cells(self, line: str) -> list[str]:
        stripped = line.strip()
        if stripped.startswith("|"):
            stripped = stripped[1:]
        if stripped.endswith("|"):
            stripped = stripped[:-1]
        return [cell.strip() for cell in stripped.split("|")]

    def _table_element_from_markdown(
        self,
        *,
        header_line: str,
        align_line: str,
        row_lines: list[str],
    ) -> dict | None:
        del align_line  # alignment is ignored in basic conversion
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
