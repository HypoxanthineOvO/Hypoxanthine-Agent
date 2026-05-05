# C4 M8 - Notion Plan 独立 Skill 与工具分流

## 结果

- 新增 `src/hypo_agent/skills/notion_plan_skill.py`。
- 新增 SkillCatalog 入口 `skills/hybrid/notion-plan/SKILL.md`。
- 新增配置项 `config/skills.yaml -> notion_plan.enabled=true`。
- 普通 Notion skill 去掉计划通触发词，计划通请求优先匹配 `notion-plan`。

## 工具

- `notion_plan_get_today`
- `notion_plan_get_structure`
- `notion_plan_add_items`

## 兼容

- 旧 `notion_get_plan_today` 未删除，普通 Notion 读写仍走 `notion`。
- `notion-plan` 只抢计划通相关请求，不抢“帮我在 Notion 创建一个页面”。

## 验证

- `uv run pytest tests/skills/test_notion_plan_skill.py tests/core/test_skill_catalog_repo.py -q`

