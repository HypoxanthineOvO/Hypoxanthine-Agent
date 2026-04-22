from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from hypo_agent.channels.qq_bot_channel import QQBotChannelService, clear_qqbot_token_cache
from hypo_agent.channels.weixin.weixin_adapter import WeixinAdapter, table_to_key_value_text
from hypo_agent.core.qq_text_renderer import downgrade_headings
from hypo_agent.models import Message


class StubImageRenderer:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.calls: list[tuple[str, str]] = []

    async def render_to_image(self, content: str, block_type: str = "markdown") -> str:
        self.calls.append((content, block_type))
        path = Path(f"/tmp/{block_type}_{len(self.calls)}.png")
        path.write_bytes(b"\x89PNG\r\n\x1a\nfake")
        return str(path)

    def build_fallback_text(self, content: str, *, block_type: str) -> str:
        return f"[{block_type}渲染失败，原始内容如下]\n{content}"


class DummyWeixinClient:
    def __init__(self) -> None:
        self.bot_token = "bot-token"
        self.user_id = ""
        self.last_context_token = "ctx-1"
        self.raw_messages: list[dict[str, object]] = []

    async def send_message_raw(
        self,
        *,
        to_user_id: str,
        text: str | None = None,
        item_list: list[dict[str, object]] | None = None,
        context_token: str | None = "",
        client_id: str | None = None,
        msg_id: str | None = None,
        message_state: int = 2,
    ) -> dict[str, object]:
        self.raw_messages.append(
            {
                "to_user_id": to_user_id,
                "text": text,
                "item_list": list(item_list or []),
                "context_token": context_token,
                "client_id": client_id,
                "msg_id": msg_id,
                "message_state": message_state,
            }
        )
        return {"ret": 0}

    def remember_context_token(self, context_token: str) -> None:
        self.last_context_token = context_token

    @staticmethod
    def build_media_upload_payload(
        *,
        to_user_id: str,
        media_type: int,
        plaintext: bytes,
        encrypted_size: int,
        aes_key_hex: str,
    ) -> dict[str, object]:
        return {
            "filekey": "filekey-1",
            "media_type": media_type,
            "to_user_id": to_user_id,
            "rawsize": len(plaintext),
            "rawfilemd5": "md5-1",
            "filesize": encrypted_size,
            "no_need_thumb": True,
            "aeskey": aes_key_hex,
        }

    async def get_upload_url(self, **payload) -> dict[str, object]:
        del payload
        return {"upload_param": "upload-param-1"}

    async def upload_media(self, *, upload_param: str, filekey: str, encrypted_data: bytes) -> str:
        del upload_param, filekey, encrypted_data
        return "download-param-1"


def test_downgrade_headings_only_changes_h3_and_below() -> None:
    text = "# H1\n## H2\n### H3\n#### H4\n正文\n"

    rendered = downgrade_headings(text, max_level=2)

    assert rendered == "# H1\n## H2\n**H3**\n**H4**\n正文\n"


def test_table_to_key_value_text_for_two_column_table() -> None:
    table = "| 名称 | 值 |\n| --- | --- |\n| 模型 | GPT-5 |\n| 状态 | 正常 |\n"

    rendered = table_to_key_value_text(table)

    assert rendered == "名称: 值\n模型: GPT-5\n状态: 正常"


def test_table_to_key_value_text_for_multi_column_table() -> None:
    table = "| 服务 | 状态 | 备注 |\n| --- | --- | --- |\n| QQ | 正常 | online |\n"

    rendered = table_to_key_value_text(table)

    assert rendered == "条目 1\n服务: QQ\n状态: 正常\n备注: online"


def test_qqbot_native_markdown_falls_back_to_disabled_mode(monkeypatch) -> None:
    clear_qqbot_token_cache()
    calls: list[tuple[str, str, dict[str, object]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8")) if request.content else {}
        calls.append((request.method, str(request.url), payload))
        if request.url.path.endswith("/getAppAccessToken"):
            return httpx.Response(200, json={"access_token": "token-1", "expires_in": 7200})
        if payload.get("msg_type") == 2:
            return httpx.Response(400, json={"message": "不允许发送原生markdown"})
        return httpx.Response(200, json={"id": "reply-1"})

    transport = httpx.MockTransport(handler)

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("hypo_agent.channels.qq_bot_channel.httpx.AsyncClient", MockAsyncClient)

    service = QQBotChannelService(
        app_id="1029384756",
        app_secret="bot-secret-xyz",
        markdown_mode="native",
    )

    result = asyncio.run(
        service.send_message(
            Message(
                text="### 标题\n**正文**",
                sender="assistant",
                session_id="main",
                channel="qq",
                sender_id="OPENID-C2C-001",
            )
        )
    )

    assert result.success is True
    assert calls[1][2]["msg_type"] == 2
    assert calls[-1][2]["msg_type"] == 0
    assert "【正文】" in str(calls[-1][2]["content"])
    assert service.markdown_mode == "disabled"


def test_weixin_adapter_sends_mixed_item_list_in_single_message(tmp_path: Path) -> None:
    async def _run() -> None:
        image_path = tmp_path / "formula.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\nfake")
        client = DummyWeixinClient()
        adapter = WeixinAdapter(
            client=client,
            target_user_id="user@im.wechat",
            image_renderer=StubImageRenderer(),
            send_delay_seconds=0,
        )

        await adapter.push(
            Message(
                text="前文\n$$E=mc^2$$\n后文",
                sender="assistant",
                session_id="main",
                channel="system",
                attachments=[{"type": "image", "url": str(image_path), "filename": "formula.png"}],
            )
        )

        assert len(client.raw_messages) == 1
        payload = client.raw_messages[0]
        item_list = payload["item_list"]
        assert isinstance(item_list, list)
        assert [item["type"] for item in item_list] == [1, 2, 1, 2]
        assert item_list[0]["text_item"]["text"].startswith("前文")
        assert item_list[2]["text_item"]["text"].strip() == "后文"

    asyncio.run(_run())
