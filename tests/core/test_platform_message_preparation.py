from __future__ import annotations

from hypo_agent.core.platform_message_preparation import prepare_message_for_platform
from hypo_agent.models import Attachment, Message


def test_prepare_message_for_weixin_leaves_plain_text_unchanged() -> None:
    message = Message(
        text="hello weixin",
        sender="assistant",
        session_id="main",
        channel="system",
    )

    prepared = prepare_message_for_platform(message, platform="weixin")

    assert len(prepared) == 1
    assert prepared[0] == message


def test_prepare_message_for_weixin_splits_markdown_image_into_text_and_image_messages() -> None:
    message = Message(
        text="图在这里 ![cat](./cat.png) 请查看",
        sender="assistant",
        session_id="main",
        channel="system",
    )

    prepared = prepare_message_for_platform(message, platform="weixin")

    assert [item.text for item in prepared] == ["图在这里 【见下方图片】 请查看", None]
    assert prepared[1].attachments == [Attachment(type="image", url="./cat.png")]


def test_prepare_message_for_weixin_keeps_pure_image_message_as_image_only() -> None:
    message = Message(
        text="![cat](./cat.png)",
        sender="assistant",
        session_id="main",
        channel="system",
    )

    prepared = prepare_message_for_platform(message, platform="weixin")

    assert len(prepared) == 1
    assert prepared[0].text is None
    assert prepared[0].attachments == [Attachment(type="image", url="./cat.png")]


def test_prepare_message_for_weixin_extracts_data_uri_images() -> None:
    message = Message(
        text="看这个 data:image/png;base64,ZmFrZQ==",
        sender="assistant",
        session_id="main",
        channel="system",
    )

    prepared = prepare_message_for_platform(message, platform="weixin")

    assert [item.text for item in prepared] == ["看这个 【见下方图片】", None]
    assert prepared[1].attachments == [Attachment(type="image", url="data:image/png;base64,ZmFrZQ==")]


def test_prepare_message_for_weixin_numbers_multiple_image_placeholders_in_order() -> None:
    message = Message(
        text="先看这个 [CQ:image,file=./a.png] 再看这个 ![b](./b.png)",
        sender="assistant",
        session_id="main",
        channel="system",
    )

    prepared = prepare_message_for_platform(message, platform="weixin")

    assert [item.text for item in prepared] == [
        "先看这个 【见下方图片 1】 再看这个 【见下方图片 2】",
        None,
        None,
    ]
    assert [item.attachments[0].url for item in prepared[1:]] == ["./a.png", "./b.png"]


def test_prepare_message_for_weixin_splits_image_attachments_after_text() -> None:
    message = Message(
        text="附件如下",
        sender="assistant",
        session_id="main",
        channel="system",
        attachments=[
            Attachment(type="image", url="./cat.png"),
            Attachment(type="file", url="./report.txt", filename="report.txt"),
        ],
    )

    prepared = prepare_message_for_platform(message, platform="weixin")

    assert len(prepared) == 2
    assert prepared[0].text == "附件如下"
    assert prepared[0].attachments == [Attachment(type="file", url="./report.txt", filename="report.txt")]
    assert prepared[1].attachments == [Attachment(type="image", url="./cat.png")]


def test_prepare_message_for_qq_leaves_message_unchanged() -> None:
    message = Message(
        text="图在这里 ![cat](./cat.png)",
        sender="assistant",
        session_id="main",
        channel="qq",
    )

    prepared = prepare_message_for_platform(message, platform="qq")

    assert prepared == [message]
