import pytest
from fastapi.testclient import TestClient
from litellm.exceptions import InternalServerError
from starlette.websockets import WebSocketDisconnect

class StubPipeline:
    async def stream_reply(self, inbound):
        text = inbound.text or ""
        yield {
            "type": "assistant_chunk",
            "text": text.upper(),
            "sender": "assistant",
            "session_id": inbound.session_id,
        }
        yield {
            "type": "assistant_done",
            "sender": "assistant",
            "session_id": inbound.session_id,
        }


class FailingPipeline:
    async def stream_reply(self, inbound):
        if False:  # pragma: no cover
            yield {}
        raise RuntimeError("fallback chain exhausted")


class TimeoutPipeline:
    async def stream_reply(self, inbound):
        if False:  # pragma: no cover
            yield {}
        raise TimeoutError("llm timeout")


class ProviderErrorPipeline:
    async def stream_reply(self, inbound):
        del inbound
        if False:  # pragma: no cover
            yield {}
        raise InternalServerError(
            message="gateway returned html",
            llm_provider="openai",
            model="openai/gpt-5.4",
        )


class ToolEventPipeline:
    async def stream_reply(self, inbound):
        yield {
            "type": "tool_call_start",
            "tool_name": "exec_command",
            "tool_call_id": "call_1",
            "session_id": inbound.session_id,
        }
        yield {
            "type": "tool_call_result",
            "tool_name": "exec_command",
            "tool_call_id": "call_1",
            "status": "success",
            "result": {"stdout": "ok"},
            "error_info": "",
            "metadata": {},
            "session_id": inbound.session_id,
        }
        yield {
            "type": "assistant_chunk",
            "text": "ok",
            "sender": "assistant",
            "session_id": inbound.session_id,
        }
        yield {
            "type": "assistant_done",
            "sender": "assistant",
            "session_id": inbound.session_id,
        }


class ProgressEventPipeline:
    async def stream_reply(self, inbound, *, event_emitter=None):
        assert event_emitter is not None
        await event_emitter(
            {
                "type": "pipeline_stage",
                "stage": "preprocessing",
                "detail": "正在分析你的消息...",
                "session_id": inbound.session_id,
            }
        )
        yield {
            "type": "assistant_chunk",
            "text": "ok",
            "sender": "assistant",
            "session_id": inbound.session_id,
        }
        yield {
            "type": "assistant_done",
            "sender": "assistant",
            "session_id": inbound.session_id,
        }


class AttachmentPipeline:
    def __init__(self) -> None:
        self.inbound = None

    async def stream_reply(self, inbound):
        self.inbound = inbound
        yield {
            "type": "assistant_chunk",
            "text": "image ok",
            "sender": "assistant",
            "session_id": inbound.session_id,
        }
        yield {
            "type": "assistant_done",
            "sender": "assistant",
            "session_id": inbound.session_id,
        }

def _client(app_factory, token: str = "test-token", pipeline=None) -> TestClient:
    app = app_factory(auth_token=token, pipeline=pipeline or StubPipeline())
    return TestClient(app)


def _assert_local_timestamp(value: object) -> None:
    assert isinstance(value, str)
    assert value.endswith("+08:00")
    assert "T" in value


def test_ws_rejects_missing_token(app_factory) -> None:
    with _client(app_factory) as client:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws"):
                pass
        assert exc_info.value.code == 4401


def test_ws_rejects_invalid_token(app_factory) -> None:
    with _client(app_factory) as client:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws?token=wrong"):
                pass
        assert exc_info.value.code == 4401


def test_ws_streams_valid_message_payload(app_factory) -> None:
    with _client(app_factory) as client:
        with client.websocket_connect("/ws?token=test-token") as ws:
            ws.send_json({"text": "hello", "sender": "user", "session_id": "s1"})
            first = ws.receive_json()
            second = ws.receive_json()
            assert first["type"] == "assistant_chunk"
            assert first["text"] == "HELLO"
            assert first["sender"] == "assistant"
            assert first["session_id"] == "s1"
            _assert_local_timestamp(first.get("timestamp"))

            assert second["type"] == "assistant_done"
            assert second["sender"] == "assistant"
            assert second["session_id"] == "s1"
            _assert_local_timestamp(second.get("timestamp"))


def test_ws_rejects_invalid_message_shape(app_factory) -> None:
    with _client(app_factory) as client:
        with client.websocket_connect("/ws?token=test-token") as ws:
            ws.send_json({"sender": "user"})
            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws.receive_json()
            assert exc_info.value.code == 4400


def test_ws_defaults_missing_session_id_to_main(app_factory) -> None:
    with _client(app_factory) as client:
        with client.websocket_connect("/ws?token=test-token") as ws:
            ws.send_json({"text": "hello", "sender": "user"})
            first = ws.receive_json()
            second = ws.receive_json()
            assert first["type"] == "assistant_chunk"
            assert first["text"] == "HELLO"
            assert first["sender"] == "assistant"
            assert first["session_id"] == "main"
            _assert_local_timestamp(first.get("timestamp"))

            assert second["type"] == "assistant_done"
            assert second["sender"] == "assistant"
            assert second["session_id"] == "main"
            _assert_local_timestamp(second.get("timestamp"))


def test_ws_sends_error_event_on_pipeline_runtime_error(app_factory) -> None:
    with _client(app_factory, pipeline=FailingPipeline()) as client:
        with client.websocket_connect("/ws?token=test-token") as ws:
            ws.send_json({"text": "hello", "sender": "user", "session_id": "s1"})
            event = ws.receive_json()
            assert event == {
                "type": "error",
                "code": "LLM_RUNTIME_ERROR",
                "message": "LLM 调用失败，请检查配置或稍后重试",
                "retryable": True,
                "session_id": "s1",
            }
            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws.receive_json()
            assert exc_info.value.code == 1011


def test_ws_sends_retryable_timeout_error_event(app_factory) -> None:
    with _client(app_factory, pipeline=TimeoutPipeline()) as client:
        with client.websocket_connect("/ws?token=test-token") as ws:
            ws.send_json({"text": "hello", "sender": "user", "session_id": "s1"})
            event = ws.receive_json()
            assert event == {
                "type": "error",
                "code": "LLM_TIMEOUT",
                "message": "LLM 调用超时，请稍后重试",
                "retryable": True,
                "session_id": "s1",
            }
            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws.receive_json()
            assert exc_info.value.code == 1011


def test_ws_maps_provider_api_error_to_llm_runtime_error(app_factory) -> None:
    with _client(app_factory, pipeline=ProviderErrorPipeline()) as client:
        with client.websocket_connect("/ws?token=test-token") as ws:
            ws.send_json({"text": "hello", "sender": "user", "session_id": "s1"})
            event = ws.receive_json()
            assert event == {
                "type": "error",
                "code": "LLM_RUNTIME_ERROR",
                "message": "LLM 调用失败，请检查配置或稍后重试",
                "retryable": True,
                "session_id": "s1",
            }
            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws.receive_json()
            assert exc_info.value.code == 1011


def test_ws_forwards_tool_events_without_modification(app_factory) -> None:
    with _client(app_factory, pipeline=ToolEventPipeline()) as client:
        with client.websocket_connect("/ws?token=test-token") as ws:
            ws.send_json({"text": "hello", "sender": "user", "session_id": "s1"})
            first = ws.receive_json()
            second = ws.receive_json()
            third = ws.receive_json()
            fourth = ws.receive_json()
            assert first["type"] == "tool_call_start"
            assert second["type"] == "tool_call_result"
            assert third["type"] == "assistant_chunk"
            assert fourth["type"] == "assistant_done"
            _assert_local_timestamp(third.get("timestamp"))
            _assert_local_timestamp(fourth.get("timestamp"))


def test_ws_forwards_pipeline_progress_events_via_event_emitter(app_factory) -> None:
    with _client(app_factory, pipeline=ProgressEventPipeline()) as client:
        with client.websocket_connect("/ws?token=test-token") as ws:
            ws.send_json({"text": "hello", "sender": "user", "session_id": "s1"})
            first = ws.receive_json()
            second = ws.receive_json()
            third = ws.receive_json()

            assert first["type"] == "pipeline_stage"
            assert first["stage"] == "preprocessing"
            assert first["session_id"] == "s1"
            _assert_local_timestamp(first.get("timestamp"))
            assert second["type"] == "assistant_chunk"
            assert third["type"] == "assistant_done"


def test_ws_broadcasts_server_timestamped_user_message_to_peer(app_factory) -> None:
    with _client(app_factory) as client:
        with (
            client.websocket_connect("/ws?token=test-token") as sender,
            client.websocket_connect("/ws?token=test-token") as peer,
        ):
            sender.send_json({"text": "hello", "sender": "user", "session_id": "main"})
            echoed_user = peer.receive_json()

            assert echoed_user["text"] == "hello"
            assert echoed_user["sender"] == "user"
            assert echoed_user["session_id"] == "main"
            _assert_local_timestamp(echoed_user.get("timestamp"))

            sender.receive_json()
            sender.receive_json()


def test_ws_accepts_attachment_only_message_payload(app_factory) -> None:
    pipeline = AttachmentPipeline()

    with _client(app_factory, pipeline=pipeline) as client:
        with client.websocket_connect("/ws?token=test-token") as ws:
            ws.send_json(
                {
                    "sender": "user",
                    "session_id": "s1",
                    "attachments": [
                        {
                            "type": "image",
                            "url": "/tmp/demo.png",
                            "filename": "demo.png",
                            "mime_type": "image/png",
                            "size_bytes": 10,
                        }
                    ],
                }
            )
            first = ws.receive_json()
            second = ws.receive_json()

    assert first["type"] == "assistant_chunk"
    assert second["type"] == "assistant_done"
    assert pipeline.inbound is not None
    assert pipeline.inbound.text is None
    assert len(pipeline.inbound.attachments) == 1
    assert pipeline.inbound.attachments[0].type == "image"
