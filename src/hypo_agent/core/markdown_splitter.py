from __future__ import annotations

import re
from typing import Any

_FENCE_OPEN_RE = re.compile(r"^```(?P<lang>[a-zA-Z0-9_+-]*)[^\n]*$")
_TABLE_DIVIDER_RE = re.compile(r"^\|\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?$")


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
    candidate_lines: list[str] = []
    index = start
    while index < len(lines):
        raw_line = lines[index]
        stripped = raw_line.strip()
        if not (stripped.startswith("|") and stripped.endswith("|")):
            break
        candidate_lines.append(raw_line)
        index += 1

    if len(candidate_lines) < 2:
        return None, start

    divider_index = None
    for idx, item in enumerate(candidate_lines):
        if _TABLE_DIVIDER_RE.match(item.strip()):
            divider_index = idx
            break
    if divider_index != 1:
        return None, start

    return "".join(candidate_lines), index
