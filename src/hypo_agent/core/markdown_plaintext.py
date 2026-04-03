from __future__ import annotations

import re

_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_HEADING_RE = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<title>.+?)\s*$")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*(?!\*)")
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)")
_STRIKE_RE = re.compile(r"~~(.+?)~~")
_ORDERED_LIST_RE = re.compile(r"^\s*\d+\.\s+")
_UNORDERED_LIST_RE = re.compile(r"^(\s*)[-*]\s+")
_DIVIDER_RE = re.compile(r"^\s*---+\s*$")
_QUOTE_RE = re.compile(r"^\s*>\s?(.*)$")


def render_markdown_plaintext(markdown_text: str) -> str:
    if not markdown_text:
        return ""

    protected_text, placeholders = _protect_inline_code(markdown_text)
    heading_rank = _build_heading_rank_map(protected_text)

    rendered_lines: list[str] = []
    current_primary_heading = ""
    for raw_line in protected_text.splitlines():
        line = raw_line.rstrip()

        heading_match = _HEADING_RE.match(line.strip())
        if heading_match:
            level = len(heading_match.group("hashes"))
            title = heading_match.group("title").strip()
            title = _render_inline_text(title)
            rank = heading_rank.get(level, 99)
            if rank == 1:
                current_primary_heading = title
                rendered_lines.append(f"『{title}』")
                continue
            if rank == 2:
                prefix = f"『{current_primary_heading}』-" if current_primary_heading else ""
                rendered_lines.append(f"{prefix}{title}")
                continue
            rendered_lines.append(title)
            continue

        if _DIVIDER_RE.match(line):
            rendered_lines.append("————————")
            continue

        quote_match = _QUOTE_RE.match(line)
        if quote_match:
            quote_text = _render_inline_text(quote_match.group(1).strip())
            rendered_lines.append(f"「{quote_text}」")
            continue

        if _UNORDERED_LIST_RE.match(line) and not _ORDERED_LIST_RE.match(line):
            line = _UNORDERED_LIST_RE.sub(lambda m: f"{m.group(1)}• ", line, count=1)

        rendered_lines.append(_render_inline_text(line))

    restored = "\n".join(rendered_lines)
    return _restore_inline_code(restored, placeholders).strip()


def downgrade_markdown_table(content: str) -> str:
    rows: list[str] = []
    for raw_line in str(content or "").splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) >= 2 and all(cell and _is_divider_cell(cell) for cell in cells):
            continue
        if "|" in stripped:
            rows.append(" | ".join(cells))
            continue
        rows.append(raw_line)
    return "\n".join(row for row in rows if row).strip()


def _is_divider_cell(value: str) -> bool:
    stripped = str(value or "").strip()
    if not stripped:
        return False
    return all(char in "-:" for char in stripped) and "-" in stripped


def _protect_inline_code(text: str) -> tuple[str, list[str]]:
    placeholders: list[str] = []

    def replace(match: re.Match[str]) -> str:
        token = f"__INLINE_CODE_{len(placeholders)}__"
        placeholders.append(match.group(1))
        return token

    return _INLINE_CODE_RE.sub(replace, text), placeholders


def _restore_inline_code(text: str, placeholders: list[str]) -> str:
    restored = text
    for index, content in enumerate(placeholders):
        restored = restored.replace(f"__INLINE_CODE_{index}__", content)
    return restored


def _build_heading_rank_map(text: str) -> dict[int, int]:
    levels = sorted(
        {
            len(match.group("hashes"))
            for line in text.splitlines()
            if (match := _HEADING_RE.match(line.strip()))
        }
    )
    return {level: index + 1 for index, level in enumerate(levels)}


def _render_inline_text(text: str) -> str:
    rendered = _LINK_RE.sub(r"\1 (\2)", text)
    rendered = _STRIKE_RE.sub(r"\1", rendered)
    rendered = _replace_bold(rendered)
    rendered = _ITALIC_RE.sub(r"\1", rendered)
    return rendered


def _replace_bold(text: str) -> str:
    rendered = text
    while True:
        next_text = _BOLD_RE.sub(r"【\1】", rendered)
        if next_text == rendered:
            break
        rendered = next_text
    if rendered.count("**") == 1 and rendered.endswith("*"):
        rendered = re.sub(r"\*\*(.+)\*$", r"【\1】", rendered)
    return rendered
