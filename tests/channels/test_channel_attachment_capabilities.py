from __future__ import annotations

from hypo_agent.channels.feishu_channel import FeishuChannel
from hypo_agent.channels.qq_bot_channel import QQBotChannelService
from hypo_agent.channels.weixin.weixin_adapter import WeixinAdapter


class DummyWeixinClient:
    bot_token = "token"
    user_id = "wx-user"


class DummyQueue:
    async def put(self, event: dict) -> None:
        del event


def test_qqbot_declares_outbound_attachment_capability() -> None:
    channel = QQBotChannelService(app_id="app", app_secret="secret")

    capability = channel.attachment_capability

    assert capability.channel == "qq_bot"
    assert capability.supports_attachment_type("image") is True
    assert capability.supports_attachment_type("file") is True
    assert "fallback_to_link" in (capability.fallback_actions or [])


def test_weixin_declares_outbound_attachment_capability() -> None:
    adapter = WeixinAdapter(client=DummyWeixinClient(), target_user_id="wx-user")

    capability = adapter.attachment_capability

    assert capability.channel == "weixin"
    assert capability.supports_attachment_type("image") is True
    assert capability.supports_attachment_type("file") is True
    assert "send_summary" in (capability.fallback_actions or [])


def test_feishu_declares_outbound_attachment_capability() -> None:
    channel = FeishuChannel(app_id="app", app_secret="secret", message_queue=DummyQueue())

    capability = channel.attachment_capability

    assert capability.channel == "feishu"
    assert capability.supports_attachment_type("image") is True
    assert capability.supports_attachment_type("file") is True
    assert "fallback_to_link" in (capability.fallback_actions or [])
