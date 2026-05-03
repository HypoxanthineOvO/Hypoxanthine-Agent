from __future__ import annotations

import asyncio
from pathlib import Path

from hypo_agent.channels.qq_bot_channel import QQBotChannelService
from hypo_agent.core.channel_dispatcher import ChannelDispatcher, ChannelRelayPolicy
from hypo_agent.core.unified_message import (
    ImageAttachmentBlock,
    MessageProvenance,
    TextBlock,
    UnifiedMessage,
)


def test_channel_relay_sends_single_qq_image_when_unified_message_has_attachment(tmp_path: Path, monkeypatch) -> None:
    async def _run() -> None:
        image_path = tmp_path / "img_1.png"
        image_path.write_bytes(b"png")
        service = QQBotChannelService(app_id="1029384756", app_secret="bot-secret-xyz")
        sent_images: list[str] = []
        sent_texts: list[str] = []

        async def fake_resolve_openid(*, message, qq_meta):
            del message, qq_meta
            return "OPENID-C2C-001"

        async def fake_send_image_with_fallback(**kwargs) -> None:
            sent_images.append(str(kwargs["image_source"]))

        async def fake_send_text_with_markdown_fallback(**kwargs) -> None:
            sent_texts.append(str(kwargs["text"]))

        monkeypatch.setattr(service, "_resolve_openid", fake_resolve_openid)
        monkeypatch.setattr(service, "_send_image_with_fallback", fake_send_image_with_fallback)
        monkeypatch.setattr(service, "_send_text_with_markdown_fallback", fake_send_text_with_markdown_fallback)

        dispatcher = ChannelDispatcher()
        dispatcher.register("qq_bot", service.push_proactive, platform="qq_bot", is_external=True)
        relay = ChannelRelayPolicy(dispatcher)

        await relay.relay_unified_message(
            UnifiedMessage(
                message_type="ai_reply",
                blocks=[
                    TextBlock(text="请看结果"),
                    ImageAttachmentBlock(url=str(image_path), filename="img_1.png"),
                ],
                provenance=MessageProvenance(source_channel="weixin"),
                session_id="main",
                channel="weixin",
                sender="assistant",
                raw_text=f"请看结果\n![img]({image_path})",
                metadata={"target_channels": ["qq_bot"]},
            ),
            origin_channel="weixin",
        )

        assert sent_texts == ["请看结果"]
        assert sent_images == [str(image_path)]

    asyncio.run(_run())
