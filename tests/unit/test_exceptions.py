from __future__ import annotations

import pytest

from hypo_agent.channels.coder.coder_client import CoderUnavailableError
from hypo_agent.channels.feishu_channel import FeishuAPIError
from hypo_agent.channels.info.info_client import InfoClientUnavailable
from hypo_agent.channels.notion.notion_client import (
    NotionTimeoutError,
    NotionUnavailableError,
)
from hypo_agent.channels.probe.probe_server import ProbeRPCError
from hypo_agent.channels.weixin.ilink_client import (
    ILinkAPIError,
    ILinkError,
    SessionExpiredError,
)
from hypo_agent.core.image_renderer import ImageRenderError
from hypo_agent.exceptions import (
    ChannelError,
    ConfigError,
    ExternalServiceError,
    HypoAgentError,
    ModelError,
    SkillError,
    StorageError,
)
from hypo_agent.skills.info_reach_skill import HypoInfoError


@pytest.mark.unit
def test_exception_hierarchy() -> None:
    assert issubclass(ConfigError, HypoAgentError)
    assert issubclass(ModelError, HypoAgentError)
    assert issubclass(ChannelError, HypoAgentError)
    assert issubclass(SkillError, HypoAgentError)
    assert issubclass(StorageError, HypoAgentError)
    assert issubclass(ExternalServiceError, HypoAgentError)

    assert issubclass(CoderUnavailableError, ExternalServiceError)
    assert issubclass(NotionUnavailableError, ExternalServiceError)
    assert issubclass(NotionTimeoutError, ExternalServiceError)
    assert issubclass(InfoClientUnavailable, ExternalServiceError)
    assert issubclass(ProbeRPCError, ExternalServiceError)
    assert issubclass(ILinkError, ExternalServiceError)
    assert issubclass(ILinkAPIError, ExternalServiceError)
    assert issubclass(SessionExpiredError, ChannelError)
    assert issubclass(FeishuAPIError, ChannelError)
    assert issubclass(ImageRenderError, SkillError)
    assert issubclass(HypoInfoError, ExternalServiceError)


@pytest.mark.unit
def test_hypo_agent_error_str_contains_context() -> None:
    error = HypoAgentError(
        "model request failed",
        operation="router.call",
        model="gpt-4o-mini",
        session_id="main",
    )

    rendered = str(error)

    assert "router.call" in rendered
    assert "model request failed" in rendered
    assert "model='gpt-4o-mini'" in rendered
    assert "session_id='main'" in rendered


@pytest.mark.unit
def test_custom_error_str_contains_useful_context() -> None:
    notion_error = NotionTimeoutError(
        "Notion retrieve page 超时",
        operation="retrieve_page",
        page_id="page-123",
    )
    feishu_error = FeishuAPIError("send failed", code=403)
    ilink_error = ILinkAPIError(
        "/ilink/bot/send",
        {"ret": 1, "errcode": 41001, "errmsg": "session expired"},
        "send_message",
    )
    image_error = ImageRenderError(
        block_type="markdown",
        fallback_text="[fallback]",
        reason="playwright unavailable",
    )

    assert "retrieve_page" in str(notion_error)
    assert "page_id='page-123'" in str(notion_error)
    assert "code='403'" in str(feishu_error)
    assert "/ilink/bot/send" in str(ilink_error)
    assert "41001" in str(ilink_error)
    assert "render_image" in str(image_error)
    assert "block_type='markdown'" in str(image_error)
