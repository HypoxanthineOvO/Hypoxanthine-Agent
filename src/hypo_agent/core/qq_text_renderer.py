from __future__ import annotations

import re

from hypo_agent.core.markdown_plaintext import markdown_to_plaintext


def render_qq_plaintext(markdown_text: str) -> str:
    return markdown_to_plaintext(markdown_text)


_HEADING_RE = re.compile(r"^(?P<indent>\s*)(?P<hashes>#{1,6})\s+(?P<title>.+?)\s*$")


def downgrade_headings(text: str, max_level: int) -> str:
    safe_level = max(1, int(max_level))
    rendered_lines: list[str] = []
    for raw_line in str(text or "").splitlines(keepends=True):
        stripped_newline = raw_line[:-1] if raw_line.endswith("\n") else raw_line
        suffix = "\n" if raw_line.endswith("\n") else ""
        match = _HEADING_RE.match(stripped_newline)
        if match is None:
            rendered_lines.append(raw_line)
            continue
        level = len(match.group("hashes"))
        if level <= safe_level:
            rendered_lines.append(raw_line)
            continue
        title = match.group("title").strip()
        rendered_lines.append(f"{match.group('indent')}**{title}**{suffix}")
    return "".join(rendered_lines)
