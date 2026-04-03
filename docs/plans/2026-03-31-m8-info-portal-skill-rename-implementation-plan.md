# M8 Info Portal Skill Rename Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将现有 `InfoSkill` 重命名为 `InfoPortalSkill`，补齐 `InfoPortalSkill` 与 `InfoReachSkill` 的文档和工具描述边界，消除“两套都连 Hypo-Info”的命名困惑，同时保持现有功能行为不变。

**Architecture:** 保持双 Skill 架构不变：`InfoPortalSkill` 继续承担“用户主动提问时的被动查询”，`InfoReachSkill` 继续承担“Scheduler/Heartbeat 驱动的主动推送与订阅管理”。本次只做命名、docstring、工具描述和 import/注册面的整理，不调整工具名、调度任务名、配置模型或 API 行为。

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, pytest, uv.

---

## Skills and Constraints

- Announce at execution start: `I'm using the writing-plans skill to create the implementation plan.`
- Execution skills to use after approval: `@executing-plans` `@test-driven-development` `@verification-before-completion`
- 已确定的设计决策：
  - 新类名采用 `InfoPortalSkill`
  - 文件重命名为 `src/hypo_agent/skills/info_portal_skill.py`
  - 测试文件同步重命名为 `tests/skills/test_info_portal_skill.py`
  - 保留 runtime skill key `info` 不变
  - 保留 `skill.name = "info"` 不变
  - 工具名保持不变：`info_today / info_search / info_benchmark / info_sections / info_query / info_summary`
- 明确不做的事：
  - 不合并两个 Skill
  - 不改 `config/tasks.yaml`
  - 不改 `config/security.yaml`
  - 不改 `config/secrets.yaml`
  - 不改任何功能行为或 API 调用路径
- 提交约束：
  - 代码提交：`M8: <说明>`
  - 若新增文档提交：`M8[doc]: <说明>`

---

## Phase Overview

1. 命名层改造：`InfoSkill` -> `InfoPortalSkill`，同步文件、import、测试命名
2. 文档与工具描述收紧：明确被动查询层 vs 主动推送层
3. 回归验证：gateway 注册、全量 pytest

---

### Task 1: 重命名 Skill 类、文件和测试文件，保留 runtime skill key `info`

**Files:**
- Create: `src/hypo_agent/skills/info_portal_skill.py`
- Delete: `src/hypo_agent/skills/info_skill.py`
- Modify: `src/hypo_agent/skills/__init__.py`
- Modify: `src/hypo_agent/gateway/app.py`
- Create: `tests/skills/test_info_portal_skill.py`
- Delete: `tests/skills/test_info_skill.py`
- Modify: `tests/gateway/test_info_skill_registration.py`

**Step 1: Write the failing test**

```python
from hypo_agent.skills.info_portal_skill import InfoPortalSkill


def test_info_portal_skill_exports_and_keeps_runtime_name() -> None:
    skill = InfoPortalSkill(info_client=FakeInfoClient())
    assert skill.name == "info"
```

```python
def test_build_default_deps_skips_info_portal_skill_when_service_config_missing(...) -> None:
    ...
    assert "info" not in deps.skill_manager._skills
```

**Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/skills/test_info_portal_skill.py::test_info_portal_skill_exports_and_keeps_runtime_name tests/gateway/test_info_skill_registration.py::test_build_default_deps_skips_info_portal_skill_when_service_config_missing -q
```

Expected: FAIL，因为新模块/新类名尚不存在

**Step 3: Write minimal implementation**

- 将 `src/hypo_agent/skills/info_skill.py` 重命名为 `src/hypo_agent/skills/info_portal_skill.py`
- 将类名 `InfoSkill` 改为 `InfoPortalSkill`
- 保留：
  - `name = "info"`
  - 现有工具名
  - 现有功能逻辑
- 更新以下引用：
  - `src/hypo_agent/skills/__init__.py`
  - `src/hypo_agent/gateway/app.py`
  - `tests/skills/test_info_portal_skill.py`
  - `tests/gateway/test_info_skill_registration.py`
- gateway 注册逻辑仍使用配置键 `info`，只把实例化类改为 `InfoPortalSkill`
- 日志事件名是否保留：
  - 推荐保留现有 `info_skill.*` event 名，避免扩大日志兼容面
  - 如改为 `info_portal_skill.*`，必须同步测试与日志断言

**Step 4: Run tests to verify it passes**

Run:

```bash
uv run pytest tests/skills/test_info_portal_skill.py tests/gateway/test_info_skill_registration.py -q
```

Expected: PASS

**Step 5: Commit**

```bash
git add src/hypo_agent/skills/info_portal_skill.py src/hypo_agent/skills/__init__.py src/hypo_agent/gateway/app.py tests/skills/test_info_portal_skill.py tests/gateway/test_info_skill_registration.py
git rm src/hypo_agent/skills/info_skill.py tests/skills/test_info_skill.py
git commit -m "M8: rename info skill to info portal skill"
```

---

### Task 2: 为两个 Skill 补齐清晰 docstring，并收紧工具 description

**Files:**
- Modify: `src/hypo_agent/skills/info_portal_skill.py`
- Modify: `src/hypo_agent/skills/info_reach_skill.py`
- Modify: `tests/skills/test_info_portal_skill.py`
- Modify: `tests/test_info_reach_skill.py`

**Step 1: Write the failing test**

```python
def test_info_portal_skill_has_passive_query_docstring() -> None:
    assert "Hypo-Info 门户被动查询" in (InfoPortalSkill.__doc__ or "")
```

```python
def test_info_reach_skill_has_proactive_push_docstring() -> None:
    assert "Hypo-Info 主动推送与订阅管理" in (InfoReachSkill.__doc__ or "")
```

```python
def test_info_portal_and_info_reach_tool_descriptions_are_separated() -> None:
    portal = InfoPortalSkill(info_client=FakeInfoClient())
    reach = InfoReachSkill(db_path=tmp_path / "hypo.db")
    portal_tools = {tool["function"]["name"]: tool["function"] for tool in portal.tools}
    reach_tools = {tool["function"]["name"]: tool["function"] for tool in reach.tools}

    assert "用户主动提问" in portal_tools["info_today"]["description"]
    assert "被动查询" in portal_tools["info_search"]["description"]
    assert "内部查询" in reach_tools["info_query"]["description"]
    assert "定时推送" in reach_tools["info_summary"]["description"]
```

**Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/skills/test_info_portal_skill.py::test_info_portal_skill_has_passive_query_docstring tests/test_info_reach_skill.py::test_info_reach_skill_has_proactive_push_docstring tests/test_info_reach_skill.py::test_info_portal_and_info_reach_tool_descriptions_are_separated -q
```

Expected: FAIL，因为 docstring 和工具描述尚未按新边界收紧

**Step 3: Write minimal implementation**

- 在 `src/hypo_agent/skills/info_portal_skill.py` 顶部加入 docstring：

```python
"""Hypo-Info 门户被动查询。

用户主动提问时使用，提供今日摘要、搜索、Benchmark、分区浏览等能力。

数据来自 Hypo-Info 前端 API。
"""
```

- 在 `src/hypo_agent/skills/info_reach_skill.py` 顶部加入/更新 docstring：

```python
"""Hypo-Info 主动推送与订阅管理。

由 Scheduler 和 Heartbeat 驱动，负责定时新闻摘要推送、
高重要性文章主动通知、订阅 CRUD。

数据通过 HypoInfoClient 调用 /api/agent/* 端点。
"""
```

- 收紧工具描述：
  - `info_today`: 标注“用户主动提问时的友好入口”
  - `info_search`: 标注“用户按关键词检索”
  - `info_benchmark`: 标注“用户查看排名”
  - `info_sections`: 标注“用户浏览分区”
  - `info_query`: 标注“主动推送层内部查询/精确过滤，不作为普通门户入口”
  - `info_summary`: 标注“Scheduler/主动简报使用，不回答普通门户问题”

**Step 4: Run tests to verify it passes**

Run:

```bash
uv run pytest tests/skills/test_info_portal_skill.py tests/test_info_reach_skill.py -q
```

Expected: PASS

**Step 5: Commit**

```bash
git add src/hypo_agent/skills/info_portal_skill.py src/hypo_agent/skills/info_reach_skill.py tests/skills/test_info_portal_skill.py tests/test_info_reach_skill.py
git commit -m "M8: clarify info skill and info reach responsibilities"
```

---

### Task 3: 明确 `info_today` vs `info_query` 的用途边界，但不改行为

**Files:**
- Modify: `src/hypo_agent/skills/info_portal_skill.py`
- Modify: `src/hypo_agent/skills/info_reach_skill.py`
- Modify: `tests/skills/test_info_portal_skill.py`
- Modify: `tests/test_info_reach_skill.py`

**Step 1: Write the failing test**

```python
def test_info_today_description_marks_user_friendly_entry() -> None:
    skill = InfoPortalSkill(info_client=FakeInfoClient())
    tools = {tool["function"]["name"]: tool["function"] for tool in skill.tools}
    assert "用户主动提问" in tools["info_today"]["description"]
```

```python
def test_info_query_description_marks_internal_push_usage(tmp_path: Path) -> None:
    skill = InfoReachSkill(db_path=tmp_path / "hypo.db")
    tools = {tool["function"]["name"]: tool["function"] for tool in skill.tools}
    assert "内部查询" in tools["info_query"]["description"]
    assert "推送" in tools["info_query"]["description"]
```

**Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/skills/test_info_portal_skill.py::test_info_today_description_marks_user_friendly_entry tests/test_info_reach_skill.py::test_info_query_description_marks_internal_push_usage -q
```

Expected: FAIL，因为当前描述仍可能含混

**Step 3: Write minimal implementation**

- 只改 description 文案，不改方法签名、参数和实现
- `info_today` 明确是“门户友好入口”
- `info_query` 明确是“内部精确查询/主动推送层使用”
- 若已有类似断言，合并为一个更清晰的描述测试，避免重复

**Step 4: Run tests to verify it passes**

Run:

```bash
uv run pytest tests/skills/test_info_portal_skill.py tests/test_info_reach_skill.py -q
```

Expected: PASS

**Step 5: Commit**

```bash
git add src/hypo_agent/skills/info_portal_skill.py src/hypo_agent/skills/info_reach_skill.py tests/skills/test_info_portal_skill.py tests/test_info_reach_skill.py
git commit -m "M8: separate info today and info query tool semantics"
```

---

### Task 4: 校验配置与 gateway 注册路径，确认小改没有改变行为

**Files:**
- Modify: `config/skills.yaml`（仅在决定改键名时；当前推荐不修改）
- Modify: `tests/gateway/test_info_skill_registration.py`
- Modify: `tests/core/test_config_loader.py`（仅在决定改键名时）

**Step 1: Write the failing test**

```python
def test_build_default_deps_registers_info_runtime_key_with_info_portal_skill(...) -> None:
    ...
    skill = deps.skill_manager._skills["info"]
    assert skill.__class__.__name__ == "InfoPortalSkill"
```

**Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/gateway/test_info_skill_registration.py::test_build_default_deps_registers_info_runtime_key_with_info_portal_skill -q
```

Expected: FAIL，因为 gateway 尚未切到新类名

**Step 3: Write minimal implementation**

- 推荐方案：保持 `config/skills.yaml` 键名 `info` 不变
  - 原因：这是小改；不需要引入配置迁移和兼容逻辑
  - gateway 中继续读 `info` 配置块
  - 但实例化对象改为 `InfoPortalSkill`
- 只有在代码中 `skill.name` 被改掉时，才同步调整注册断言

**Step 4: Run tests to verify it passes**

Run:

```bash
uv run pytest tests/gateway/test_info_skill_registration.py -q
```

Expected: PASS

**Step 5: Commit**

```bash
git add src/hypo_agent/gateway/app.py tests/gateway/test_info_skill_registration.py
git commit -m "M8: keep info runtime key while renaming portal skill"
```

---

### Task 5: 全量回归与完成报告

**Files:**
- Modify: `docs/plans/2026-03-31-m8-info-portal-skill-rename-implementation-plan.md`（如需记录偏差）

**Step 1: Run focused tests**

Run:

```bash
uv run pytest tests/skills/test_info_portal_skill.py tests/test_info_reach_skill.py tests/gateway/test_info_skill_registration.py -q
```

Expected: PASS

**Step 2: Run full suite**

Run:

```bash
uv run pytest -q
```

Expected: PASS

**Step 3: Prepare completion report**

- 输出：
  - 变更文件清单
  - 实现摘要
  - 偏差记录
  - 测试结果
  - 遗留问题

**Step 4: Commit doc if needed**

```bash
git add docs/plans/2026-03-31-m8-info-portal-skill-rename-implementation-plan.md
git commit -m "M8[doc]: add info portal rename implementation plan"
```

---

## Notes for Execution

- `InfoPortalSkill` 改名的重点是“类名/文件名/文档边界”，不是 runtime 行为迁移。
- 保留 skill key `info` 不变，能把影响控制在最小范围；这是本计划的默认方案。
- 不要顺手改 `InfoSkill` 的工具行为、格式化文本或客户端逻辑，本次不做功能变更。
- `InfoReachSkill` 的 `info_query/info_summary` 仅通过 description 约束用途，不做功能合并。

---

Plan complete and saved to `docs/plans/2026-03-31-m8-info-portal-skill-rename-implementation-plan.md`. Two execution options:

**1. Subagent-Driven (this session)** - 我在当前会话按任务逐项执行并回报。

**2. Parallel Session (separate)** - 你批准后，我按 `executing-plans` 流程继续实现。
