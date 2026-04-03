from __future__ import annotations

from fastapi.testclient import TestClient

from hypo_agent.channels.qq_channel import QQChannelService
from hypo_agent.models import Message
class QueuePipelineStub:
    def __init__(self) -> None:
        self.inbounds: list[Message] = []

    async def start_event_consumer(self) -> None:
        return None

    async def stop_event_consumer(self) -> None:
        return None

    async def enqueue_user_message(self, inbound: Message, *, emit):
        self.inbounds.append(inbound)
        await emit(
            {
                "type": "assistant_chunk",
                "text": "ok",
                "sender": "assistant",
                "session_id": inbound.session_id,
            }
        )
        await emit(
            {
                "type": "assistant_done",
                "sender": "assistant",
                "session_id": inbound.session_id,
            }
        )

    async def stream_reply(self, inbound):
        del inbound
        if False:  # pragma: no cover
            yield {}


def _bind_service(app, allowed_users: set[str]) -> None:
    service = QQChannelService(
        napcat_http_url="http://localhost:3000",
        bot_qq="123456789",
        allowed_users=allowed_users,
    )

    async def fake_send_private_text(*, user_id: str, text: str) -> bool:
        del user_id, text
        return True

    service.adapter.send_private_text = fake_send_private_text  # type: ignore[method-assign]
    app.state.qq_channel_service = service
    app.state.qq_config_dir_snapshot = None


def test_qq_ws_accepts_allowed_private_message_and_enqueues_pipeline(app_factory) -> None:
    pipeline = QueuePipelineStub()
    app = app_factory(pipeline=pipeline)
    _bind_service(app, {"10001"})

    with TestClient(app) as client:
        with client.websocket_connect("/ws/qq/onebot") as ws:
            ws.send_json(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "user_id": "10001",
                    "message": "你好",
                }
            )

    assert len(pipeline.inbounds) == 1
    assert pipeline.inbounds[0].text == "你好"
    assert pipeline.inbounds[0].channel == "qq"
    assert pipeline.inbounds[0].sender_id == "10001"


def test_qq_ws_silently_ignores_non_whitelisted_user(app_factory) -> None:
    pipeline = QueuePipelineStub()
    app = app_factory(pipeline=pipeline)
    _bind_service(app, {"10001"})

    with TestClient(app) as client:
        with client.websocket_connect("/ws/qq/onebot") as ws:
            ws.send_json(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "user_id": "10002",
                    "message": "hello",
                }
            )

    assert pipeline.inbounds == []
