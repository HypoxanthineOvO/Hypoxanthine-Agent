from __future__ import annotations

import asyncio

from hypo_agent.core.image_renderer import ImageRenderError
from hypo_agent.core.qq_renderer import QQRenderer
from hypo_agent.models import Attachment, Message


class StubImageRenderer:
    def __init__(self, *, available: bool = True, fail: bool = False) -> None:
        self.available = available
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    async def render_to_image(self, content: str, block_type: str = "markdown") -> str:
        self.calls.append((content, block_type))
        if self.fail:
            raise ImageRenderError(
                block_type=block_type,
                fallback_text=f"[{block_type}渲染失败，原始内容如下]\n{content}",
                reason="boom",
            )
        return f"/tmp/{block_type}_{len(self.calls)}.png"

    def build_fallback_text(self, content: str, *, block_type: str) -> str:
        return f"[{block_type}渲染失败，原始内容如下]\n{content}"


def test_qq_renderer_renders_plain_text() -> None:
    renderer = QQRenderer()

    segments = asyncio.run(
        renderer.render(Message(text="**你好**", sender="assistant", session_id="main"))
    )

    assert segments == [{"type": "text", "text": "【你好】"}]


def test_qq_renderer_outputs_image_segments_for_code_and_table_blocks() -> None:
    image_renderer = StubImageRenderer()
    renderer = QQRenderer(image_renderer=image_renderer)
    message = Message(
        text="前文\n```python\nprint('x')\n```\n| A | B |\n| --- | --- |\n| 1 | 2 |\n后文",
        sender="assistant",
        session_id="main",
    )

    segments = asyncio.run(renderer.render(message))

    assert [segment["type"] for segment in segments] == ["text", "image", "image", "text"]
    assert image_renderer.calls == [("print('x')", "code"), ("| A | B |\n| --- | --- |\n| 1 | 2 |", "table")]


def test_qq_renderer_preserves_attachment_images() -> None:
    renderer = QQRenderer()
    message = Message(
        text="hello",
        sender="assistant",
        session_id="main",
        attachments=[Attachment(type="image", url="/tmp/cat.png", filename="cat.png")],
    )

    segments = asyncio.run(renderer.render(message))

    assert segments[-1] == {"type": "image", "source": "/tmp/cat.png", "name": "cat.png"}


def test_qq_renderer_deduplicates_attachment_and_legacy_file() -> None:
    renderer = QQRenderer()
    message = Message(
        text="已导出",
        sender="assistant",
        session_id="main",
        attachments=[
            Attachment(
                type="file",
                url="/tmp/notion-export.md",
                filename="notion-export.md",
                mime_type="text/markdown",
            )
        ],
        file="/tmp/notion-export.md",
    )

    segments = asyncio.run(renderer.render(message))

    file_segments = [segment for segment in segments if segment["type"] == "file"]
    assert file_segments == [
        {
            "type": "file",
            "source": "/tmp/notion-export.md",
            "name": "notion-export.md",
            "mime_type": "text/markdown",
            "attachment_type": "file",
        }
    ]


def test_qq_renderer_falls_back_to_text_when_image_rendering_fails() -> None:
    image_renderer = StubImageRenderer(fail=True)
    renderer = QQRenderer(image_renderer=image_renderer)

    segments = asyncio.run(
        renderer.render(
            Message(
                text="```python\nprint('x')\n```",
                sender="assistant",
                session_id="main",
            )
        )
    )

    assert segments == [
        {
            "type": "text",
            "text": "[code渲染失败，原始内容如下]\nprint('x')",
        }
    ]
