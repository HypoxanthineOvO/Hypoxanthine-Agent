from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from hypo_agent.channels.info.wewe_rss_client import (
    WeWeRSSAuthError,
    WeWeRSSClient,
    WeWeRSSProtocolError,
)


def test_wewe_rss_client_list_accounts_uses_auth_header() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/trpc/account.list"
        assert request.headers["Authorization"] == "auth-code-1"
        assert request.url.params["batch"] == "1"
        return httpx.Response(
            200,
            json=[
                {
                    "result": {
                        "data": {
                            "json": {
                                "items": [
                                    {
                                        "id": "vid-1",
                                        "name": "reader-a",
                                        "status": 1,
                                        "updatedAt": "2026-04-11T00:00:00.000Z",
                                    }
                                ],
                                "blocks": [],
                            }
                        }
                    }
                }
            ],
        )

    async def _run() -> None:
        client = WeWeRSSClient(
            base_url="http://wewe.local",
            auth_code="auth-code-1",
            transport=httpx.MockTransport(handler),
        )
        payload = await client.list_accounts()
        assert payload["items"][0]["id"] == "vid-1"
        assert payload["items"][0]["status"] == 1
        assert payload["blocks"] == []
        await client.close()

    asyncio.run(_run())


def test_wewe_rss_client_list_accounts_accepts_direct_data_payload_shape() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(
            200,
            json=[
                {
                    "result": {
                        "data": {
                            "items": [
                                {
                                    "id": "vid-raw-1",
                                    "name": "reader-raw",
                                    "status": 1,
                                }
                            ],
                            "blocks": [],
                        }
                    }
                }
            ],
        )

    async def _run() -> None:
        client = WeWeRSSClient(
            base_url="http://wewe.local",
            auth_code="auth-code-1",
            transport=httpx.MockTransport(handler),
        )
        payload = await client.list_accounts()
        assert payload["items"][0]["id"] == "vid-raw-1"
        await client.close()

    asyncio.run(_run())


def test_wewe_rss_client_create_login_url_parses_mutation_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/trpc/platform.createLoginUrl"
        assert request.headers["Authorization"] == "auth-code-1"
        assert json.loads(request.content.decode("utf-8")) == {"json": None}
        return httpx.Response(
            200,
            json={
                "result": {
                    "data": {
                        "json": {
                            "uuid": "uuid-1",
                            "scanUrl": "https://scan.example/abc",
                        }
                    }
                }
            },
        )

    async def _run() -> None:
        client = WeWeRSSClient(
            base_url="http://wewe.local",
            auth_code="auth-code-1",
            transport=httpx.MockTransport(handler),
        )
        payload = await client.create_login_url()
        assert payload == {"uuid": "uuid-1", "scanUrl": "https://scan.example/abc"}
        await client.close()

    asyncio.run(_run())


def test_wewe_rss_client_get_login_result_parses_query_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/trpc/platform.getLoginResult"
        assert request.url.params["batch"] == "1"
        assert json.loads(request.url.params["input"]) == {"0": {"id": "uuid-2"}}
        return httpx.Response(
            200,
            json=[
                {
                    "result": {
                        "data": {
                            "json": {
                                "vid": "vid-2",
                                "username": "reader-b",
                                "token": "token-b",
                            }
                        }
                    }
                }
            ],
        )

    async def _run() -> None:
        client = WeWeRSSClient(
            base_url="http://wewe.local",
            auth_code="auth-code-1",
            transport=httpx.MockTransport(handler),
        )
        payload = await client.get_login_result("uuid-2")
        assert payload["vid"] == "vid-2"
        assert payload["username"] == "reader-b"
        assert payload["token"] == "token-b"
        await client.close()

    asyncio.run(_run())


def test_wewe_rss_client_add_account_posts_expected_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/trpc/account.add"
        assert json.loads(request.content.decode("utf-8")) == {"id": "vid-3", "name": "reader-c", "token": "token-c"}
        return httpx.Response(200, json={"result": {"data": {"json": {"ok": True}}}})

    async def _run() -> None:
        client = WeWeRSSClient(
            base_url="http://wewe.local",
            auth_code="auth-code-1",
            transport=httpx.MockTransport(handler),
        )
        payload = await client.add_account(id="vid-3", name="reader-c", token="token-c")
        assert payload == {"ok": True}
        await client.close()

    asyncio.run(_run())


def test_wewe_rss_client_raises_auth_error_on_401() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            401,
            json=[
                {
                    "error": {
                        "message": "authCode不正确！",
                        "data": {"code": "UNAUTHORIZED", "httpStatus": 401, "path": "account.list"},
                    }
                }
            ],
        )

    async def _run() -> None:
        client = WeWeRSSClient(
            base_url="http://wewe.local",
            auth_code="bad-auth-code",
            transport=httpx.MockTransport(handler),
        )
        with pytest.raises(WeWeRSSAuthError):
            await client.list_accounts()
        await client.close()

    asyncio.run(_run())


def test_wewe_rss_client_raises_protocol_error_for_malformed_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={"unexpected": True})

    async def _run() -> None:
        client = WeWeRSSClient(
            base_url="http://wewe.local",
            auth_code="auth-code-1",
            transport=httpx.MockTransport(handler),
        )
        with pytest.raises(WeWeRSSProtocolError):
            await client.list_accounts()
        await client.close()

    asyncio.run(_run())
