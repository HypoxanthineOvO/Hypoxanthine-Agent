from __future__ import annotations

import re
from typing import Any

_FENCE_OPEN_RE = re.compile(r"^```(?P<lang>[a-zA-Z0-9_+-]*)[^\n]*$")
_TABLE_DIVIDER_CELL_RE = re.compile(r"^:?-{3,}:?$")


def split_markdown_blocks(text: str) -> list[dict[str, str]]:
    """Split markdown into ordered block-level fragments without losing content."""
    if not text:
        return []

    lines = text.splitlines(keepends=True)
    blocks: list[dict[str, str]] = []
    text_buffer: list[str] = []
    index = 0

    def flush_text() -> None:
        if not text_buffer:
            return
        content = "".join(text_buffer)
        if blocks and blocks[-1]["type"] == "text":
            blocks[-1]["content"] += content
        else:
            blocks.append({"type": "text", "content": content})
        text_buffer.clear()

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        fence_match = _FENCE_OPEN_RE.match(stripped)
        if fence_match:
            flush_text()
            lang = str(fence_match.group("lang") or "").strip().lower()
            block_lines = [line]
            index += 1
            while index < len(lines):
                block_lines.append(lines[index])
                if lines[index].strip() == "```":
                    index += 1
                    break
                index += 1
            block_type = "mermaid" if lang == "mermaid" else "code"
            blocks.append({"type": block_type, "content": "".join(block_lines)})
            continue

        if _is_math_block_line(stripped):
            flush_text()
            blocks.append({"type": "math", "content": line})
            index += 1
            continue

        table_block, consumed = _read_table_block(lines, index)
        if table_block is not None:
            flush_text()
            blocks.append({"type": "table", "content": table_block})
            index = consumed
            continue

        text_buffer.append(line)
        index += 1

    flush_text()
    return blocks


def renderable_markdown_block(block: dict[str, Any]) -> str:
    block_type = str(block.get("type") or "").strip().lower()
    content = str(block.get("content") or "")
    if block_type == "code":
        return _strip_fenced_block(content)
    if block_type == "mermaid":
        return _strip_fenced_block(content)
    if block_type == "math":
        return content.strip()
    return content.strip("\n")


def _strip_fenced_block(content: str) -> str:
    lines = content.splitlines()
    if not lines:
        return ""
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip("\n")


def _is_math_block_line(stripped_line: str) -> bool:
    if not stripped_line.startswith("$$") or not stripped_line.endswith("$$"):
        return False
    if len(stripped_line) < 4:
        return False
    return stripped_line.count("$$") >= 2


def _read_table_block(lines: list[str], start: int) -> tuple[str | None, int]:
    if start + 1 >= len(lines):
        return None, start

    header_line = lines[start]
    divider_line = lines[start + 1]
    if not _is_table_header_line(header_line) or not _is_table_divider_line(divider_line):
        return None, start

    candidate_lines = [header_line, divider_line]
    index = start + 2
    while index < len(lines):
        raw_line = lines[index]
        stripped = raw_line.strip()
        if not stripped:
            break
        if _FENCE_OPEN_RE.match(stripped) or _is_math_block_line(stripped):
            break
        if not _is_table_row_line(raw_line):
            break
        candidate_lines.append(raw_line)
        index += 1

    return "".join(candidate_lines), index


def _is_table_header_line(raw_line: str) -> bool:
    stripped = raw_line.strip()
    return bool(stripped and "|" in stripped)


def _is_table_divider_line(raw_line: str) -> bool:
    stripped = raw_line.strip()
    if not stripped or "|" not in stripped:
        return False
    cells = [cell.strip() for cell in stripped.strip("|").split("|")]
    if len(cells) < 2:
        return False
    return all(cell and _TABLE_DIVIDER_CELL_RE.match(cell) for cell in cells)


def _is_table_row_line(raw_line: str) -> bool:
    stripped = raw_line.strip()
    if not stripped:
        return False
    if "|" not in stripped:
        return False
    return not _is_table_divider_line(raw_line)
