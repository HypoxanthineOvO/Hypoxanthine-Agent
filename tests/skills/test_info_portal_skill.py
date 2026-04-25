from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx

from hypo_agent.skills.info_portal_skill import InfoPortalSkill


def test_info_portal_skill_exports_and_keeps_runtime_name() -> None:
    skill = InfoPortalSkill(info_client=FakeInfoClient())
    assert skill.name == "info"


def test_info_portal_skill_has_passive_query_docstring() -> None:
    assert "Hypo-Info 门户被动查询" in (InfoPortalSkill.__doc__ or "")


def test_info_today_description_is_concise_function_summary() -> None:
    skill = InfoPortalSkill(info_client=FakeInfoClient())
    tools = {tool["function"]["name"]: tool["function"] for tool in skill.tools}
    assert tools["info_today"]["description"] == "Get today's news digest, optionally filtered by section."


class FakeInfoClient:
    def __init__(self) -> None:
        self.homepage_calls = 0
        self.article_calls: list[dict[str, object]] = []
        self.benchmark_calls: list[int] = []
        self.sections_calls = 0
        self.homepage_payload: dict | Exception = {}
        self.articles_payload: list[dict] | Exception = []
        self.benchmark_payload: list[dict] | Exception = []
        self.sections_payload: list[dict] | Exception = []

    async def get_homepage(self) -> dict:
        self.homepage_calls += 1
        if isinstance(self.homepage_payload, Exception):
            raise self.homepage_payload
        return dict(self.homepage_payload)

    async def get_articles(
        self,
        section: str | None = None,
        date: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        self.article_calls.append({"section": section, "date": date, "limit": limit})
        if isinstance(self.articles_payload, Exception):
            raise self.articles_payload
        return list(self.articles_payload)

    async def get_sections(self) -> list[dict]:
        self.sections_calls += 1
        if isinstance(self.sections_payload, Exception):
            raise self.sections_payload
        return list(self.sections_payload)

    async def get_benchmark_ranking(self, top_n: int = 10) -> list[dict]:
        self.benchmark_calls.append(top_n)
        if isinstance(self.benchmark_payload, Exception):
            raise self.benchmark_payload
        return list(self.benchmark_payload)

    async def health(self) -> dict:
        return {"status": "ok"}


def test_info_today_formats_homepage_summary() -> None:
    async def _run() -> None:
        client = FakeInfoClient()
        client.homepage_payload = {
            "today": [
                {
                    "title": "GPT-5.4 发布",
                    "source": "OpenAI",
                    "section": "AI",
                    "score": 9.8,
                    "summary": "模型能力、稳定性和工具调用链路都有升级。",
                    "url": "https://example.com/gpt-5-4",
                },
                {
                    "title": "Claude 4.1 更新",
                    "source": "Anthropic",
                    "section": "AI",
                    "score": 9.4,
                    "summary": "长上下文和代码编辑质量继续提升。",
                    "url": "https://example.com/claude-4-1",
                },
                {
                    "title": "新开源向量库",
                    "source": "GitHub",
                    "section": "开源",
                    "score": 8.7,
                    "summary": "",
                    "url": "https://example.com/vector-db",
                },
            ]
        }
        skill = InfoPortalSkill(
            info_client=client,
            now_fn=lambda: datetime(2026, 3, 26, 8, 0, 0, tzinfo=UTC),
        )

        output = await skill.execute("info_today", {})

        assert output.status == "success"
        assert output.result == "\n\n---\n\n".join(
            [
                "\n".join(
                    [
                        "📅 今日资讯（共 3 篇）- 2026-03-26",
                        "",
                        "📰 GPT-5.4 发布",
                        "",
                        "来源：OpenAI | 分区：AI | 重要性：⭐⭐⭐⭐（9.8/10）",
                        "",
                        "摘要：模型能力、稳定性和工具调用链路都有升级。",
                        "",
                        "链接：https://example.com/gpt-5-4",
                    ]
                ),
                "\n".join(
                    [
                        "📰 Claude 4.1 更新",
                        "",
                        "来源：Anthropic | 分区：AI | 重要性：⭐⭐⭐⭐（9.4/10）",
                        "",
                        "摘要：长上下文和代码编辑质量继续提升。",
                        "",
                        "链接：https://example.com/claude-4-1",
                    ]
                ),
                "\n".join(
                    [
                        "📰 新开源向量库",
                        "",
                        "来源：GitHub | 分区：开源 | 重要性：⭐⭐⭐⭐（8.7/10）",
                        "",
                        "摘要：暂无摘要",
                        "",
                        "链接：https://example.com/vector-db",
                    ]
                ),
            ]
        )
        assert client.homepage_calls == 1
        assert client.article_calls == []

    asyncio.run(_run())


def test_info_today_section_uses_section_filtered_articles() -> None:
    async def _run() -> None:
        client = FakeInfoClient()
        client.articles_payload = [
            {
                "title": "MCP 生态新进展",
                "source": "Hypo-Info",
                "score": 8.6,
                "section": "AI",
                "summary": "围绕协议生态和工具互操作性展开。",
                "url": "https://example.com/mcp",
            },
            {
                "title": "开源 Agent 框架发布",
                "source": "GitHub",
                "score": 8.2,
                "section": "AI",
                "summary": "聚焦多 Agent 调度和状态管理。",
                "url": "https://example.com/agent",
            },
        ]
        skill = InfoPortalSkill(
            info_client=client,
            now_fn=lambda: datetime(2026, 3, 26, 8, 0, 0, tzinfo=UTC),
        )

        output = await skill.execute("info_today", {"section": "AI"})

        assert output.status == "success"
        assert output.result == "\n\n---\n\n".join(
            [
                "\n".join(
                    [
                        "📅 今日资讯（共 2 篇）- 2026-03-26",
                        "",
                        "📰 MCP 生态新进展",
                        "",
                        "来源：Hypo-Info | 分区：AI | 重要性：⭐⭐⭐⭐（8.6/10）",
                        "",
                        "摘要：围绕协议生态和工具互操作性展开。",
                        "",
                        "链接：https://example.com/mcp",
                    ]
                ),
                "\n".join(
                    [
                        "📰 开源 Agent 框架发布",
                        "",
                        "来源：GitHub | 分区：AI | 重要性：⭐⭐⭐⭐（8.2/10）",
                        "",
                        "摘要：聚焦多 Agent 调度和状态管理。",
                        "",
                        "链接：https://example.com/agent",
                    ]
                ),
            ]
        )
        assert client.homepage_calls == 0
        assert client.article_calls == [
            {"section": "AI", "date": "2026-03-26", "limit": 20},
        ]

    asyncio.run(_run())


def test_info_today_truncates_long_results() -> None:
    async def _run() -> None:
        client = FakeInfoClient()
        client.homepage_payload = {
            "today": [
                {
                    "title": f"新闻 {index}",
                    "source": "Hypo-Info",
                    "section": "AI",
                    "score": 8.0 + index / 10,
                    "summary": f"这是第 {index} 条新闻的完整摘要。",
                    "url": f"https://example.com/news-{index}",
                }
                for index in range(1, 18)
            ]
        }
        skill = InfoPortalSkill(
            info_client=client,
            now_fn=lambda: datetime(2026, 3, 26, 8, 0, 0, tzinfo=UTC),
            max_items=15,
        )

        output = await skill.execute("info_today", {})

        assert output.status == "success"
        assert "📅 今日资讯（共 17 篇）- 2026-03-26" in output.result
        assert "共 17 篇，显示前 15 篇" in output.result
        assert "📰 新闻 1" in output.result
        assert "📰 新闻 15" in output.result
        assert "📰 新闻 16" not in output.result

    asyncio.run(_run())


def test_info_today_formats_nested_fields_without_dumping_json() -> None:
    async def _run() -> None:
        client = FakeInfoClient()
        client.homepage_payload = {
            "today": [
                {
                    "article": {
                        "title": "OpenAI 推理栈更新",
                        "url": "https://example.com/nested-openai",
                    },
                    "source": {"name": "OpenAI Blog"},
                    "section": {"name": "AI"},
                    "score": 9.1,
                    "summary": {"brief": "吞吐和成本曲线继续改善。"},
                }
            ]
        }
        skill = InfoPortalSkill(
            info_client=client,
            now_fn=lambda: datetime(2026, 3, 26, 8, 0, 0, tzinfo=UTC),
        )

        output = await skill.execute("info_today", {})

        assert output.status == "success"
        assert "OpenAI 推理栈更新" in output.result
        assert "OpenAI Blog" in output.result
        assert "AI" in output.result
        assert "吞吐和成本曲线继续改善。" in output.result
        assert "https://example.com/nested-openai" in output.result
        assert '"article"' not in output.result
        assert "{'article':" not in output.result

    asyncio.run(_run())


def test_info_search_filters_articles_by_query() -> None:
    async def _run() -> None:
        client = FakeInfoClient()
        client.articles_payload = [
            {
                "title": "Claude 4 评测",
                "source": "Hypo-Info",
                "score": 8.9,
                "summary": "对比多个模型的长上下文能力",
                "section": "AI",
                "url": "https://example.com/claude-review",
            },
            {
                "title": "数据库优化实践",
                "source": "TechBlog",
                "score": 7.4,
                "summary": "主要讨论索引与查询计划",
                "section": "工程",
                "url": "https://example.com/db",
            },
            {
                "title": "模型路由策略",
                "source": "Hypo-Info",
                "score": 8.1,
                "summary": "Claude 4 与 GPT-5 的任务分配",
                "section": "AI",
                "url": "https://example.com/router",
            },
        ]
        skill = InfoPortalSkill(
            info_client=client,
            now_fn=lambda: datetime(2026, 3, 26, 8, 0, 0, tzinfo=UTC),
        )

        output = await skill.execute("info_search", {"query": "Claude", "limit": 2})

        assert output.status == "success"
        assert output.result == "\n\n---\n\n".join(
            [
                "\n".join(
                    [
                        "🔍 Claude 4 评测",
                        "",
                        "来源：Hypo-Info | 分区：AI",
                        "",
                        "摘要：对比多个模型的长上下文能力",
                        "",
                        "链接：https://example.com/claude-review",
                    ]
                ),
                "\n".join(
                    [
                        "🔍 模型路由策略",
                        "",
                        "来源：Hypo-Info | 分区：AI",
                        "",
                        "摘要：Claude 4 与 GPT-5 的任务分配",
                        "",
                        "链接：https://example.com/router",
                    ]
                ),
            ]
        )
        assert client.article_calls == [{"section": None, "date": None, "limit": 20}]

    asyncio.run(_run())


def test_info_benchmark_formats_ranking_table() -> None:
    async def _run() -> None:
        client = FakeInfoClient()
        client.benchmark_payload = [
            {
                "rank": 1,
                "model": "GPT-5",
                "organization": "OpenAI",
                "score": 92.3,
                "coding": 94.1,
                "reasoning": 91.8,
                "math": 90.5,
                "updated_at": "2026-03-26",
            },
            {
                "rank": 2,
                "model": "Claude 4",
                "organization": "Anthropic",
                "score": 91.1,
                "coding": 90.0,
            },
            {
                "rank": 3,
                "model": "Gemini 2.5 Pro",
                "organization": "Google",
                "score": 89.4,
            },
        ]
        skill = InfoPortalSkill(info_client=client)

        output = await skill.execute("info_benchmark", {"top_n": 3})

        assert output.status == "success"
        assert output.result == "\n".join(
            [
                "🏆 LLM Benchmark 排名（截至 2026-03-26）",
                "",
                "1. GPT-5（OpenAI）",
                "    综合得分：92.3",
                "    优势：coding 94.1 | reasoning 91.8 | math 90.5",
                "",
                "2. Claude 4（Anthropic）",
                "    综合得分：91.1",
                "    优势：coding 90",
                "",
                "3. Gemini 2.5 Pro（Google）",
                "    综合得分：89.4",
            ]
        )
        assert client.benchmark_calls == [3]

    asyncio.run(_run())


def test_info_sections_formats_available_sections() -> None:
    async def _run() -> None:
        client = FakeInfoClient()
        client.sections_payload = [
            {"name": "AI"},
            {"name": "开源"},
            {"name": "学术"},
        ]
        skill = InfoPortalSkill(info_client=client)

        output = await skill.execute("info_sections", {})

        assert output.status == "success"
        assert output.result == "AI | 开源 | 学术"
        assert client.sections_calls == 1

    asyncio.run(_run())


def test_info_unavailable_returns_friendly_message() -> None:
    async def _run() -> None:
        client = FakeInfoClient()
        client.homepage_payload = httpx.ConnectError("connect failed")
        skill = InfoPortalSkill(info_client=client)

        output = await skill.execute("info_today", {})

        assert output.status == "error"
        assert output.error_info == "Hypo-Info 当前不可用，请确认服务是否启动"

    asyncio.run(_run())
