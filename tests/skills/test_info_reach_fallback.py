from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx

from hypo_agent.core.pipeline import ChatPipeline
from hypo_agent.models import Message
from hypo_agent.skills.info_reach_skill import InfoReachSkill


class DummyQueue:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def put(self, event: dict[str, Any]) -> None:
        self.events.append(dict(event))


class StubRouter:
    async def call(self, model_name, messages, *, session_id=None, tools=None):
        del model_name, messages, session_id, tools
        return "ok"


class StubSessionMemory:
    def append(self, message: Message) -> None:
        del message

    def get_recent_messages(self, session_id: str, limit: int | None = None) -> list[Message]:
        del session_id, limit
        return []


def _build_skill(*, tmp_path: Path, transport: httpx.MockTransport) -> tuple[InfoReachSkill, DummyQueue]:
    queue = DummyQueue()
    skill = InfoReachSkill(
        message_queue=queue,
        db_path=tmp_path / "hypo.db",
        base_url="http://localhost:8200",
        transport=transport,
    )
    return skill, queue


def test_digest_available(tmp_path: Path) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/agent/digest":
            return httpx.Response(
                200,
                json={
                    "highlight": "今天 AI 新闻很多。",
                    "sections": [{"category": "AI", "items": ["模型更新"]}],
                    "stats": {"total_articles": 1},
                },
            )
        raise AssertionError(f"unexpected path: {request.url.path}")

    skill, queue = _build_skill(tmp_path=tmp_path, transport=httpx.MockTransport(_handler))

    result = asyncio.run(skill.run_scheduled_summary())

    assert result["summary_pushed"] is True
    assert len(queue.events) == 1
    assert "今天 AI 新闻很多" in str(queue.events[0]["summary"])


def test_digest_empty_fallback_summary(tmp_path: Path) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/agent/digest":
            return httpx.Response(200, json={"highlight": "", "sections": [], "stats": {"total_articles": 0}})
        if request.url.path == "/api/agent/summary":
            return httpx.Response(
                200,
                json={
                    "categories": [
                        {"category": "AI", "article_count": 2, "top_articles": ["模型更新", "Agent 工具链"]}
                    ],
                    "total_articles": 2,
                },
            )
        raise AssertionError(f"unexpected path: {request.url.path}")

    skill, queue = _build_skill(tmp_path=tmp_path, transport=httpx.MockTransport(_handler))

    result = asyncio.run(skill.run_scheduled_summary())

    assert result["summary_pushed"] is True
    assert len(queue.events) == 1
    assert "AI" in str(queue.events[0]["summary"])
    assert "Agent 工具链" in str(queue.events[0]["summary"])


def test_all_empty_fallback_query(tmp_path: Path) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/agent/digest":
            return httpx.Response(200, json={"sections": [], "stats": {"total_articles": 0}})
        if request.url.path == "/api/agent/summary":
            return httpx.Response(200, json={"categories": [], "total_articles": 0})
        if request.url.path == "/api/agent/query":
            return httpx.Response(
                200,
                json={
                    "total": 2,
                    "articles": [
                        {"title": "OpenAI 发布新模型", "summary": "推理增强", "url": "https://example.com/a"},
                        {"title": "Anthropic 更新 Claude", "summary": "代码质量提升", "url": "https://example.com/b"},
                    ],
                },
            )
        raise AssertionError(f"unexpected path: {request.url.path}")

    skill, queue = _build_skill(tmp_path=tmp_path, transport=httpx.MockTransport(_handler))

    result = asyncio.run(skill.run_scheduled_summary())

    assert result["summary_pushed"] is True
    assert len(queue.events) == 1
    assert "今日资讯" in str(queue.events[0]["summary"])
    assert "OpenAI 发布新模型" in str(queue.events[0]["summary"])


def test_no_articles(tmp_path: Path) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/agent/digest":
            return httpx.Response(200, json={"sections": [], "stats": {"total_articles": 0}})
        if request.url.path == "/api/agent/summary":
            return httpx.Response(200, json={"categories": [], "total_articles": 0})
        if request.url.path == "/api/agent/query":
            return httpx.Response(200, json={"total": 0, "articles": []})
        raise AssertionError(f"unexpected path: {request.url.path}")

    skill, queue = _build_skill(tmp_path=tmp_path, transport=httpx.MockTransport(_handler))

    result = asyncio.run(skill.run_scheduled_summary())

    assert result["summary_pushed"] is True
    assert len(queue.events) == 1
    assert queue.events[0]["summary"] == "📰 今日暂无新资讯。"


def test_push_uses_hypo_info_tag(tmp_path: Path) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/agent/digest":
            return httpx.Response(
                200,
                json={
                    "highlight": "今天 AI 新闻很多。",
                    "sections": [{"category": "AI", "items": ["模型更新"]}],
                    "stats": {"total_articles": 1},
                },
            )
        raise AssertionError(f"unexpected path: {request.url.path}")

    skill, queue = _build_skill(tmp_path=tmp_path, transport=httpx.MockTransport(_handler))
    asyncio.run(skill.run_scheduled_summary())

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=StubSessionMemory(),
    )
    message = pipeline._event_to_message(queue.events[0])

    assert message is not None
    assert message.message_tag == "hypo_info"
