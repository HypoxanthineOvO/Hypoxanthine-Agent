from __future__ import annotations

import asyncio
from pathlib import Path

from hypo_agent.channels.qq_adapter import QQAdapter
from hypo_agent.models import Attachment, Message


class StubRenderer:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.calls: list[tuple[str, str]] = []

    async def render_to_image(self, content: str, block_type: str = "markdown") -> str:
        self.calls.append((content, block_type))
        return f"/tmp/{block_type}_{len(self.calls)}.png"


def test_format_plain_text() -> None:
    adapter = QQAdapter(napcat_http_url="http://localhost:3000")

    segments = asyncio.run(
        adapter.format(
            Message(text="**你好**", sender="assistant", session_id="s1")
        )
    )

    assert segments == [{"type": "text", "data": {"text": "【你好】"}}]


def test_format_with_code_block() -> None:
    renderer = StubRenderer()
    adapter = QQAdapter(napcat_http_url="http://localhost:3000", image_renderer=renderer)
    message = Message(
        text="前文\n```python\nprint('x')\n```\n后文",
        sender="assistant",
        session_id="s1",
    )

    segments = asyncio.run(adapter.format(message))

    assert [segment["type"] for segment in segments] == ["text", "image", "text"]
    assert renderer.calls == [("print('x')", "code")]


def test_format_with_table() -> None:
    renderer = StubRenderer()
    adapter = QQAdapter(napcat_http_url="http://localhost:3000", image_renderer=renderer)
    message = Message(
        text="说明\n| A | B |\n| --- | --- |\n| 1 | 2 |\n结尾",
        sender="assistant",
        session_id="s1",
    )

    segments = asyncio.run(adapter.format(message))

    assert [segment["type"] for segment in segments] == ["text", "image", "text"]
    assert renderer.calls[0][1] == "table"


def test_format_renderer_unavailable() -> None:
    renderer = StubRenderer(available=False)
    adapter = QQAdapter(napcat_http_url="http://localhost:3000", image_renderer=renderer)
    message = Message(
        text="前文\n```python\nprint('x')\n```\n后文",
        sender="assistant",
        session_id="s1",
    )

    segments = asyncio.run(adapter.format(message))

    assert len(segments) == 1
    assert segments[0]["type"] == "text"
    assert "[代码块渲染失败，原始内容如下]" in segments[0]["data"]["text"]
    assert "print('x')" in segments[0]["data"]["text"]


def test_format_multiple_blocks() -> None:
    renderer = StubRenderer()
    adapter = QQAdapter(napcat_http_url="http://localhost:3000", image_renderer=renderer)
    message = Message(
        text=(
            "A\n"
            "```python\nprint(1)\n```\n"
            "B\n"
            "| X | Y |\n| --- | --- |\n| 1 | 2 |\n"
            "C\n"
            "```mermaid\ngraph TD\nA-->B\n```\n"
            "D"
        ),
        sender="assistant",
        session_id="s1",
    )

    segments = asyncio.run(adapter.format(message))

    assert [segment["type"] for segment in segments] == [
        "text",
        "image",
        "text",
        "image",
        "text",
        "image",
        "text",
    ]
    assert [item[1] for item in renderer.calls] == ["code", "table", "mermaid"]


def test_format_includes_file_attachments() -> None:
    renderer = StubRenderer()
    adapter = QQAdapter(napcat_http_url="http://localhost:3000", image_renderer=renderer)
    export_path = Path("/tmp/export.pdf")
    message = Message(
        text="已导出",
        sender="assistant",
        session_id="s1",
        attachments=[
            Attachment(type="file", url=str(export_path), filename="export.pdf", mime_type="application/pdf")
        ],
    )

    segments = asyncio.run(adapter.format(message))

    assert segments[-1]["type"] == "file"
    assert segments[-1]["data"]["file"].startswith("file:///")


def test_long_message_not_truncated_or_split() -> None:
    adapter = QQAdapter(napcat_http_url="http://localhost:3000")
    long_text = "A" * 5000

    segments = asyncio.run(
        adapter.format(
            Message(text=long_text, sender="assistant", session_id="s1")
        )
    )

    assert segments == [{"type": "text", "data": {"text": long_text}}]
    assert "WebUI" not in segments[0]["data"]["text"]
