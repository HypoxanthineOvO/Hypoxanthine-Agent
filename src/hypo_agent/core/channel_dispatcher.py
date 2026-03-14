from __future__ import annotations

import inspect
from typing import Any, Awaitable, Callable

import structlog

from hypo_agent.models import Message

logger = structlog.get_logger("hypo_agent.channel_dispatcher")

ChannelSink = Callable[[Message], Awaitable[None] | None]


class ChannelDispatcher:
    def __init__(self) -> None:
        self._sinks: dict[str, ChannelSink] = {}

    def register(self, channel: str, sink: ChannelSink) -> None:
        name = str(channel).strip()
        if not name:
            raise ValueError("channel is required")
        self._sinks[name] = sink

    def unregister(self, channel: str) -> None:
        self._sinks.pop(str(channel).strip(), None)

    async def broadcast(
        self,
        message: Message,
        *,
        exclude_channels: set[str] | None = None,
        exclude_client_ids: set[str] | None = None,
    ) -> None:
        excluded = {str(item).strip() for item in (exclude_channels or set()) if str(item).strip()}
        for channel, sink in list(self._sinks.items()):
            if channel in excluded:
                continue
            try:
                if channel == "webui" and exclude_client_ids:
                    try:
                        result = sink(message, exclude_client_ids=exclude_client_ids)
                    except TypeError as exc:
                        if "exclude_client_ids" not in str(exc):
                            raise
                        result = sink(message)
                else:
                    result = sink(message)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.exception(
                    "channel_dispatcher.broadcast_failed",
                    channel=channel,
                    session_id=message.session_id,
                    message_tag=message.message_tag,
                )

    @property
    def channels(self) -> tuple[str, ...]:
        return tuple(self._sinks.keys())
