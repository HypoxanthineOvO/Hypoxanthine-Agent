from __future__ import annotations

import asyncio

from hypo_agent.core.channel_dispatcher import ChannelDispatcher
from hypo_agent.models import Message


def test_channel_dispatcher_broadcasts_to_all_channels() -> None:
    async def _run() -> None:
        dispatcher = ChannelDispatcher()
        received_a: list[str] = []
        received_b: list[str] = []
        message = Message(text="hello", sender="assistant", session_id="main")

        async def sink_a(msg: Message) -> None:
            received_a.append(msg.text or "")

        async def sink_b(msg: Message) -> None:
            received_b.append(msg.text or "")

        dispatcher.register("a", sink_a)
        dispatcher.register("b", sink_b)

        await dispatcher.broadcast(message)

        assert received_a == ["hello"]
        assert received_b == ["hello"]

    asyncio.run(_run())


def test_channel_dispatcher_continues_after_sink_failure() -> None:
    async def _run() -> None:
        dispatcher = ChannelDispatcher()
        received: list[str] = []
        message = Message(text="hello", sender="assistant", session_id="main")

        async def broken_sink(msg: Message) -> None:
            del msg
            raise RuntimeError("boom")

        async def healthy_sink(msg: Message) -> None:
            received.append(msg.text or "")

        dispatcher.register("broken", broken_sink)
        dispatcher.register("healthy", healthy_sink)

        await dispatcher.broadcast(message)

        assert received == ["hello"]

    asyncio.run(_run())
