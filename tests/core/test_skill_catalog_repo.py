from __future__ import annotations

from pathlib import Path

import pytest

from hypo_agent.core.skill_catalog import SkillCatalog


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("skill_name", "message", "key_tool"),
    [
        ("git-workflow", "帮我看看这个 repo 最近的 commit 历史", "exec_command"),
        ("system-service-ops", "hypo-agent 服务状态怎么样", "exec_command"),
        ("python-project-dev", "跑一下 pytest 看看测试结果", "exec_command"),
        ("hypo-agent-ops", "用测试模式跑一下 smoke test", "exec_command"),
        ("host-inspection", "服务器磁盘和内存现在什么情况", "exec_command"),
        ("weather", "帮我查一下北京天气", "exec_command"),
        ("agent-browser", "打开这个网页并点一下页面里的按钮", "exec_command"),
        ("github-ops", "帮我看看这个仓库有哪些 open PR", "exec_command"),
        ("log-inspector", "查看最近的错误日志", "read_file"),
        ("agent-search", "搜索一下 Claude 4 最新消息", "search_web"),
        ("info-portal", "今天有什么 AI 新闻", "info_today"),
        ("notion", "帮我在 Notion 创建一个页面", "notion_create_entry"),
        ("notion", "帮我把 Notion 页面转成 md 发给我", "notion_export_page_markdown"),
        ("coder", "提交一个代码任务给 Coder", "coder_submit_task"),
        ("probe", "看看探针设备列表", "probe_list_devices"),
        ("info-reach", "帮我订阅 LLM 相关资讯", "info_subscribe"),
    ],
)
def test_repo_skill_catalog_matches_expected_skill_messages(
    skill_name: str,
    message: str,
    key_tool: str,
) -> None:
    catalog = SkillCatalog(REPO_ROOT / "skills")
    catalog.scan()

    names = {manifest.name for manifest in catalog.list_manifests()}
    candidates = [item.name for item in catalog.match_candidates(message)]
    body = catalog.load_body(skill_name)

    assert skill_name in names
    assert skill_name in candidates
    assert key_tool in body


def test_repo_skill_catalog_contains_notion_references() -> None:
    catalog = SkillCatalog(REPO_ROOT / "skills")
    catalog.scan()

    references = catalog.load_references("notion")

    assert "property-types.md" in references


def test_repo_skill_catalog_contains_agent_browser_references() -> None:
    catalog = SkillCatalog(REPO_ROOT / "skills")
    catalog.scan()

    references = catalog.load_references("agent-browser")

    assert "command-patterns.md" in references


def test_repo_skill_catalog_contains_github_ops_references() -> None:
    catalog = SkillCatalog(REPO_ROOT / "skills")
    catalog.scan()

    references = catalog.load_references("github-ops")

    assert "command-patterns.md" in references


def test_repo_skill_catalog_contains_all_phase1_phase2_skill_names() -> None:
    catalog = SkillCatalog(REPO_ROOT / "skills")
    catalog.scan()

    names = {manifest.name for manifest in catalog.list_manifests()}

    assert {
        "log-inspector",
        "git-workflow",
        "system-service-ops",
        "python-project-dev",
        "hypo-agent-ops",
        "host-inspection",
        "weather",
        "agent-browser",
        "github-ops",
        "agent-search",
        "info-portal",
        "notion",
        "coder",
        "probe",
        "info-reach",
    }.issubset(names)


def test_repo_skill_catalog_prioritizes_notion_for_plan_todo_queries() -> None:
    catalog = SkillCatalog(REPO_ROOT / "skills")
    catalog.scan()

    candidates = catalog.match_candidates("查看一下今天的计划通待办事项")

    assert candidates
    assert candidates[0].name == "notion"
