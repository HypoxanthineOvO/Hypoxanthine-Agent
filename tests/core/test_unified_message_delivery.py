from __future__ import annotations

from hypo_agent.core.unified_message import (
    ImageAttachmentBlock,
    MessageProvenance,
    TextBlock,
    UnifiedMessage,
    message_from_unified,
)


def test_message_from_unified_strips_markdown_image_refs_when_attachment_exists() -> None:
    message = UnifiedMessage(
        message_type="ai_reply",
        blocks=[
            TextBlock(text="请看结果"),
            ImageAttachmentBlock(url="output/imagegen/img_1.png", filename="img_1.png"),
        ],
        provenance=MessageProvenance(source_channel="weixin"),
        session_id="main",
        channel="weixin",
        sender="assistant",
        raw_text="请看结果\n![img](output/imagegen/img_1.png)",
    )

    outbound = message_from_unified(message)

    assert outbound.text == "请看结果"
    assert len(outbound.attachments) == 1
    assert outbound.attachments[0].type == "image"
    assert outbound.image == "output/imagegen/img_1.png"
