from __future__ import annotations

import asyncio
from pathlib import Path

from hypo_agent.core.image_renderer import ImageRenderError
from hypo_agent.core.weixin_renderer import WeixinRenderer
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


def test_weixin_renderer_renders_plain_text_without_markdown_symbols() -> None:
    renderer = WeixinRenderer()

    text = renderer.render_message_text(
        Message(
            text="**提醒** `喝水`",
            sender="assistant",
            session_id="main",
            message_tag="reminder",
        )
    )

    assert text.startswith("🔔 ")
    assert "**" not in text
    assert "`" not in text
    assert "提醒" in text
    assert "喝水" in text


def test_weixin_renderer_outputs_images_for_renderable_blocks() -> None:
    image_renderer = StubImageRenderer()
    renderer = WeixinRenderer(image_renderer=image_renderer)

    segments = asyncio.run(
        renderer.render(
            Message(
                text="```python\nprint('x')\n```\n| A | B |\n| --- | --- |\n| 1 | 2 |\n",
                sender="assistant",
                session_id="main",
            )
        )
    )

    assert [segment["type"] for segment in segments] == ["image", "image"]
    assert image_renderer.calls == [("print('x')", "code"), ("| A | B |\n| --- | --- |\n| 1 | 2 |", "table")]


def test_weixin_renderer_preserves_image_attachments() -> None:
    renderer = WeixinRenderer()

    segments = asyncio.run(
        renderer.render(
            Message(
                text="hello",
                sender="assistant",
                session_id="main",
                attachments=[Attachment(type="image", url="/tmp/cat.png", filename="cat.png")],
            )
        )
    )

    assert segments[-1] == {"type": "image", "source": "/tmp/cat.png", "name": "cat.png"}


def test_weixin_renderer_falls_back_to_text_when_rendering_fails() -> None:
    renderer = WeixinRenderer(image_renderer=StubImageRenderer(fail=True))

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


def test_weixin_adapter_source_does_not_import_qq_adapter() -> None:
    adapter_source = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "hypo_agent"
        / "channels"
        / "weixin"
        / "weixin_adapter.py"
    ).read_text(encoding="utf-8")

    assert "QQAdapter" not in adapter_source
