from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from hypo_agent.channels.weixin.ilink_client import ILinkClient, LoginError, SessionExpiredError


def _decode_uin(value: str) -> str:
    return base64.b64decode(value.encode("utf-8")).decode("utf-8")


def test_ilink_client_loads_persisted_auth_state(tmp_path: Path) -> None:
    token_path = tmp_path / "weixin_auth.json"
    token_path.write_text(
        json.dumps(
            {
                "token": "bot-123",
                "bot_token": "bot-123",
                "baseurl": "https://custom.example.com",
                "bot_id": "bot-id-1",
                "user_id": "user-id-1",
                "get_updates_buf": "cursor-1",
            }
        ),
        encoding="utf-8",
    )

    client = ILinkClient("https://ilinkai.weixin.qq.com", token_path=str(token_path))

    assert client.bot_token == "bot-123"
    assert client.base_url == "https://custom.example.com"
    assert client.bot_id == "bot-id-1"
    assert client.user_id == "user-id-1"
    assert client.last_context_token == ""
    assert client.get_updates_buf == "cursor-1"

    asyncio.run(client.close())


def test_ilink_client_login_polls_until_confirmed_and_persists(tmp_path: Path) -> None:
    token_path = tmp_path / "weixin_auth.json"
    sleep_calls: list[float] = []
    qrcode_contents: list[str] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    statuses = iter(
        [
            {"status": "wait"},
            {"status": "scaned"},
            {
                "status": "confirmed",
                "bot_token": "bot-xyz",
                "baseurl": "https://alt.ilink.example.com",
                "ilink_bot_id": "bot-42",
                "ilink_user_id": "user-99",
            },
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/ilink/bot/get_bot_qrcode":
            assert request.method == "GET"
            assert "Authorization" not in request.headers
            assert "AuthorizationType" not in request.headers
            assert "X-WECHAT-UIN" not in request.headers
            assert parse_qs(request.url.query.decode("utf-8")) == {"bot_type": ["3"]}
            return httpx.Response(
                200,
                json={
                    "qrcode": "qrcode-id-1",
                    "qrcode_img_content": "weixin://qr-content-1",
                },
            )

        if request.url.path == "/ilink/bot/get_qrcode_status":
            assert request.method == "GET"
            assert "Authorization" not in request.headers
            assert parse_qs(request.url.query.decode("utf-8")) == {"qrcode": ["qrcode-id-1"]}
            return httpx.Response(200, json=next(statuses))

        raise AssertionError(f"unexpected request path: {request.url.path}")

    client = ILinkClient(
        "https://ilinkai.weixin.qq.com",
        token_path=str(token_path),
        transport=httpx.MockTransport(handler),
        sleep_func=fake_sleep,
    )

    session = asyncio.run(client.login(on_qrcode_content=qrcode_contents.append))

    assert session == {
        "token": "bot-xyz",
        "baseurl": "https://alt.ilink.example.com",
        "bot_id": "bot-42",
        "user_id": "user-99",
    }
    assert client.bot_token == "bot-xyz"
    assert client.base_url == "https://alt.ilink.example.com"
    assert client.bot_id == "bot-42"
    assert client.user_id == "user-99"
    assert qrcode_contents == ["weixin://qr-content-1"]
    assert sleep_calls == [1.0, 1.0]
    assert json.loads(token_path.read_text(encoding="utf-8")) == {
        "token": "bot-xyz",
        "bot_token": "bot-xyz",
        "baseurl": "https://alt.ilink.example.com",
        "bot_id": "bot-42",
        "user_id": "user-99",
        "last_context_token": "",
        "get_updates_buf": "",
    }

    asyncio.run(client.close())


def test_ilink_client_login_refreshes_qrcode_when_expired(tmp_path: Path) -> None:
    token_path = tmp_path / "weixin_auth.json"
    qrcode_contents: list[str] = []
    sleep_calls: list[float] = []
    status_queries: list[str] = []
    qr_requests = 0

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal qr_requests
        if request.url.path == "/ilink/bot/get_bot_qrcode":
            qr_requests += 1
            return httpx.Response(
                200,
                json={
                    "qrcode": f"qrcode-id-{qr_requests}",
                    "qrcode_img_content": f"weixin://qr-content-{qr_requests}",
                },
            )
        if request.url.path == "/ilink/bot/get_qrcode_status":
            qrcode = parse_qs(request.url.query.decode("utf-8"))["qrcode"][0]
            status_queries.append(qrcode)
            if qrcode == "qrcode-id-1":
                return httpx.Response(200, json={"status": "expired"})
            return httpx.Response(
                200,
                json={
                    "status": "confirmed",
                    "bot_token": "bot-xyz",
                    "baseurl": "",
                    "ilink_bot_id": "bot-42",
                    "ilink_user_id": "user-99",
                },
            )
        raise AssertionError(f"unexpected request path: {request.url.path}")

    client = ILinkClient(
        "https://ilinkai.weixin.qq.com",
        token_path=str(token_path),
        transport=httpx.MockTransport(handler),
        sleep_func=fake_sleep,
    )

    session = asyncio.run(client.login(on_qrcode_content=qrcode_contents.append))

    assert session["baseurl"] == "https://ilinkai.weixin.qq.com"
    assert qrcode_contents == ["weixin://qr-content-1", "weixin://qr-content-2"]
    assert status_queries == ["qrcode-id-1", "qrcode-id-2"]
    assert sleep_calls == [1.0]

    asyncio.run(client.close())


def test_ilink_client_get_updates_filters_bot_messages_and_persists_cursor(tmp_path: Path) -> None:
    token_path = tmp_path / "weixin_auth.json"
    token_path.write_text(
        json.dumps(
            {
                "bot_token": "bot-123",
                "baseurl": "https://custom.example.com",
                "get_updates_buf": "cursor-1",
            }
        ),
        encoding="utf-8",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/ilink/bot/getupdates"
        assert request.headers["Authorization"] == "Bearer bot-123"
        assert request.headers["AuthorizationType"] == "ilink_bot_token"
        assert _decode_uin(request.headers["X-WECHAT-UIN"]).isdigit()
        payload = json.loads(request.content.decode("utf-8"))
        assert payload == {
            "get_updates_buf": "cursor-1",
            "base_info": {"channel_version": "1.0.2"},
        }
        assert request.headers["Content-Length"] == str(len(request.content))
        return httpx.Response(
            200,
            json={
                "ret": 0,
                "msgs": [
                    {
                        "message_id": 1,
                        "from_user_id": "user@im.wechat",
                        "message_type": 1,
                        "context_token": "ctx-1",
                    },
                    {
                        "message_id": 2,
                        "from_user_id": "bot@im.bot",
                        "message_type": 2,
                        "context_token": "ctx-2",
                    },
                ],
                "get_updates_buf": "cursor-2",
                "longpolling_timeout_ms": 35000,
            },
        )

    client = ILinkClient(
        "https://ilinkai.weixin.qq.com",
        token_path=str(token_path),
        transport=httpx.MockTransport(handler),
    )

    messages = asyncio.run(client.get_updates())

    assert messages == [
        {
            "message_id": 1,
            "from_user_id": "user@im.wechat",
            "message_type": 1,
            "context_token": "ctx-1",
        }
    ]
    assert client.get_updates_buf == "cursor-2"
    assert json.loads(token_path.read_text(encoding="utf-8")) == {
        "token": "bot-123",
        "bot_token": "bot-123",
        "baseurl": "https://custom.example.com",
        "bot_id": "",
        "user_id": "",
        "last_context_token": "",
        "get_updates_buf": "cursor-2",
    }

    asyncio.run(client.close())


def test_ilink_client_get_updates_raises_session_expired(tmp_path: Path) -> None:
    token_path = tmp_path / "weixin_auth.json"
    token_path.write_text(json.dumps({"bot_token": "bot-123"}), encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/ilink/bot/getupdates"
        return httpx.Response(200, json={"errcode": -14, "errmsg": "session expired"})

    client = ILinkClient(
        "https://ilinkai.weixin.qq.com",
        token_path=str(token_path),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(SessionExpiredError):
        asyncio.run(client.get_updates())

    asyncio.run(client.close())


def test_ilink_client_retries_network_errors_with_exponential_backoff(tmp_path: Path) -> None:
    token_path = tmp_path / "weixin_auth.json"
    token_path.write_text(
        json.dumps({"bot_token": "bot-123", "baseurl": "https://custom.example.com"}),
        encoding="utf-8",
    )
    sleep_calls: list[float] = []
    attempts = 0

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 4:
            raise httpx.ReadError("temporary network failure", request=request)
        assert request.url.path == "/ilink/bot/getconfig"
        return httpx.Response(200, json={"ret": 0, "typing_ticket": "ticket-1"})

    client = ILinkClient(
        "https://ilinkai.weixin.qq.com",
        token_path=str(token_path),
        transport=httpx.MockTransport(handler),
        sleep_func=fake_sleep,
    )

    payload = asyncio.run(client.get_config("user@im.wechat"))

    assert payload["typing_ticket"] == "ticket-1"
    assert attempts == 4
    assert sleep_calls == [1.0, 2.0, 4.0]

    asyncio.run(client.close())


def test_ilink_client_send_message_omits_context_token_when_none(tmp_path: Path) -> None:
    token_path = tmp_path / "weixin_auth.json"
    token_path.write_text(
        json.dumps({"bot_token": "bot-123", "baseurl": "https://custom.example.com"}),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("Authorization")
        captured["content_length"] = request.headers.get("Content-Length")
        captured["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"ret": 0})

    client = ILinkClient(
        "https://ilinkai.weixin.qq.com",
        token_path=str(token_path),
        transport=httpx.MockTransport(handler),
    )

    client_id = asyncio.run(
        client.send_message(
            "user@im.wechat",
            "hello",
            context_token=None,
        )
    )

    assert client_id.startswith("wcb-")
    assert captured["authorization"] == "Bearer bot-123"
    payload = captured["payload"]
    assert payload["msg"]["to_user_id"] == "user@im.wechat"
    assert payload["msg"]["client_id"] == client_id
    assert payload["msg"]["message_type"] == 2
    assert payload["msg"]["message_state"] == 2
    assert payload["msg"]["item_list"] == [{"type": 1, "text_item": {"text": "hello"}}]
    assert payload["base_info"] == {"channel_version": "1.0.2"}
    assert "context_token" not in payload["msg"]
    assert captured["content_length"] == str(len(json.dumps(payload).encode("utf-8")))

    asyncio.run(client.close())


def test_ilink_client_send_typing_fetches_ticket_before_sending(tmp_path: Path) -> None:
    token_path = tmp_path / "weixin_auth.json"
    token_path.write_text(
        json.dumps({"bot_token": "bot-123", "baseurl": "https://custom.example.com"}),
        encoding="utf-8",
    )
    sendtyping_payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        if request.url.path == "/ilink/bot/getconfig":
            assert payload == {
                "ilink_user_id": "user@im.wechat",
                "base_info": {"channel_version": "1.0.2"},
            }
            return httpx.Response(200, json={"ret": 0, "typing_ticket": "ticket-1"})
        if request.url.path == "/ilink/bot/sendtyping":
            sendtyping_payloads.append(payload)
            return httpx.Response(200, json={"ret": 0})
        raise AssertionError(f"unexpected request path: {request.url.path}")

    client = ILinkClient(
        "https://ilinkai.weixin.qq.com",
        token_path=str(token_path),
        transport=httpx.MockTransport(handler),
    )

    asyncio.run(client.send_typing("user@im.wechat", status=2))

    assert sendtyping_payloads == [
        {
            "ilink_user_id": "user@im.wechat",
            "typing_ticket": "ticket-1",
            "status": 2,
            "base_info": {"channel_version": "1.0.2"},
        }
    ]

    asyncio.run(client.close())


def test_ilink_client_request_post_omits_authorization_when_token_missing(tmp_path: Path) -> None:
    token_path = tmp_path / "weixin_auth.json"
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("Authorization")
        captured["content_length"] = request.headers.get("Content-Length")
        captured["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"ret": 0})

    client = ILinkClient(
        "https://ilinkai.weixin.qq.com",
        token_path=str(token_path),
        transport=httpx.MockTransport(handler),
    )

    response = asyncio.run(client._request_post("/ilink/bot/test", {"hello": "world"}, token=None))

    assert response == {"ret": 0}
    assert captured["authorization"] is None
    assert captured["payload"] == {
        "hello": "world",
        "base_info": {"channel_version": "1.0.2"},
    }
    assert captured["content_length"] == str(len(json.dumps(captured["payload"]).encode("utf-8")))

    asyncio.run(client.close())


def test_ilink_client_get_upload_url_includes_media_metadata(tmp_path: Path) -> None:
    token_path = tmp_path / "weixin_auth.json"
    token_path.write_text(
        json.dumps({"bot_token": "bot-123", "baseurl": "https://custom.example.com"}),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"ret": 0, "upload_param": "upload-param-1"})

    client = ILinkClient(
        "https://ilinkai.weixin.qq.com",
        token_path=str(token_path),
        transport=httpx.MockTransport(handler),
    )

    payload = asyncio.run(
        client.get_upload_url(
            filekey="filekey-1",
            media_type=1,
            to_user_id="user@im.wechat",
            rawsize=64,
            rawfilemd5="0123456789abcdef0123456789abcdef",
            filesize=80,
            aeskey="00112233445566778899aabbccddeeff",
        )
    )

    assert payload["upload_param"] == "upload-param-1"
    assert captured["payload"] == {
        "filekey": "filekey-1",
        "media_type": 1,
        "to_user_id": "user@im.wechat",
        "rawsize": 64,
        "rawfilemd5": "0123456789abcdef0123456789abcdef",
        "filesize": 80,
        "no_need_thumb": True,
        "aeskey": "00112233445566778899aabbccddeeff",
        "base_info": {"channel_version": "1.0.2"},
    }
    headers = captured["headers"]
    assert headers["authorization"] == "Bearer bot-123"
    assert headers["content-length"] == str(len(json.dumps(captured["payload"]).encode("utf-8")))

    asyncio.run(client.close())


def test_ilink_client_upload_media_posts_ciphertext_to_cdn(tmp_path: Path) -> None:
    token_path = tmp_path / "weixin_auth.json"
    token_path.write_text(
        json.dumps({"bot_token": "bot-123", "baseurl": "https://custom.example.com"}),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["content"] = bytes(request.content)
        return httpx.Response(200, headers={"x-encrypted-param": "download-param-1"})

    client = ILinkClient(
        "https://ilinkai.weixin.qq.com",
        token_path=str(token_path),
        transport=httpx.MockTransport(handler),
    )

    encrypt_query_param = asyncio.run(
        client.upload_media(
            upload_param="upload-param-1",
            filekey="filekey-1",
            encrypted_data=b"ciphertext",
        )
    )

    assert encrypt_query_param == "download-param-1"
    assert captured["method"] == "POST"
    assert (
        captured["url"]
        == "https://novac2c.cdn.weixin.qq.com/c2c/upload?encrypted_query_param=upload-param-1&filekey=filekey-1"
    )
    assert captured["headers"]["content-type"] == "application/octet-stream"
    assert captured["headers"]["content-length"] == str(len(b"ciphertext"))
    assert captured["content"] == b"ciphertext"

    asyncio.run(client.close())


def test_ilink_client_send_image_posts_image_item(tmp_path: Path) -> None:
    token_path = tmp_path / "weixin_auth.json"
    token_path.write_text(
        json.dumps({"bot_token": "bot-123", "baseurl": "https://custom.example.com"}),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"ret": 0})

    client = ILinkClient(
        "https://ilinkai.weixin.qq.com",
        token_path=str(token_path),
        transport=httpx.MockTransport(handler),
    )

    asyncio.run(
        client.send_image(
            to_user_id="user@im.wechat",
            encrypt_query_param="download-param-1",
            aes_key="YWJjZGVmMDEyMzQ1Njc4OWFiY2RlZg==",
            encrypted_file_size=1024,
            context_token="ctx-1",
        )
    )

    payload = captured["payload"]
    assert payload["msg"]["to_user_id"] == "user@im.wechat"
    assert payload["msg"]["message_type"] == 2
    assert payload["msg"]["message_state"] == 2
    assert payload["msg"]["context_token"] == "ctx-1"
    assert payload["msg"]["item_list"] == [
        {
            "type": 2,
            "image_item": {
                "media": {
                    "encrypt_query_param": "download-param-1",
                    "aes_key": "YWJjZGVmMDEyMzQ1Njc4OWFiY2RlZg==",
                    "encrypt_type": 1,
                },
                "mid_size": 1024,
            },
        }
    ]

    asyncio.run(client.close())
