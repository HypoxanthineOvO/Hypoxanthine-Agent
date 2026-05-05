from __future__ import annotations

from hypo_agent.core.active_recovery import ActiveRecoveryStateMachine
from hypo_agent.core.delivery import AttachmentDeliveryOutcome, ChannelCapability, DeliveryResult
from hypo_agent.core.resource_resolution import (
    ResourceCandidate,
    ResourceRecoveryAction,
    ResourceRef,
    ResourceResolution,
)


def test_active_recovery_asks_user_when_resource_is_ambiguous() -> None:
    machine = ActiveRecoveryStateMachine()
    resolution = ResourceResolution(
        status="ambiguous",
        candidates=[
            ResourceCandidate(
                ref=ResourceRef(kind="file", uri="/tmp/a.md", display_name="a.md"),
                score=0.9,
                source="search_root",
            )
        ],
        recovery_action=ResourceRecoveryAction(
            type="ask_user",
            reason="multiple_candidates",
            message="请选择文件。",
        ),
    )

    decision = machine.decide_channel_delivery(
        resource_resolution=resolution,
        capability=ChannelCapability(channel="weixin", supported_attachment_types={"file"}),
    )

    assert decision.state == "ask_user"
    assert decision.action == "confirm_resource"
    assert decision.recovery_action["reason"] == "multiple_candidates"


def test_active_recovery_falls_back_when_channel_cannot_send_attachment_type() -> None:
    machine = ActiveRecoveryStateMachine()

    decision = machine.decide_channel_delivery(
        resource_resolution=ResourceResolution(
            status="resolved",
            ref=ResourceRef(kind="attachment", uri="/tmp/clip.mp4", display_name="clip.mp4", metadata={"attachment_type": "video"}),
        ),
        capability=ChannelCapability(
            channel="feishu",
            supported_attachment_types={"image", "file"},
            fallback_actions=["fallback_to_link", "send_summary"],
        ),
    )

    assert decision.state == "fallback"
    assert decision.action == "fallback_to_link"
    assert decision.recovery_action["reason"] == "unsupported_attachment_type"


def test_active_recovery_retries_upload_failure_with_budget() -> None:
    machine = ActiveRecoveryStateMachine(max_retries=2)

    decision = machine.decide_after_delivery(
        DeliveryResult.failed(
            "qq_bot",
            segment_count=1,
            error="upload failed",
            attachment_outcomes=[
                AttachmentDeliveryOutcome(
                    filename="report.md",
                    attachment_type="file",
                    success=False,
                    error="upload failed",
                )
            ],
        ),
        attempts=1,
    )

    assert decision.state == "retry"
    assert decision.action == "retry_upload"
    assert decision.retry_after_attempts == 1


def test_active_recovery_gives_up_explained_when_retry_budget_exhausted() -> None:
    machine = ActiveRecoveryStateMachine(max_retries=2)

    decision = machine.decide_after_delivery(
        DeliveryResult.failed("weixin", segment_count=1, error="upload failed"),
        attempts=2,
    )

    assert decision.state == "give_up_explained"
    assert decision.action == "explain_failure"
    assert decision.recovery_action["reason"] == "retry_budget_exhausted"


def test_active_recovery_verifies_successful_delivery() -> None:
    machine = ActiveRecoveryStateMachine()

    decision = machine.decide_after_delivery(DeliveryResult.ok("feishu", segment_count=1), attempts=0)

    assert decision.state == "verify_result"
    assert decision.action == "verify_delivery"
