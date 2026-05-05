from __future__ import annotations

import asyncio
from pathlib import Path

from hypo_agent.core.delivery import DeliveryResult
from hypo_agent.models import Message


class RecordingDispatcher:
    def __init__(self, channels: tuple[str, ...] = ("qq", "weixin", "feishu")) -> None:
        self.channels = channels
        self.messages: list[Message] = []
        self.results = {
            channel: DeliveryResult.ok(channel, segment_count=1)
            for channel in channels
        }

    async def send(self, message: Message) -> list[DeliveryResult]:
        self.messages.append(message)
        target_channels = message.metadata.get("target_channels")
        channels = tuple(target_channels) if isinstance(target_channels, list) else self.channels
        return [self.results[channel] for channel in channels]


def test_outbound_send_service_builds_dry_run_plan_without_dispatch(tmp_path: Path) -> None:
    image = tmp_path / "cat.png"
    image.write_bytes(b"png")
    report = tmp_path / "report.txt"
    report.write_text("hello", encoding="utf-8")
    dispatcher = RecordingDispatcher()

    from hypo_agent.core.outbound_send import OutboundSendRequest, OutboundSendService

    service = OutboundSendService(dispatcher=dispatcher)
    result = asyncio.run(
        service.send(
            OutboundSendRequest(
                text="[C3-SMOKE] hello",
                images=[str(image)],
                files=[str(report)],
                channels=["qq", "weixin"],
                dry_run=True,
            )
        )
    )

    assert result.dry_run is True
    assert dispatcher.messages == []
    assert result.target_channels == ["qq", "weixin"]
    assert [item["type"] for item in result.attachments] == ["image", "file"]
    assert result.channel_results == {
        "qq": {"success": None, "planned": True, "error": None},
        "weixin": {"success": None, "planned": True, "error": None},
    }


def test_outbound_send_service_dispatches_and_reports_partial_success(tmp_path: Path) -> None:
    image = tmp_path / "cat.png"
    image.write_bytes(b"png")
    dispatcher = RecordingDispatcher(channels=("qq", "weixin"))
    dispatcher.results["weixin"] = DeliveryResult.failed("weixin", segment_count=1, error="upload failed")

    from hypo_agent.core.outbound_send import OutboundSendRequest, OutboundSendService

    service = OutboundSendService(dispatcher=dispatcher)
    result = asyncio.run(
        service.send(
            OutboundSendRequest(
                text="[C3-SMOKE] hello",
                images=[str(image)],
                channels=[],
                dry_run=False,
            )
        )
    )

    assert result.success is False
    assert len(dispatcher.messages) == 1
    assert dispatcher.messages[0].metadata["target_channels"] == ["qq", "weixin"]
    assert dispatcher.messages[0].attachments[0].type == "image"
    assert result.channel_results["qq"]["success"] is True
    assert result.channel_results["weixin"]["success"] is False
    assert result.channel_results["weixin"]["error"] == "upload failed"


def test_outbound_send_service_reports_missing_target_channel_result(tmp_path: Path) -> None:
    class PartialDispatcher(RecordingDispatcher):
        async def send(self, message: Message) -> list[DeliveryResult]:
            self.messages.append(message)
            return [DeliveryResult.ok("weixin", segment_count=1)]

    from hypo_agent.core.outbound_send import OutboundSendRequest, OutboundSendService

    service = OutboundSendService(dispatcher=PartialDispatcher(channels=("qq", "weixin")))
    result = asyncio.run(
        service.send(
            OutboundSendRequest(
                text="[C3-SMOKE] hello",
                channels=["qq", "weixin"],
                dry_run=False,
            )
        )
    )

    assert result.success is False
    assert result.channel_results["weixin"]["success"] is True
    assert result.channel_results["qq"] == {
        "success": False,
        "planned": False,
        "error": "no_delivery_result",
    }


def test_outbound_send_service_defaults_to_registered_external_channels() -> None:
    from hypo_agent.core.channel_dispatcher import ChannelDispatcher, ChannelRelayPolicy
    from hypo_agent.core.outbound_send import OutboundSendRequest, OutboundSendService

    async def _run() -> None:
        dispatcher = ChannelDispatcher()

        async def weixin_sink(message: Message) -> DeliveryResult:
            del message
            return DeliveryResult.ok("weixin", segment_count=1)

        dispatcher.register("webui", lambda message: None, platform="webui", is_external=False)
        dispatcher.register("weixin", weixin_sink, platform="weixin", is_external=True)
        service = OutboundSendService(dispatcher=ChannelRelayPolicy(dispatcher))
        result = await service.send(OutboundSendRequest(text="hello", dry_run=True))

        assert result.target_channels == ["weixin"]
        assert list(result.channel_results) == ["weixin"]

    asyncio.run(_run())


def test_outbound_send_service_maps_qq_bot_result_to_qq_target() -> None:
    from hypo_agent.core.outbound_send import OutboundSendRequest, OutboundSendService

    class RelayLike:
        channels = ("qq",)

        async def relay_message(self, message: Message, **kwargs) -> None:
            del message, kwargs

        def last_delivery_for(self, channel: str) -> dict | None:
            if channel == "qq_bot":
                return DeliveryResult.ok("qq_bot", segment_count=1).to_status_payload()
            return None

    result = asyncio.run(
        OutboundSendService(dispatcher=RelayLike()).send(
            OutboundSendRequest(text="hello", channels=["qq"])
        )
    )

    assert result.success is True
    assert result.channel_results["qq"]["success"] is True


def test_outbound_send_service_missing_file_is_structured_error(tmp_path: Path) -> None:
    from hypo_agent.core.outbound_send import OutboundSendRequest, OutboundSendService

    service = OutboundSendService(dispatcher=RecordingDispatcher())
    result = asyncio.run(
        service.send(
            OutboundSendRequest(
                text="hello",
                files=[str(tmp_path / "missing.pdf")],
                dry_run=True,
            )
        )
    )

    assert result.success is False
    assert result.error == "attachment_not_found"
    assert result.channel_results == {}
