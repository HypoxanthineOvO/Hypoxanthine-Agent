from __future__ import annotations

from hypo_agent.core.operation_events import OperationEvent


def test_operation_event_serializes_resource_candidates_for_channels() -> None:
    event = OperationEvent.resource_candidates(
        operation_id="op-1",
        session_id="main",
        candidates=[
            {
                "display_name": "report.md",
                "uri": "/tmp/report.md",
                "source": "recent_generated",
            }
        ],
        recovery_action={
            "type": "ask_user",
            "reason": "multiple_candidates",
            "message": "请选择要发送的文件。",
        },
    )

    payload = event.to_payload()

    assert payload["type"] == "operation_event"
    assert payload["event_type"] == "resource_candidates"
    assert payload["operation_id"] == "op-1"
    assert payload["session_id"] == "main"
    assert payload["recovery_action"]["type"] == "ask_user"
    assert payload["candidates"][0]["display_name"] == "report.md"


def test_operation_event_serializes_channel_delivery_result() -> None:
    event = OperationEvent.channel_delivery(
        operation_id="op-2",
        session_id="main",
        channel="weixin",
        status="fallback",
        delivery={
            "success": False,
            "error": "unsupported attachment type",
        },
        recovery_action={
            "type": "send_summary",
            "reason": "unsupported_attachment_type",
            "message": "微信不支持该附件类型，改发摘要。",
        },
    )

    payload = event.to_payload()

    assert payload["event_type"] == "channel_delivery"
    assert payload["channel"] == "weixin"
    assert payload["status"] == "fallback"
    assert payload["delivery"]["error"] == "unsupported attachment type"
    assert payload["recovery_action"]["reason"] == "unsupported_attachment_type"
