from __future__ import annotations

import re
from typing import Any


_INLINE_TOKEN_RE = re.compile(
    r"(\[([^\]]+)\]\(([^)]+)\)|\*\*([^*]+)\*\*|~~([^~]+)~~|`([^`]+)`|\*([^*]+)\*)"
)
_DETAILS_RE = re.compile(
    r"^<details><summary>(?P<title>.*?)</summary>\n?(?P<body>.*?)\n?</details>$",
    re.DOTALL,
)


def blocks_to_markdown(blocks: list[dict[str, Any]]) -> str:
    rendered = _render_blocks(blocks, indent=0)
    return "\n\n".join(chunk for chunk in rendered if chunk).strip()


def markdown_to_blocks(markdown: str) -> list[dict[str, Any]]:
    lines = str(markdown or "").splitlines()
    blocks: list[dict[str, Any]] = []
    stack: list[tuple[int, dict[str, Any], str]] = []
    index = 0

    while index < len(lines):
        raw_line = lines[index]
        stripped = raw_line.strip()
        if not stripped:
            index += 1
            continue

        if stripped.startswith("```"):
            language = stripped[3:].strip() or "plain text"
            code_lines: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code_lines.append(lines[index])
                index += 1
            block = {
                "object": "block",
                "type": "code",
                "code": {
                    "language": language,
                    "rich_text": _rich_text_objects("\n".join(code_lines)),
                },
            }
            _append_block(blocks, stack, block, indent=0, block_type="code")
            index += 1
            continue

        details_match = _DETAILS_RE.match("\n".join(lines[index:]).strip())
        if details_match is not None:
            body_blocks = markdown_to_blocks(details_match.group("body"))
            block = {
                "object": "block",
                "type": "toggle",
                "toggle": {
                    "rich_text": _rich_text_objects(details_match.group("title")),
                    "children": body_blocks,
                },
            }
            _append_block(blocks, stack, block, indent=0, block_type="toggle")
            break

        if stripped.startswith("|") and stripped.endswith("|"):
            table_lines = [stripped]
            probe = index + 1
            while probe < len(lines):
                candidate = lines[probe].strip()
                if candidate.startswith("|") and candidate.endswith("|"):
                    table_lines.append(candidate)
                    probe += 1
                    continue
                break
            block = _parse_table_block(table_lines)
            _append_block(blocks, stack, block, indent=0, block_type="table")
            index = probe
            continue

        indent = len(raw_line) - len(raw_line.lstrip(" "))
        list_match = re.match(r"^(\s*)([-*] |\d+\. )(.+)$", raw_line)
        todo_match = re.match(r"^(\s*)- \[( |x|X)\] (.+)$", raw_line)
        heading_match = re.match(r"^(#{1,3}) (.+)$", stripped)
        image_match = re.match(r"^!\[(.*?)\]\((.+)\)$", stripped)

        if heading_match is not None:
            level = len(heading_match.group(1))
            key = f"heading_{level}"
            block = {
                "object": "block",
                "type": key,
                key: {"rich_text": _rich_text_objects(heading_match.group(2))},
            }
            _append_block(blocks, stack, block, indent=0, block_type=key)
        elif todo_match is not None:
            block = {
                "object": "block",
                "type": "to_do",
                "to_do": {
                    "checked": todo_match.group(2).lower() == "x",
                    "rich_text": _rich_text_objects(todo_match.group(3)),
                },
            }
            _append_block(blocks, stack, block, indent=indent, block_type="to_do")
        elif list_match is not None:
            marker = list_match.group(2)
            content = list_match.group(3)
            block_type = (
                "numbered_list_item"
                if marker.strip().endswith(".")
                else "bulleted_list_item"
            )
            block = {
                "object": "block",
                "type": block_type,
                block_type: {"rich_text": _rich_text_objects(content)},
            }
            _append_block(blocks, stack, block, indent=indent, block_type=block_type)
        elif stripped.startswith("> "):
            block = {
                "object": "block",
                "type": "quote",
                "quote": {"rich_text": _rich_text_objects(stripped[2:])},
            }
            _append_block(blocks, stack, block, indent=0, block_type="quote")
        elif stripped == "---":
            block = {"object": "block", "type": "divider", "divider": {}}
            _append_block(blocks, stack, block, indent=0, block_type="divider")
        elif image_match is not None:
            caption, url = image_match.groups()
            block = {
                "object": "block",
                "type": "image",
                "image": {
                    "type": "external",
                    "external": {"url": url},
                    "caption": _rich_text_objects(caption) if caption else [],
                },
            }
            _append_block(blocks, stack, block, indent=0, block_type="image")
        else:
            block = {
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": _rich_text_objects(stripped)},
            }
            _append_block(blocks, stack, block, indent=0, block_type="paragraph")

        index += 1

    return blocks


def _append_block(
    blocks: list[dict[str, Any]],
    stack: list[tuple[int, dict[str, Any], str]],
    block: dict[str, Any],
    *,
    indent: int,
    block_type: str,
) -> None:
    while stack and indent <= stack[-1][0]:
        stack.pop()

    if stack and stack[-1][2] in {"bulleted_list_item", "numbered_list_item", "to_do", "toggle"}:
        parent_block = stack[-1][1]
        parent_data = parent_block.get(stack[-1][2], {})
        children = parent_data.setdefault("children", [])
        children.append(block)
    else:
        blocks.append(block)

    if block_type in {"bulleted_list_item", "numbered_list_item", "to_do", "toggle"}:
        stack.append((indent, block, block_type))


def _parse_table_block(lines: list[str]) -> dict[str, Any]:
    rows: list[list[str]] = []
    for line in lines:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        rows.append(cells)
    if len(rows) >= 2 and all(set(cell) <= {"-", ":"} for cell in rows[1]):
        rows.pop(1)
    width = max((len(row) for row in rows), default=0)
    children = []
    for row in rows:
        padded = row + [""] * max(0, width - len(row))
        children.append(
            {
                "object": "block",
                "type": "table_row",
                "table_row": {
                    "cells": [_rich_text_objects(cell) for cell in padded],
                },
            }
        )
    return {
        "object": "block",
        "type": "table",
        "table": {
            "table_width": width,
            "has_column_header": bool(children),
            "has_row_header": False,
            "children": children,
        },
    }


def _render_blocks(blocks: list[dict[str, Any]], *, indent: int) -> list[str]:
    chunks: list[str] = []
    for block in blocks:
        block_type = str(block.get("type") or "")
        payload = block.get(block_type, {}) if isinstance(block.get(block_type), dict) else {}
        children = payload.get("children", []) if isinstance(payload.get("children"), list) else []
        text = _render_rich_text(payload.get("rich_text", []))

        if block_type == "paragraph":
            chunk = text
        elif block_type in {"heading_1", "heading_2", "heading_3"}:
            level = int(block_type[-1])
            chunk = f"{'#' * level} {text}".rstrip()
        elif block_type == "bulleted_list_item":
            chunk = f"{' ' * indent}- {text}".rstrip()
        elif block_type == "numbered_list_item":
            chunk = f"{' ' * indent}1. {text}".rstrip()
        elif block_type == "to_do":
            marker = "x" if bool(payload.get("checked")) else " "
            chunk = f"{' ' * indent}- [{marker}] {text}".rstrip()
        elif block_type == "code":
            language = str(payload.get("language") or "").strip() or "plain text"
            chunk = f"```{language}\n{text}\n```"
        elif block_type == "quote":
            chunk = f"> {text}".rstrip()
        elif block_type == "divider":
            chunk = "---"
        elif block_type == "image":
            url = _extract_file_url(payload)
            caption = _render_rich_text(payload.get("caption", []))
            chunk = f"![{caption}]({url})" if url else f"![{caption}]()"
        elif block_type == "table":
            chunk = _render_table(payload)
        elif block_type == "toggle":
            body = blocks_to_markdown(children)
            chunk = f"<details><summary>{text}</summary>\n{body}\n</details>"
        elif block_type == "callout":
            icon = payload.get("icon", {})
            emoji = icon.get("emoji") if isinstance(icon, dict) else None
            prefix = f"{emoji} " if emoji else ""
            chunk = f"> {prefix}{text}".rstrip()
        elif block_type == "bookmark":
            url = str(payload.get("url") or "").strip()
            label = text or url or "bookmark"
            chunk = f"[{label}]({url})" if url else label
        elif block_type == "child_page":
            title = str(payload.get("title") or "").strip() or "未命名子页面"
            chunk = f"- 子页面: {title}"
        elif block_type == "child_database":
            title = str(payload.get("title") or "").strip() or "未命名子数据库"
            chunk = f"- 子数据库: {title}"
        else:
            chunk = f"[不支持的块类型: {block_type or 'unknown'}]"

        if children and block_type in {"bulleted_list_item", "numbered_list_item", "to_do"}:
            child_chunks = _render_blocks(children, indent=indent + 2)
            if child_chunks:
                chunk = "\n".join([chunk, *child_chunks])
        chunks.append(chunk.strip("\n"))
    return chunks


def _render_table(payload: dict[str, Any]) -> str:
    rows = payload.get("children", [])
    if not isinstance(rows, list) or not rows:
        return "[空表格]"
    parsed_rows: list[list[str]] = []
    for row in rows:
        row_payload = row.get("table_row", {}) if isinstance(row, dict) else {}
        cells = row_payload.get("cells", []) if isinstance(row_payload, dict) else []
        parsed_rows.append([_render_rich_text(cell) for cell in cells if isinstance(cell, list)])
    if not parsed_rows:
        return "[空表格]"
    header = parsed_rows[0]
    sep = ["---"] * len(header)
    lines = [
        f"| {' | '.join(header)} |",
        f"| {' | '.join(sep)} |",
    ]
    for row in parsed_rows[1:]:
        padded = row + [""] * max(0, len(header) - len(row))
        lines.append(f"| {' | '.join(padded[: len(header)])} |")
    return "\n".join(lines)


def _render_rich_text(items: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        text = str(item.get("plain_text") or _extract_text_content(item) or "")
        annotations = item.get("annotations", {}) if isinstance(item.get("annotations"), dict) else {}
        if annotations.get("code"):
            text = f"`{text}`"
        if annotations.get("bold"):
            text = f"**{text}**"
        if annotations.get("italic"):
            text = f"*{text}*"
        if annotations.get("strikethrough"):
            text = f"~~{text}~~"
        href = str(item.get("href") or "").strip()
        if href:
            text = f"[{text}]({href})"
        parts.append(text)
    return "".join(parts)


def _extract_text_content(item: dict[str, Any]) -> str:
    text = item.get("text")
    if isinstance(text, dict):
        return str(text.get("content") or "")
    mention = item.get("mention")
    if isinstance(mention, dict):
        return str(mention.get("plain_text") or "")
    return ""


def _extract_file_url(payload: dict[str, Any]) -> str:
    for key in ("external", "file"):
        candidate = payload.get(key)
        if isinstance(candidate, dict):
            url = str(candidate.get("url") or "").strip()
            if url:
                return url
    return ""


def _rich_text_objects(text: str) -> list[dict[str, Any]]:
    value = str(text or "")
    if not value:
        return []
    items: list[dict[str, Any]] = []
    last_index = 0
    for match in _INLINE_TOKEN_RE.finditer(value):
        if match.start() > last_index:
            items.append(_text_object(value[last_index : match.start()]))
        if match.group(2) is not None and match.group(3) is not None:
            items.append(_text_object(match.group(2), href=match.group(3)))
        elif match.group(4) is not None:
            items.append(_text_object(match.group(4), bold=True))
        elif match.group(5) is not None:
            items.append(_text_object(match.group(5), strikethrough=True))
        elif match.group(6) is not None:
            items.append(_text_object(match.group(6), code=True))
        elif match.group(7) is not None:
            items.append(_text_object(match.group(7), italic=True))
        last_index = match.end()
    if last_index < len(value):
        items.append(_text_object(value[last_index:]))
    return [item for item in items if item["text"]["content"]]


def _text_object(
    content: str,
    *,
    href: str | None = None,
    bold: bool = False,
    italic: bool = False,
    strikethrough: bool = False,
    code: bool = False,
) -> dict[str, Any]:
    return {
        "type": "text",
        "text": {"content": content, **({"link": {"url": href}} if href else {})},
        "annotations": {
            "bold": bold,
            "italic": italic,
            "strikethrough": strikethrough,
            "underline": False,
            "code": code,
            "color": "default",
        },
    }
