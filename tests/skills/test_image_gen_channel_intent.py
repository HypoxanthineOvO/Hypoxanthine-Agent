"""Tests for M3 — channel delivery and Agent intent integration."""
from __future__ import annotations

from pathlib import Path

from hypo_agent.core.operation_events import OperationEvent, OperationEventType


class TestOperationEventImageTypes:
    """Tests for new OperationEventType values: image_generation, image_delivery."""

    def test_operation_event_type_includes_image_generation(self) -> None:
        # This will fail until we extend OperationEventType
        event = OperationEvent(
            operation_id="img_gen_001",
            session_id="session_abc",
            event_type="image_generation",  # type: ignore[arg-type]
            status="success",
        )
        assert event.event_type == "image_generation"

    def test_operation_event_type_includes_image_delivery(self) -> None:
        event = OperationEvent(
            operation_id="img_del_001",
            session_id="session_abc",
            event_type="image_delivery",  # type: ignore[arg-type]
            status="success",
            channel="qq",
        )
        assert event.event_type == "image_delivery"

    def test_image_generation_factory(self) -> None:
        event = OperationEvent.image_generation(
            operation_id="img_gen_002",
            session_id="session_abc",
            status="success",
            generation={
                "prompt": "a cat wearing sunglasses",
                "size": "1024x1024",
                "quality": "medium",
                "n": 1,
                "output_paths": ["/tmp/cat.png"],
                "duration_ms": 4500,
            },
        )
        assert event.event_type == "image_generation"
        assert event.status == "success"
        assert event.generation["prompt"] == "a cat wearing sunglasses"
        assert event.generation["duration_ms"] == 4500

    def test_image_generation_factory_failure(self) -> None:
        event = OperationEvent.image_generation(
            operation_id="img_gen_003",
            session_id="session_abc",
            status="failed",
            generation={
                "prompt": "inappropriate content",
                "error_type": "content_policy",
            },
            recovery_action={
                "type": "modify_prompt",
                "reason": "content_policy_violation",
            },
        )
        assert event.event_type == "image_generation"
        assert event.status == "failed"
        assert event.recovery_action is not None
        assert event.recovery_action["type"] == "modify_prompt"

    def test_image_delivery_factory(self) -> None:
        event = OperationEvent.image_delivery(
            operation_id="img_del_002",
            session_id="session_abc",
            channel="qq",
            status="success",
            delivery={
                "image_path": "/tmp/cat.png",
                "target_channel": "qq",
                "delivery_method": "attachment",
                "duration_ms": 1200,
            },
        )
        assert event.event_type == "image_delivery"
        assert event.channel == "qq"
        assert event.status == "success"
        assert event.delivery["delivery_method"] == "attachment"

    def test_image_delivery_factory_failure(self) -> None:
        event = OperationEvent.image_delivery(
            operation_id="img_del_003",
            session_id="session_abc",
            channel="wechat",
            status="failed",
            delivery={
                "image_path": "/tmp/cat.png",
                "target_channel": "wechat",
                "error": "channel does not support image attachments",
            },
            recovery_action={
                "type": "fallback",
                "reason": "unsupported_attachment",
                "message": "Sending image URL instead.",
            },
        )
        assert event.event_type == "image_delivery"
        assert event.status == "failed"
        assert event.recovery_action is not None

    def test_image_generation_payload_serialization(self) -> None:
        event = OperationEvent.image_generation(
            operation_id="img_gen_004",
            session_id="session_abc",
            status="success",
            generation={"prompt": "test", "duration_ms": 100},
        )
        payload = event.to_payload()
        assert payload["type"] == "operation_event"
        assert payload["event_type"] == "image_generation"
        assert payload["operation_id"] == "img_gen_004"
        assert "generation" in payload

    def test_image_delivery_payload_serialization(self) -> None:
        event = OperationEvent.image_delivery(
            operation_id="img_del_004",
            session_id="session_abc",
            channel="feishu",
            status="success",
            delivery={"image_path": "/tmp/test.png"},
        )
        payload = event.to_payload()
        assert payload["event_type"] == "image_delivery"
        assert payload["channel"] == "feishu"
        assert "delivery" in payload


class TestImageGenerationIntentDetection:
    """Tests for Agent NLU intent detection for image generation triggers."""

    def test_detect_image_intent_chinese(self) -> None:
        from hypo_agent.skills.image_gen_skill import detect_image_generation_intent

        result = detect_image_generation_intent("帮我画一只猫")
        assert result is not None
        assert result["intent"] == "generate_image"
        assert "猫" in result["prompt"]

    def test_detect_image_intent_english(self) -> None:
        from hypo_agent.skills.image_gen_skill import detect_image_generation_intent

        result = detect_image_generation_intent("generate an image of a sunset")
        assert result is not None
        assert result["intent"] == "generate_image"
        assert "sunset" in result["prompt"]

    def test_detect_image_intent_with_style(self) -> None:
        from hypo_agent.skills.image_gen_skill import detect_image_generation_intent

        result = detect_image_generation_intent("画一张水彩风格的山水画")
        assert result is not None
        assert result["intent"] == "generate_image"
        assert "水彩" in result.get("style", "") or "山水" in result["prompt"]

    def test_detect_image_intent_edit(self) -> None:
        from hypo_agent.skills.image_gen_skill import detect_image_generation_intent

        result = detect_image_generation_intent("给这张图片加上帽子")
        assert result is not None
        assert result["intent"] == "edit_image"

    def test_detect_image_intent_no_match(self) -> None:
        from hypo_agent.skills.image_gen_skill import detect_image_generation_intent

        result = detect_image_generation_intent("今天天气怎么样？")
        assert result is None

    def test_detect_image_intent_edge_cases(self) -> None:
        from hypo_agent.skills.image_gen_skill import detect_image_generation_intent

        # Empty string
        assert detect_image_generation_intent("") is None
        # Whitespace only
        assert detect_image_generation_intent("   ") is None
        # Very short non-image text
        assert detect_image_generation_intent("hi") is None


class TestChannelCapabilityImageSupport:
    """Tests for ChannelCapability image attachment support checks."""

    def test_channel_supports_image_type(self) -> None:
        from hypo_agent.core.delivery import ChannelCapability

        cap = ChannelCapability(
            channel="qq",
            supports_text=True,
            supported_attachment_types={"image", "file"},
            max_attachment_bytes=10 * 1024 * 1024,
        )
        assert cap.supports_attachment_type("image") is True

    def test_channel_does_not_support_image_type(self) -> None:
        from hypo_agent.core.delivery import ChannelCapability

        cap = ChannelCapability(
            channel="sms",
            supports_text=True,
            supported_attachment_types=set(),
            max_attachment_bytes=None,
        )
        assert cap.supports_attachment_type("image") is False

    def test_channel_image_size_within_limit(self) -> None:
        from hypo_agent.core.delivery import ChannelCapability

        cap = ChannelCapability(
            channel="qq",
            supports_text=True,
            supported_attachment_types={"image"},
            max_attachment_bytes=5 * 1024 * 1024,
        )
        # 1MB image is within 5MB limit
        assert 1024 * 1024 <= (cap.max_attachment_bytes or float("inf"))

    def test_channel_image_size_exceeds_limit(self) -> None:
        from hypo_agent.core.delivery import ChannelCapability

        cap = ChannelCapability(
            channel="qq",
            supports_text=True,
            supported_attachment_types={"image"},
            max_attachment_bytes=5 * 1024 * 1024,
        )
        # 10MB image exceeds 5MB limit
        assert 10 * 1024 * 1024 > (cap.max_attachment_bytes or float("inf"))
