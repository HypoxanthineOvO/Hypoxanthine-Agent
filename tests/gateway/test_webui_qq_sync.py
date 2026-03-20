from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from hypo_agent.core.event_queue import EventQueue
from hypo_agent.channels.qq_channel import QQChannelService
from hypo_agent.core.pipeline import ChatPipeline
from hypo_agent.gateway.app import AppDeps, create_app
from hypo_agent.memory.session import SessionMemory
from hypo_agent.memory.structured_store import StructuredStore
from hypo_agent.models import Message


class NoopScheduler:
    is_running = False

    async def start(self) -> None:
        self.is_running = True

    async def stop(self) -> None:
        self.is_running = False


class StreamingRouter:
    def __init__(self, reply: str) -> None:
        self.reply = reply

    async def stream(self, model_name, messages, *, session_id=None, tools=None):
        del model_name, messages, session_id, tools
        yield self.reply


def _make_app(tmp_path: Path, *, reply: str) -> TestClient:
    session_memory = SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20)
    structured_store = StructuredStore(db_path=tmp_path / "hypo.db")
    event_queue = EventQueue()
    pipeline = ChatPipeline(
        router=StreamingRouter(reply),
        chat_model="Gemini3Pro",
        session_memory=session_memory,
        history_window=20,
        event_queue=event_queue,
    )
    app = create_app(
        auth_token="test-token",
        pipeline=pipeline,
        deps=AppDeps(
            session_memory=session_memory,
            structured_store=structured_store,
            event_queue=event_queue,
            scheduler=NoopScheduler(),
        ),
    )
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "skills.yaml").write_text(
        "default_timeout_seconds: 30\nskills:\n  qq:\n    enabled: false\n",
        encoding="utf-8",
    )
    app.state.config_dir = config_dir
    return app


def test_webui_sender_syncs_full_messages_to_other_webui_clients(tmp_path: Path) -> None:
    app = _make_app(tmp_path, reply="HELLO FROM AGENT")

    with TestClient(app) as client:
        with (
            client.websocket_connect("/ws?token=test-token") as sender,
            client.websocket_connect("/ws?token=test-token") as peer,
        ):
            sender.send_json({"text": "hello from web", "sender": "user", "session_id": "main"})

            first = sender.receive_json()
            second = sender.receive_json()
            peer_first = peer.receive_json()
            peer_second = peer.receive_json()

    assert first["type"] == "assistant_chunk"
    assert second["type"] == "assistant_done"
    assert peer_first["text"] == "hello from web"
    assert peer_first["sender"] == "user"
    assert peer_first["channel"] == "webui"
    assert peer_second["text"] == "HELLO FROM AGENT"
    assert peer_second["sender"] == "assistant"
    assert peer_second["channel"] == "webui"


def test_qq_inbound_message_syncs_user_and_reply_to_webui(tmp_path: Path) -> None:
    app = _make_app(tmp_path, reply="QQ ASSISTANT REPLY")

    with TestClient(app) as client:
        service = QQChannelService(
            napcat_http_url="http://localhost:3000",
            bot_qq="123456789",
            allowed_users={"10001"},
        )

        async def fake_send_message(*, user_id: str, message: Message) -> bool:
            del user_id, message
            return True

        service.adapter.send_message = fake_send_message  # type: ignore[method-assign]

        with client.websocket_connect("/ws?token=test-token") as ws:
            asyncio.run(
                service.handle_onebot_event(
                    {
                        "post_type": "message",
                        "message_type": "private",
                        "user_id": "10001",
                        "message": "你好，来自 QQ",
                    },
                    pipeline=client.app.state.pipeline,
                )
            )
            inbound = ws.receive_json()
            outbound = ws.receive_json()

    assert inbound["text"] == "你好，来自 QQ"
    assert inbound["sender"] == "user"
    assert inbound["channel"] == "qq"
    assert outbound["text"] == "QQ ASSISTANT REPLY"
    assert outbound["sender"] == "assistant"
    assert outbound["channel"] == "qq"


def test_webui_origin_conversation_is_mirrored_to_qq_notifications(tmp_path: Path) -> None:
    long_reply = "A" * 520
    app = _make_app(tmp_path, reply=long_reply)
    mirrored: list[str] = []

    class ConnectedQQClient:
        status = "connected"

        def get_status(self) -> dict[str, str]:
            return {"status": "connected"}

        async def stop(self) -> None:
            return None

    class MirrorQQService:
        async def send_message(self, message: Message) -> None:
            mirrored.append(str(message.text or ""))

    with TestClient(app) as client:
        client.app.state.qq_ws_client = ConnectedQQClient()
        client.app.state.qq_channel_service = MirrorQQService()

        with client.websocket_connect("/ws?token=test-token") as ws:
            ws.send_json({"text": "mirror me", "sender": "user", "session_id": "main"})
            ws.receive_json()
            ws.receive_json()

    assert mirrored[0] == "[WebUI] User: mirror me"
    assert mirrored[1] == f"[WebUI] Assistant: {long_reply}"
