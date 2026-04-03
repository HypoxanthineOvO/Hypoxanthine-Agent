from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from hypo_agent.models import Message


class DummyPipeline:
    """No-op pipeline used by API tests that should never hit model backends."""

    async def start_event_consumer(self) -> None:
        return None

    async def stop_event_consumer(self) -> None:
        return None

    async def enqueue_user_message(self, inbound: Message, *, emit) -> None:
        del inbound, emit

    async def stream_reply(self, inbound):
        del inbound
        if False:  # pragma: no cover
            yield {}


class PassivePipeline(DummyPipeline):
    """Alias for tests that only need lifecycle hooks without response output."""


class NoopScheduler:
    """Scheduler stub that records running state without scheduling real jobs."""

    is_running = False

    async def start(self) -> None:
        self.is_running = True

    async def stop(self) -> None:
        self.is_running = False

    def register_interval_job(self, *args, **kwargs) -> None:
        del args, kwargs

    def register_cron_job(self, *args, **kwargs) -> None:
        del args, kwargs


class NoopRouter:
    """Router stub that returns deterministic in-process responses."""

    async def call(self, model_name, messages, *, session_id=None, tools=None):
        del model_name, messages, session_id, tools
        return "ok"

    async def call_with_tools(self, model_name, messages, *, tools=None, session_id=None):
        del model_name, messages, tools, session_id
        return {"text": "ok", "tool_calls": []}

    async def stream(self, model_name, messages, *, session_id=None, tools=None):
        del model_name, messages, session_id, tools
        yield "ok"


@dataclass(slots=True)
class RecordingExternalSink:
    """Fake outbound sink that records messages instead of talking to real channels."""

    name: str
    deliveries: list[Message] = field(default_factory=list)

    async def push(self, message: Message) -> None:
        self.deliveries.append(message)


class FakeWeixinChannel:
    """Fake Weixin channel used by gateway lifespan tests to prevent polling."""

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.started = 0
        self.stopped = 0
        self.messages_sent = 0
        self.messages_received = 0
        self.last_message_at = None
        self.client = SimpleNamespace(bot_token="fake-weixin-token", user_id="", bot_id="fake-weixin-bot")

    async def start(self) -> None:
        self.started += 1

    async def stop(self) -> None:
        self.stopped += 1

    def record_message_sent(self) -> None:
        self.messages_sent += 1

    def get_status(self) -> dict[str, object]:
        return {
            "status": "connected" if self.started else "disconnected",
            "bot_id": getattr(self.client, "bot_id", ""),
            "user_id": getattr(self.client, "user_id", ""),
            "last_message_at": self.last_message_at,
            "messages_received": self.messages_received,
            "messages_sent": self.messages_sent,
        }


class FakeQQWSClient:
    """Fake NapCat WebSocket client that never opens a socket."""

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.started = 0
        self.stopped = 0
        self.status = "disconnected"
        self.connected_at = None
        self.last_message_at = None
        self.messages_received = 0
        self.messages_sent = 0

    async def start(self) -> None:
        self.started += 1
        self.status = "connected"

    async def stop(self) -> None:
        self.stopped += 1
        self.status = "disconnected"

    def record_message_sent(self) -> None:
        self.messages_sent += 1

    def get_status(self) -> dict[str, object]:
        return {
            "status": self.status,
            "connected_at": self.connected_at,
            "last_message_at": self.last_message_at,
            "messages_received": self.messages_received,
            "messages_sent": self.messages_sent,
        }


class FakeQQBotWSClient(FakeQQWSClient):
    """Fake QQ Bot WebSocket client that mirrors the NapCat test contract."""

    ws_connected = True


class FakeIMAPClient:
    """Fake IMAP client that records calls and never reaches remote mail servers."""

    def __init__(self, host: str, port: int = 993) -> None:
        self.host = host
        self.port = port
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def _record(self, name: str, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        return "OK", [b""]

    def login(self, username: str, password: str):
        return self._record("login", username, password)

    def select(self, mailbox: str = "INBOX"):
        return self._record("select", mailbox)

    def search(self, charset, *criteria):
        del charset
        self.calls.append(("search", criteria, {}))
        return "OK", [b""]

    def fetch(self, message_id, query):
        return self._record("fetch", message_id, query)

    def store(self, *args, **kwargs):
        return self._record("store", *args, **kwargs)

    def close(self):
        return self._record("close")

    def logout(self):
        return self._record("logout")
