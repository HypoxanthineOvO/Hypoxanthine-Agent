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
        ("log-inspector", "查看最近的错误日志", "read_file"),
        ("agent-search", "搜索一下 Claude 4 最新消息", "web_search"),
        ("info-portal", "今天有什么 AI 新闻", "info_today"),
        ("notion", "帮我在 Notion 创建一个页面", "notion_create_entry"),
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
        "agent-search",
        "info-portal",
        "notion",
        "coder",
        "probe",
        "info-reach",
    }.issubset(names)
