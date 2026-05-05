from __future__ import annotations

from hypo_agent.core.delivery import (
    AttachmentDeliveryOutcome,
    ChannelCapability,
    DeliveryResult,
    combine_delivery_results,
)


def test_channel_capability_declares_supported_attachment_types_and_limits() -> None:
    capability = ChannelCapability(
        channel="weixin",
        supports_text=True,
        supported_attachment_types={"image", "file"},
        max_attachment_bytes=25 * 1024 * 1024,
        fallback_actions=["send_link", "send_summary"],
    )

    assert capability.supports_attachment_type("file") is True
    assert capability.supports_attachment_type("video") is False
    assert capability.describe_limit("file") == "weixin supports file up to 26214400 bytes"


def test_delivery_result_carries_per_attachment_outcomes() -> None:
    outcome = AttachmentDeliveryOutcome(
        filename="report.md",
        attachment_type="file",
        success=False,
        error="upload failed",
        recovery_action={
            "type": "fallback_to_link",
            "reason": "upload_failed",
            "message": "文件上传失败，可改为发送下载链接。",
        },
    )

    result = DeliveryResult.failed(
        "qq_bot",
        segment_count=1,
        error="upload failed",
        attachment_outcomes=[outcome],
    )

    payload = result.to_status_payload()

    assert result.attachment_outcomes == [outcome]
    assert payload["attachment_outcomes"][0]["filename"] == "report.md"
    assert payload["attachment_outcomes"][0]["recovery_action"]["type"] == "fallback_to_link"


def test_combine_delivery_results_preserves_attachment_outcomes() -> None:
    first = DeliveryResult.ok(
        "feishu",
        segment_count=1,
        attachment_outcomes=[
            AttachmentDeliveryOutcome(
                filename="a.md",
                attachment_type="file",
                success=True,
            )
        ],
    )
    second = DeliveryResult.failed(
        "feishu",
        segment_count=1,
        error="image too large",
        attachment_outcomes=[
            AttachmentDeliveryOutcome(
                filename="b.png",
                attachment_type="image",
                success=False,
                error="image too large",
            )
        ],
    )

    combined = combine_delivery_results("feishu", [first, second])

    assert combined.success is False
    assert combined.segment_count == 2
    assert [item.filename for item in combined.attachment_outcomes] == ["a.md", "b.png"]
