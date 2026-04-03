from __future__ import annotations

from hypo_agent.core.markdown_plaintext import markdown_to_plaintext


def render_qq_plaintext(markdown_text: str) -> str:
    return markdown_to_plaintext(markdown_text)
