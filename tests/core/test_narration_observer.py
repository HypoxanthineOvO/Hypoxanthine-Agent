from __future__ import annotations

import asyncio

import httpx
import pytest

from hypo_agent.core.narration_observer import (
    NarrationObserver,
    is_local_vllm_model,
    probe_local_vllm_model,
)
from hypo_agent.core.tool_narration import TraceEntry
from hypo_agent.models import NarrationConfig, NarrationToolConfig, NarrationToolLevels


class StubRouter:
    def __init__(self, *, replies: list[str] | None = None) -> None:
        self.replies = list(replies or ["我先核对一下"])
        self.calls: list[dict[str, object]] = []

    def get_model_for_task(self, task_type: str) -> str:
        assert task_type == "lightweight"
        return "GenesiQWen35BA3B"

    async def call(
        self,
        model_name: str,
        messages: list[dict[str, object]],
        *,
        session_id: str | None = None,
        tools: list[dict[str, object]] | None = None,
    ) -> str:
        self.calls.append(
            {
                "model_name": model_name,
                "messages": messages,
                "session_id": session_id,
                "tools": tools,
            }
        )
        if self.replies:
            return self.replies.pop(0)
        return "我先处理一下"


def _config(*, llm_timeout_ms: int = 800, dedup_max_consecutive: int = 2) -> NarrationConfig:
    return NarrationConfig(
        enabled=True,
        model="GenesiQWen35BA3B",
        tool_levels=NarrationToolLevels(
            heavy=["scan_emails", "search_web", "heavy_tool"],
            medium=["write_file"],
        ),
        tool_narration={
            "get_heartbeat_snapshot": NarrationToolConfig(template="📋 正在读取今日概况..."),
            "get_notion_todo_snapshot": NarrationToolConfig(template="📋 正在读取计划通..."),
            "search_emails": NarrationToolConfig(
                template="📧 正在搜索邮件「{query}」...",
                fallback="📧 正在搜索邮件...",
            ),
            "scan_emails": NarrationToolConfig(template="📧 正在扫描最新邮件..."),
            "get_email_detail": NarrationToolConfig(template="📧 正在读取邮件详情..."),
            "info_today": NarrationToolConfig(template="📰 正在整理今日信息..."),
            "update_reminder": NarrationToolConfig(
                template="⏰ 正在更新提醒「{title}」...",
                fallback="⏰ 正在更新提醒...",
            ),
        },
        llm_timeout_ms=llm_timeout_ms,
        llm_repeat_threshold=3,
        dedup_max_consecutive=dedup_max_consecutive,
        debounce_seconds=2.0,
        max_narration_length=80,
    )


@pytest.mark.parametrize(
    ("tool_name", "tool_args", "expected_text"),
    [
        ("get_heartbeat_snapshot", {}, "📋 正在读取今日概况..."),
        ("search_emails", {"query": "闭馆 通知"}, "📧 正在搜索邮件「闭馆 通知」..."),
        ("scan_emails", {}, "📧 正在扫描最新邮件..."),
        ("get_email_detail", {"message_id": "m1"}, "📧 正在读取邮件详情..."),
        ("info_today", {}, "📰 正在整理今日信息..."),
    ],
)
def test_observer_renders_new_templates_without_llm_call(
    tool_name: str,
    tool_args: dict[str, object],
    expected_text: str,
) -> None:
    router = StubRouter()
    observer = NarrationObserver(router=router, config=_config())

    narration = asyncio.run(
        observer.maybe_narrate(
            tool_name=tool_name,
            tool_args=tool_args,
            user_message_context="帮我处理一下",
            session_id="s1",
        )
    )

    assert narration == expected_text
    assert router.calls == []


def test_observer_skips_unknown_non_gated_tool_without_generic_fallback() -> None:
    router = StubRouter()
    observer = NarrationObserver(router=router, config=_config())

    narration = asyncio.run(
        observer.maybe_narrate(
            tool_name="mystery_tool",
            tool_args={"query": "test"},
            user_message_context="帮我试一下",
            session_id="s1",
        )
    )

    assert narration is None
    assert router.calls == []


def test_observer_builds_structured_context_and_redacts_sensitive_args() -> None:
    router = StubRouter(replies=["我先翻一下记录"])
    observer = NarrationObserver(router=router, config=_config())
    observer.record_trace_event(
        session_id="s1",
        event_type="tool_call_result",
        tool_name="search_emails",
        summary="找到了 3 封相关邮件",
        elapsed_ms=120,
    )
    observer.record_trace_event(
        session_id="s1",
        event_type="tool_call_error",
        tool_name="get_email_detail",
        summary="正文读取超时",
        elapsed_ms=350,
    )

    narration = asyncio.run(
        observer.maybe_narrate(
            tool_name="heavy_tool",
            tool_args={"query": "浦东新区公园", "password": "secret", "token": "abc"},
            user_message_context="搜一下浦东新区的公园",
            session_id="s1",
            iteration_number=2,
            total_tools_called=4,
        )
    )

    assert narration == "我先翻一下记录"
    assert len(router.calls) == 1
    prompt = str(router.calls[0]["messages"][0]["content"])
    assert "已经做了 4 步，这是第 2 轮" in prompt
    assert "搜索邮件" in prompt
    assert "读取邮件详情" in prompt
    assert "***" in prompt
    assert "secret" not in prompt
    assert "abc" not in prompt
    assert "heavy_tool" not in prompt


def test_observer_suppresses_semantically_similar_narration() -> None:
    router = StubRouter(replies=["我先核对一下时间", "我先确认一下时间"])
    observer = NarrationObserver(router=router, config=_config())

    first = asyncio.run(
        observer.maybe_narrate(
            tool_name="heavy_tool",
            tool_args={"query": "普拉提"},
            user_message_context="把普拉提改到四点",
            session_id="s1",
            iteration_number=1,
            total_tools_called=1,
        )
    )
    second = asyncio.run(
        observer.maybe_narrate(
            tool_name="heavy_tool",
            tool_args={"query": "普拉提 训练"},
            user_message_context="把普拉提改到四点",
            session_id="s1",
            iteration_number=2,
            total_tools_called=2,
        )
    )

    assert first == "我先核对一下时间"
    assert second is None


def test_observer_drops_timeout_to_silence() -> None:
    class SlowRouter(StubRouter):
        async def call(
            self,
            model_name: str,
            messages: list[dict[str, object]],
            *,
            session_id: str | None = None,
            tools: list[dict[str, object]] | None = None,
        ) -> str:
            del model_name, messages, session_id, tools
            await asyncio.sleep(0.05)
            return "我还在想"

    observer = NarrationObserver(
        router=SlowRouter(),
        config=_config(llm_timeout_ms=10),
    )

    narration = asyncio.run(
        observer.maybe_narrate(
            tool_name="heavy_tool",
            tool_args={"query": "今天新闻"},
            user_message_context="帮我搜一下今天新闻",
            session_id="main",
            iteration_number=1,
            total_tools_called=1,
        )
    )

    assert narration is None


def test_observer_stays_silent_when_local_model_marked_unavailable() -> None:
    observer = NarrationObserver(router=StubRouter(), config=_config())
    observer.set_llm_ready(False)

    narration = asyncio.run(
        observer.maybe_narrate(
            tool_name="heavy_tool",
            tool_args={"query": "今天新闻"},
            user_message_context="帮我搜一下今天新闻",
            session_id="main",
            iteration_number=1,
            total_tools_called=1,
        )
    )

    assert narration is None


def test_is_local_vllm_model_detects_local_genesis_endpoint() -> None:
    assert is_local_vllm_model(provider="GenesisLocal", api_base="http://localhost:18081/v1") is True
    assert is_local_vllm_model(provider="Genesis", api_base="http://10.15.88.94:8100/v1") is False


def test_probe_local_vllm_model_success_and_failure() -> None:
    success_transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"data": [{"id": "qwen3.6-35b"}]})
    )
    failure_transport = httpx.MockTransport(
        lambda request: (_ for _ in ()).throw(httpx.ConnectError("boom", request=request))
    )

    assert probe_local_vllm_model(
        api_base="http://localhost:18081/v1",
        api_key="genesis-llm-2026",
        transport=success_transport,
    ) is True
    assert probe_local_vllm_model(
        api_base="http://localhost:18081/v1",
        api_key="genesis-llm-2026",
        transport=failure_transport,
    ) is False


@pytest.mark.integration
def test_observer_generates_real_local_llm_narration() -> None:
    from hypo_agent.core.config_loader import load_runtime_model_config
    from hypo_agent.core.model_router import ModelRouter

    runtime_config = load_runtime_model_config()
    router = ModelRouter(runtime_config)
    observer = NarrationObserver(router=router, config=_config())

    narration = asyncio.run(
        observer.maybe_narrate(
            tool_name="heavy_tool",
            tool_args={"query": "浦东新区公园"},
            user_message_context="搜一下浦东新区的公园",
            session_id="integration-narration",
            iteration_number=1,
            total_tools_called=1,
        )
    )

    assert narration is not None
    assert narration.strip()
    assert len(narration) <= 25
    assert "heavy_tool" not in narration
