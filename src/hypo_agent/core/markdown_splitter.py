from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any

_FENCE_OPEN_RE = re.compile(r"^```(?P<lang>[a-zA-Z0-9_+-]*)[^\n]*$")
_TABLE_DIVIDER_CELL_RE = re.compile(r"^:?-{3,}:?$")
_STANDALONE_IMAGE_RE = re.compile(r"^\s*!\[[^\]]*\]\([^)]+\)\s*$")
_HORIZONTAL_RULE_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")


class BlockType(Enum):
    TEXT = "text"
    CODE_BLOCK = "code_block"
    TABLE = "table"
    MATH_BLOCK = "math_block"
    MATH_INLINE = "math_inline"
    MERMAID = "mermaid"
    IMAGE = "image"
    HORIZONTAL_RULE = "hr"


@dataclass(slots=True)
class MarkdownBlock:
    type: BlockType
    content: str
    language: str | None = None


def split_markdown(text: str) -> list[MarkdownBlock]:
    if not text:
        return []

    lines = text.splitlines(keepends=True)
    blocks: list[MarkdownBlock] = []
    text_buffer: list[str] = []
    index = 0

    def flush_text() -> None:
        if not text_buffer:
            return
        content = "".join(text_buffer)
        if blocks and blocks[-1].type is BlockType.TEXT:
            blocks[-1].content += content
        else:
            blocks.append(MarkdownBlock(type=BlockType.TEXT, content=content))
        text_buffer.clear()

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        fence_match = _FENCE_OPEN_RE.match(stripped)
        if fence_match:
            flush_text()
            language = str(fence_match.group("lang") or "").strip().lower() or None
            block_lines = [line]
            index += 1
            while index < len(lines):
                block_lines.append(lines[index])
                if lines[index].strip() == "```":
                    index += 1
                    break
                index += 1
            block_type = BlockType.MERMAID if language == "mermaid" else BlockType.CODE_BLOCK
            blocks.append(
                MarkdownBlock(
                    type=block_type,
                    content="".join(block_lines),
                    language=language,
                )
            )
            continue

        math_block, next_index = _read_math_block(lines, index)
        if math_block is not None:
            flush_text()
            blocks.append(math_block)
            index = next_index
            continue

        table_block, next_index = _read_table_block(lines, index)
        if table_block is not None:
            flush_text()
            blocks.append(table_block)
            index = next_index
            continue

        if _STANDALONE_IMAGE_RE.match(stripped):
            flush_text()
            blocks.append(MarkdownBlock(type=BlockType.IMAGE, content=line))
            index += 1
            continue

        if _HORIZONTAL_RULE_RE.match(stripped):
            flush_text()
            blocks.append(MarkdownBlock(type=BlockType.HORIZONTAL_RULE, content=line))
            index += 1
            continue

        text_buffer.append(line)
        index += 1

    flush_text()
    return blocks


def split_markdown_blocks(text: str) -> list[dict[str, str]]:
    legacy_type_map = {
        BlockType.TEXT: "text",
        BlockType.CODE_BLOCK: "code",
        BlockType.TABLE: "table",
        BlockType.MATH_BLOCK: "math",
        BlockType.MERMAID: "mermaid",
        BlockType.IMAGE: "image",
        BlockType.HORIZONTAL_RULE: "hr",
    }
    blocks: list[dict[str, str]] = []
    for block in split_markdown(text):
        payload = {
            "type": legacy_type_map.get(block.type, "text"),
            "content": block.content,
        }
        if block.language:
            payload["language"] = block.language
        blocks.append(payload)
    return blocks


def renderable_markdown_block(block: MarkdownBlock | dict[str, Any]) -> str:
    if isinstance(block, MarkdownBlock):
        block_type = block.type.value
        content = block.content
    else:
        block_type = str(block.get("type") or "").strip().lower()
        content = str(block.get("content") or "")

    if block_type in {"code", "code_block", "mermaid"}:
        return _strip_fenced_block(content)
    if block_type in {"math", "math_block"}:
        return content.strip()
    return content.strip("\n")


def _strip_fenced_block(content: str) -> str:
    lines = content.splitlines()
    if not lines:
        return ""
    if lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip("\n")


def _read_math_block(lines: list[str], start: int) -> tuple[MarkdownBlock | None, int]:
    line = lines[start]
    stripped = line.strip()
    if not stripped.startswith("$$"):
        return None, start

    if stripped.count("$$") >= 2 and stripped != "$$":
        return MarkdownBlock(type=BlockType.MATH_BLOCK, content=line), start + 1

    block_lines = [line]
    index = start + 1
    while index < len(lines):
        block_lines.append(lines[index])
        if lines[index].strip() == "$$":
            return (
                MarkdownBlock(type=BlockType.MATH_BLOCK, content="".join(block_lines)),
                index + 1,
            )
        index += 1

    return MarkdownBlock(type=BlockType.MATH_BLOCK, content="".join(block_lines)), index


def _read_table_block(lines: list[str], start: int) -> tuple[MarkdownBlock | None, int]:
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
        if _FENCE_OPEN_RE.match(stripped) or stripped.startswith("$$"):
            break
        if _STANDALONE_IMAGE_RE.match(stripped) or _HORIZONTAL_RULE_RE.match(stripped):
            break
        if not _is_table_row_line(raw_line):
            break
        candidate_lines.append(raw_line)
        index += 1

    return MarkdownBlock(type=BlockType.TABLE, content="".join(candidate_lines)), index


def _is_table_header_line(raw_line: str) -> bool:
    stripped = raw_line.strip()
    if not stripped or "|" not in stripped:
        return False
    return not stripped.startswith("```")


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
    if not stripped or "|" not in stripped:
        return False
    return not _is_table_divider_line(raw_line)
