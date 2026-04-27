from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from hypo_agent.gateway.app import AppDeps, create_app
from hypo_agent.memory.session import SessionMemory
from hypo_agent.memory.structured_store import StructuredStore
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


def _build_app(tmp_path: Path) -> tuple[TestClient, QueuePipelineStub]:
    pipeline = QueuePipelineStub()
    deps = AppDeps(
        session_memory=SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20),
        structured_store=StructuredStore(db_path=tmp_path / "hypo.db"),
    )
    app = create_app(auth_token="test-token", pipeline=pipeline, deps=deps)
    return TestClient(app), pipeline


def _seed_qqbot_config(config_dir: Path) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "skills.yaml").write_text(
        """
default_timeout_seconds: 30
skills:
  qq:
    enabled: false
    deprecated: true
  qq_bot:
    enabled: true
""".strip(),
        encoding="utf-8",
    )
    (config_dir / "secrets.yaml").write_text(
        """
providers: {}
services:
  qq_bot:
    app_id: "1029384756"
    app_secret: "bot-secret-xyz"
    enabled: true
""".strip(),
        encoding="utf-8",
    )


def _sign(secret: str, timestamp: str, body: bytes) -> str:
    from hypo_agent.channels.qq_bot_channel import QQBotChannelService

    return QQBotChannelService.build_signature_hex(secret=secret, timestamp=timestamp, body=body)


def test_qqbot_verify_signature_accepts_valid_signature() -> None:
    from hypo_agent.channels.qq_bot_channel import QQBotChannelService

    body = b'{"op":0,"t":"C2C_MESSAGE_CREATE","d":{"content":"hello"}}'
    timestamp = "1711459200"
    signature = _sign("bot-secret-xyz", timestamp, body)

    assert QQBotChannelService.verify_signature(
        secret="bot-secret-xyz",
        timestamp=timestamp,
        signature=signature,
        body=body,
    )


def test_qqbot_verify_signature_rejects_invalid_signature() -> None:
    from hypo_agent.channels.qq_bot_channel import QQBotChannelService

    assert not QQBotChannelService.verify_signature(
        secret="bot-secret-xyz",
        timestamp="1711459200",
        signature="00" * 64,
        body=b'{"msg":"tampered"}',
    )


def test_qqbot_handle_event_accepts_c2c_message_and_strips_mentions(tmp_path: Path) -> None:
    from hypo_agent.channels.qq_bot_channel import QQBotChannelService

    _, pipeline = _build_app(tmp_path)

    payload = {
        "op": 0,
        "t": "C2C_MESSAGE_CREATE",
        "d": {
            "id": "msg-001",
            "content": "<@!1029384756> 你好",
            "timestamp": "2026-03-26T10:00:00+08:00",
            "author": {
                "id": "author-1",
                "user_openid": "OPENID-C2C-001",
            },
        },
    }
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    service = QQBotChannelService(app_id="1029384756", app_secret="bot-secret-xyz")

    handled = asyncio.run(service.handle_event(json.loads(raw.decode("utf-8")), pipeline=pipeline))

    assert handled is True
    assert len(pipeline.inbounds) == 1
    inbound = pipeline.inbounds[0]
    assert inbound.text == "你好"
    assert inbound.channel == "qq"
    assert inbound.sender_id == "OPENID-C2C-001"
    assert inbound.metadata["qq"]["msg_id"] == "msg-001"


def test_qqbot_webhook_challenge_returns_signed_plain_token(tmp_path: Path) -> None:
    from hypo_agent.channels.qq_bot_channel import QQBotChannelService

    payload = {
        "op": 13,
        "d": {
            "plain_token": "test_token",
            "event_ts": "1234567890",
        },
    }

    service = QQBotChannelService(app_id="1029384756", app_secret="bot-secret-xyz")
    response = asyncio.run(
        service.handle_webhook_request(
            body=json.dumps(payload).encode("utf-8"),
            signature="",
            timestamp="",
            pipeline=None,
        )
    )

    assert response[0] == 200
    body = response[1]
    assert body["plain_token"] == "test_token"
    assert body["signature"] == QQBotChannelService.build_signature_hex(
        secret="bot-secret-xyz",
        timestamp="1234567890",
        body=b"test_token",
    )


def test_qqbot_webhook_rejects_invalid_signature(tmp_path: Path) -> None:
    from hypo_agent.channels.qq_bot_channel import QQBotChannelService

    payload = {"op": 0, "t": "C2C_MESSAGE_CREATE", "d": {"id": "msg-001"}}
    raw = json.dumps(payload).encode("utf-8")
    service = QQBotChannelService(app_id="1029384756", app_secret="bot-secret-xyz")

    response = asyncio.run(
        service.handle_webhook_request(
            body=raw,
            signature="00" * 64,
            timestamp="1711459200",
            pipeline=None,
        )
    )

    assert response[0] == 403


def test_qqbot_send_message_fetches_token_and_sends_text(monkeypatch) -> None:
    from hypo_agent.channels.qq_bot_channel import QQBotChannelService, clear_qqbot_token_cache

    clear_qqbot_token_cache()
    calls: list[tuple[str, str, dict[str, object]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8")) if request.content else {}
        calls.append((request.method, str(request.url), payload))
        if request.url.path.endswith("/getAppAccessToken"):
            return httpx.Response(
                200,
                json={"access_token": "token-1", "expires_in": 7200},
            )
        assert request.headers["Authorization"] == "QQBot token-1"
        return httpx.Response(200, json={"id": "reply-1", "timestamp": "1711459200"})

    transport = httpx.MockTransport(handler)

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("hypo_agent.channels.qq_bot_channel.httpx.AsyncClient", MockAsyncClient)

    service = QQBotChannelService(app_id="1029384756", app_secret="bot-secret-xyz")
    asyncio.run(
        service.send_message(
            Message(
                text="**测试**",
                sender="assistant",
                session_id="main",
                channel="qq",
                sender_id="OPENID-C2C-001",
                metadata={"qq": {"msg_id": "msg-001"}},
            )
        )
    )

    assert calls[0][1] == "https://bots.qq.com/app/getAppAccessToken"
    assert calls[1][1].endswith("/v2/users/OPENID-C2C-001/messages")
    assert calls[1][2]["msg_id"] == "msg-001"
    assert calls[1][2]["msg_type"] == 2
    assert calls[1][2]["markdown"] == {"content": "**测试**"}


def test_qqbot_send_message_retries_once_after_token_expiry(monkeypatch) -> None:
    from hypo_agent.channels.qq_bot_channel import QQBotChannelService, clear_qqbot_token_cache

    clear_qqbot_token_cache()
    calls: list[tuple[str, str, dict[str, object]]] = []
    token_requests = {"count": 0}
    message_requests = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8")) if request.content else {}
        calls.append((request.method, str(request.url), payload))
        if request.url.path.endswith("/getAppAccessToken"):
            token_requests["count"] += 1
            return httpx.Response(
                200,
                json={"access_token": f"token-{token_requests['count']}", "expires_in": 7200},
            )

        message_requests["count"] += 1
        if message_requests["count"] == 1:
            return httpx.Response(401, json={"message": "token expired"})
        assert request.headers["Authorization"] == "QQBot token-2"
        return httpx.Response(200, json={"id": "reply-2", "timestamp": "1711459201"})

    transport = httpx.MockTransport(handler)

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("hypo_agent.channels.qq_bot_channel.httpx.AsyncClient", MockAsyncClient)

    service = QQBotChannelService(app_id="1029384756", app_secret="bot-secret-xyz")
    asyncio.run(
        service.send_message(
            Message(
                text="retry",
                sender="assistant",
                session_id="main",
                channel="qq",
                sender_id="OPENID-C2C-001",
                metadata={"qq": {"msg_id": "msg-001"}},
            )
        )
    )

    assert token_requests["count"] == 2
    assert message_requests["count"] == 2
    assert calls[-1][1].endswith("/v2/users/OPENID-C2C-001/messages")


def test_qqbot_send_message_uses_persisted_openid_and_ignores_non_qq_sender_id(monkeypatch) -> None:
    from hypo_agent.channels.qq_bot_channel import QQBotChannelService, clear_qqbot_token_cache

    clear_qqbot_token_cache()
    calls: list[tuple[str, str, dict[str, object]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8")) if request.content else {}
        calls.append((request.method, str(request.url), payload))
        if request.url.path.endswith("/getAppAccessToken"):
            return httpx.Response(200, json={"access_token": "token-1", "expires_in": 7200})
        return httpx.Response(200, json={"id": "reply-1", "timestamp": "1711459200"})

    transport = httpx.MockTransport(handler)

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    async def load_openid() -> str | None:
        return "OPENID-C2C-001"

    monkeypatch.setattr("hypo_agent.channels.qq_bot_channel.httpx.AsyncClient", MockAsyncClient)

    service = QQBotChannelService(
        app_id="1029384756",
        app_secret="bot-secret-xyz",
        load_target_openid=load_openid,
    )
    asyncio.run(
        service.send_message(
            Message(
                text="跨通道消息",
                sender="assistant",
                session_id="main",
                channel="weixin",
                sender_id="o9cq808jv68ZmOGLuAh4Yt0rna6g@im.wechat",
            )
        )
    )

    assert calls[1][1].endswith("/v2/users/OPENID-C2C-001/messages")
    assert "im.wechat" not in calls[1][1]


def test_qqbot_handle_event_persists_openid(monkeypatch) -> None:
    from hypo_agent.channels.qq_bot_channel import QQBotChannelService

    persisted: list[str] = []

    async def save_openid(openid: str) -> None:
        persisted.append(openid)

    service = QQBotChannelService(
        app_id="1029384756",
        app_secret="bot-secret-xyz",
        save_target_openid=save_openid,
    )
    pipeline = QueuePipelineStub()
    payload = {
        "op": 0,
        "t": "C2C_MESSAGE_CREATE",
        "d": {
            "id": "msg-001",
            "content": "你好",
            "timestamp": "2026-03-26T10:00:00+08:00",
            "author": {
                "user_openid": "OPENID-C2C-001",
            },
        },
    }

    handled = asyncio.run(service.handle_event(payload, pipeline=pipeline))

    assert handled is True
    assert persisted == ["OPENID-C2C-001"]


def test_qqbot_send_message_falls_back_to_text_when_image_upload_fails(tmp_path: Path, monkeypatch) -> None:
    from hypo_agent.channels.qq_bot_channel import QQBotChannelService, clear_qqbot_token_cache

    clear_qqbot_token_cache()
    image_path = tmp_path / "cat.png"
    image_path.write_bytes(b"fake-image")
    calls: list[tuple[str, str, dict[str, object]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8")) if request.content else {}
        calls.append((request.method, str(request.url), payload))
        if request.url.path.endswith("/getAppAccessToken"):
            return httpx.Response(200, json={"access_token": "token-1", "expires_in": 7200})
        if request.url.path.endswith("/files"):
            return httpx.Response(500, json={"message": "upload failed"})
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"id": "reply-1", "timestamp": "1711459200"})
        raise AssertionError(f"unexpected request: {request.url}")

    transport = httpx.MockTransport(handler)

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("hypo_agent.channels.qq_bot_channel.httpx.AsyncClient", MockAsyncClient)

    service = QQBotChannelService(app_id="1029384756", app_secret="bot-secret-xyz")
    asyncio.run(
        service.send_message(
            Message(
                text="请看截图",
                sender="assistant",
                session_id="main",
                channel="qq",
                sender_id="OPENID-C2C-001",
                attachments=[{"type": "image", "url": str(image_path), "filename": "cat.png"}],
            )
        )
    )

    assert calls[-1][1].endswith("/v2/users/OPENID-C2C-001/messages")
    assert calls[-1][2]["msg_type"] == 0
    assert "[图片] cat.png" in str(calls[-1][2]["content"])


def test_qqbot_send_message_uses_public_file_url_for_local_images(tmp_path: Path, monkeypatch) -> None:
    from hypo_agent.channels.qq_bot_channel import QQBotChannelService, clear_qqbot_token_cache

    clear_qqbot_token_cache()
    image_path = tmp_path / "cat.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    calls: list[tuple[str, str, dict[str, object]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8")) if request.content else {}
        calls.append((request.method, str(request.url), payload))
        if request.url.path.endswith("/getAppAccessToken"):
            return httpx.Response(200, json={"access_token": "token-1", "expires_in": 7200})
        if request.url.path.endswith("/files"):
            return httpx.Response(200, json={"file_info": "file-info-1"})
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"id": "reply-1", "timestamp": "1711459200"})
        raise AssertionError(f"unexpected request: {request.url}")

    transport = httpx.MockTransport(handler)

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("hypo_agent.channels.qq_bot_channel.httpx.AsyncClient", MockAsyncClient)

    service = QQBotChannelService(
        app_id="1029384756",
        app_secret="bot-secret-xyz",
        public_base_url="https://bot.example.com",
        public_file_token="token-abc",
    )
    asyncio.run(
        service.send_message(
            Message(
                text="请看截图",
                sender="assistant",
                session_id="main",
                channel="qq",
                sender_id="OPENID-C2C-001",
                attachments=[{"type": "image", "url": str(image_path), "filename": "cat.png"}],
            )
        )
    )

    upload_call = next(call for call in calls if call[1].endswith("/v2/users/OPENID-C2C-001/files"))
    assert upload_call[1].endswith("/v2/users/OPENID-C2C-001/files")
    assert "url" in upload_call[2]
    assert upload_call[2]["url"].startswith("https://bot.example.com/api/files?")
    assert "token=token-abc" in str(upload_call[2]["url"])
    assert "path=" in str(upload_call[2]["url"])


def test_qqbot_send_message_uploads_file_attachment(tmp_path: Path, monkeypatch) -> None:
    from hypo_agent.channels.qq_bot_channel import QQBotChannelService, clear_qqbot_token_cache

    clear_qqbot_token_cache()
    export_path = tmp_path / "notion-export.md"
    export_path.write_text("# Notion Export\n", encoding="utf-8")
    calls: list[tuple[str, str, dict[str, object]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8")) if request.content else {}
        calls.append((request.method, str(request.url), payload))
        if request.url.path.endswith("/getAppAccessToken"):
            return httpx.Response(200, json={"access_token": "token-1", "expires_in": 7200})
        if request.url.path.endswith("/files"):
            return httpx.Response(200, json={"file_info": "file-info-1"})
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"id": "reply-1", "timestamp": "1711459200"})
        raise AssertionError(f"unexpected request: {request.url}")

    transport = httpx.MockTransport(handler)

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("hypo_agent.channels.qq_bot_channel.httpx.AsyncClient", MockAsyncClient)

    service = QQBotChannelService(app_id="1029384756", app_secret="bot-secret-xyz")
    result = asyncio.run(
        service.send_message(
            Message(
                text="已导出 Notion 文件。",
                sender="assistant",
                session_id="main",
                channel="qq",
                sender_id="OPENID-C2C-001",
                attachments=[
                    {
                        "type": "file",
                        "url": str(export_path),
                        "filename": "notion-export.md",
                        "mime_type": "text/markdown",
                    }
                ],
            )
        )
    )

    assert result.success is True
    upload_call = next(call for call in calls if call[1].endswith("/v2/users/OPENID-C2C-001/files"))
    assert upload_call[2]["file_type"] == 4
    assert upload_call[2]["srv_send_msg"] is False
    assert upload_call[2]["file_data"] == "IyBOb3Rpb24gRXhwb3J0Cg=="

    media_call = calls[-1]
    assert media_call[1].endswith("/v2/users/OPENID-C2C-001/messages")
    assert media_call[2]["msg_type"] == 7
    assert media_call[2]["media"] == {"file_info": "file-info-1"}


def test_qqbot_send_message_deduplicates_attachment_and_legacy_file(tmp_path: Path, monkeypatch) -> None:
    from hypo_agent.channels.qq_bot_channel import QQBotChannelService, clear_qqbot_token_cache

    clear_qqbot_token_cache()
    export_path = tmp_path / "notion-export.md"
    export_path.write_text("# Notion Export\n", encoding="utf-8")
    calls: list[tuple[str, str, dict[str, object]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8")) if request.content else {}
        calls.append((request.method, str(request.url), payload))
        if request.url.path.endswith("/getAppAccessToken"):
            return httpx.Response(200, json={"access_token": "token-1", "expires_in": 7200})
        if request.url.path.endswith("/files"):
            return httpx.Response(200, json={"file_info": "file-info-1"})
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"id": "reply-1", "timestamp": "1711459200"})
        raise AssertionError(f"unexpected request: {request.url}")

    transport = httpx.MockTransport(handler)

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("hypo_agent.channels.qq_bot_channel.httpx.AsyncClient", MockAsyncClient)

    service = QQBotChannelService(app_id="1029384756", app_secret="bot-secret-xyz")
    result = asyncio.run(
        service.send_message(
            Message(
                text="已导出 Notion 文件。",
                sender="assistant",
                session_id="main",
                channel="qq",
                sender_id="OPENID-C2C-001",
                attachments=[
                    {
                        "type": "file",
                        "url": str(export_path),
                        "filename": "notion-export.md",
                        "mime_type": "text/markdown",
                    }
                ],
                file=str(export_path),
            )
        )
    )

    assert result.success is True
    upload_calls = [call for call in calls if call[1].endswith("/v2/users/OPENID-C2C-001/files")]
    media_calls = [
        call
        for call in calls
        if call[1].endswith("/v2/users/OPENID-C2C-001/messages") and call[2].get("msg_type") == 7
    ]
    assert len(upload_calls) == 1
    assert len(media_calls) == 1


def test_qqbot_send_message_uses_named_public_file_url_for_local_files(tmp_path: Path, monkeypatch) -> None:
    from hypo_agent.channels.qq_bot_channel import QQBotChannelService, clear_qqbot_token_cache

    clear_qqbot_token_cache()
    export_path = tmp_path / "notion-export.md"
    export_path.write_text("# Notion Export\n", encoding="utf-8")
    calls: list[tuple[str, str, dict[str, object]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8")) if request.content else {}
        calls.append((request.method, str(request.url), payload))
        if request.url.path.endswith("/getAppAccessToken"):
            return httpx.Response(200, json={"access_token": "token-1", "expires_in": 7200})
        if request.url.path.endswith("/files"):
            return httpx.Response(200, json={"file_info": "file-info-1"})
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"id": "reply-1", "timestamp": "1711459200"})
        raise AssertionError(f"unexpected request: {request.url}")

    transport = httpx.MockTransport(handler)

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("hypo_agent.channels.qq_bot_channel.httpx.AsyncClient", MockAsyncClient)

    service = QQBotChannelService(
        app_id="1029384756",
        app_secret="bot-secret-xyz",
        public_base_url="https://bot.example.com",
        public_file_token="token-abc",
    )
    result = asyncio.run(
        service.send_message(
            Message(
                text="已导出 Notion 文件。",
                sender="assistant",
                session_id="main",
                channel="qq",
                sender_id="OPENID-C2C-001",
                attachments=[
                    {
                        "type": "file",
                        "url": str(export_path),
                        "filename": "notion-export.md",
                        "mime_type": "text/markdown",
                    }
                ],
            )
        )
    )

    assert result.success is True
    upload_call = next(call for call in calls if call[1].endswith("/v2/users/OPENID-C2C-001/files"))
    assert upload_call[2]["url"].startswith("https://bot.example.com/api/files/notion-export.md?")
    assert "token=token-abc" in str(upload_call[2]["url"])
    assert "path=" in str(upload_call[2]["url"])
    assert "file_data" not in upload_call[2]


def test_qqbot_legacy_send_message_uploads_file_attachment(tmp_path: Path, monkeypatch) -> None:
    from hypo_agent.channels.qq_bot_channel import QQBotChannelService, clear_qqbot_token_cache

    clear_qqbot_token_cache()
    export_path = tmp_path / "notion-export.md"
    export_path.write_text("# Notion Export\n", encoding="utf-8")
    calls: list[tuple[str, str, dict[str, object]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8")) if request.content else {}
        calls.append((request.method, str(request.url), payload))
        if request.url.path.endswith("/getAppAccessToken"):
            return httpx.Response(200, json={"access_token": "token-1", "expires_in": 7200})
        if request.url.path.endswith("/files"):
            return httpx.Response(200, json={"file_info": "file-info-1"})
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"id": "reply-1", "timestamp": "1711459200"})
        raise AssertionError(f"unexpected request: {request.url}")

    transport = httpx.MockTransport(handler)

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("hypo_agent.channels.qq_bot_channel.httpx.AsyncClient", MockAsyncClient)

    service = QQBotChannelService(
        app_id="1029384756",
        app_secret="bot-secret-xyz",
        markdown_mode="disabled",
    )
    result = asyncio.run(
        service.send_message(
            Message(
                text="已导出 Notion 文件。",
                sender="assistant",
                session_id="main",
                channel="qq",
                sender_id="OPENID-C2C-001",
                attachments=[
                    {
                        "type": "file",
                        "url": str(export_path),
                        "filename": "notion-export.md",
                    }
                ],
            )
        )
    )

    assert result.success is True
    assert any(call[1].endswith("/v2/users/OPENID-C2C-001/files") for call in calls)
    assert calls[-1][2]["msg_type"] == 7
    assert calls[-1][2]["media"] == {"file_info": "file-info-1"}


def test_qqbot_send_message_merges_adjacent_text_segments_around_images(tmp_path: Path, monkeypatch) -> None:
    from hypo_agent.channels.qq_bot_channel import QQBotChannelService

    image_path = tmp_path / "table.png"
    image_path.write_bytes(b"png")
    send_order: list[tuple[str, dict[str, object]]] = []
    image_calls: list[dict[str, object]] = []
    text_calls: list[dict[str, object]] = []

    async def fake_resolve_openid(*, message, qq_meta):
        del message, qq_meta
        return "OPENID-C2C-001"

    async def fake_send_image_with_fallback(**kwargs) -> None:
        send_order.append(("image", kwargs))
        image_calls.append(kwargs)

    async def fake_send_text_with_markdown_fallback(**kwargs) -> None:
        send_order.append(("text", kwargs))
        text_calls.append(kwargs)

    async def fake_render_markdown_segments(_message):
        return [
            {"type": "text", "text": "前文"},
            {"type": "image", "source": str(image_path), "name": "table.png"},
            {"type": "text", "text": "后文一"},
            {"type": "text", "text": "后文二"},
        ]

    service = QQBotChannelService(app_id="1029384756", app_secret="bot-secret-xyz")
    monkeypatch.setattr(service, "_resolve_openid", fake_resolve_openid)
    monkeypatch.setattr(service, "_render_markdown_segments", fake_render_markdown_segments)
    monkeypatch.setattr(service, "_send_image_with_fallback", fake_send_image_with_fallback)
    monkeypatch.setattr(service, "_send_text_with_markdown_fallback", fake_send_text_with_markdown_fallback)

    result = asyncio.run(
        service.send_message(
            Message(
                text="ignored",
                sender="assistant",
                session_id="main",
                channel="qq",
                sender_id="OPENID-C2C-001",
            )
        )
    )

    assert result.success is True
    assert result.segment_count == 4
    assert image_calls == [
        {
            "route_kind": "c2c",
            "openid": "OPENID-C2C-001",
            "guild_id": None,
            "msg_id": None,
            "text": None,
            "image_source": str(image_path),
            "fallback_text": "[图片] table.png",
        }
    ]
    assert text_calls == [
        {
            "route_kind": "c2c",
            "openid": "OPENID-C2C-001",
            "guild_id": None,
            "msg_id": None,
            "text": "前文",
        },
        {
            "route_kind": "c2c",
            "openid": "OPENID-C2C-001",
            "guild_id": None,
            "msg_id": None,
            "text": "后文一\n后文二",
        }
    ]
    assert send_order == [
        (
            "text",
            {
                "route_kind": "c2c",
                "openid": "OPENID-C2C-001",
                "guild_id": None,
                "msg_id": None,
                "text": "前文",
            },
        ),
        (
            "image",
            {
                "route_kind": "c2c",
                "openid": "OPENID-C2C-001",
                "guild_id": None,
                "msg_id": None,
                "text": None,
                "image_source": str(image_path),
                "fallback_text": "[图片] table.png",
            },
        ),
        (
            "text",
            {
                "route_kind": "c2c",
                "openid": "OPENID-C2C-001",
                "guild_id": None,
                "msg_id": None,
                "text": "后文一\n后文二",
            },
        ),
    ]
    assert all("[图片]" not in str(call["text"]) for call in text_calls)
