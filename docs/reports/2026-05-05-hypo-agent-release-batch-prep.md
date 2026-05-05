# Hypo-Agent 分批 Release 准备

## 当前运行状态

- 分支：`main`
- 后端：`http://127.0.0.1:8765`
- 前端：`http://127.0.0.1:5178`
- 健康检查：
  - `GET /api/health` 返回 `200`
  - 前端首页返回 `200`
- 备注：`5173` 被其他项目占用，Hypo-Agent 前端继续使用 `5178`。

## Sync 状态

已执行：

```text
hypo-workflow sync --check-only
hypo-workflow sync --repair
```

当前结果：

- `error_count: 0`
- `stale_count: 1`
- 剩余 warning：`.pipeline/reports.compact.md` 缺失，原因是 `source_missing`
- 已刷新：`.pipeline/PROGRESS.compact.md`、`.pipeline/metrics.compact.yaml`、`PROJECT-SUMMARY.md`

## Release 前置判断

当前 worktree 不满足正式 release 自动化前置条件，因为存在大量未提交变更。建议先做分批 commit，最后再进入版本号、CHANGELOG、tag 和 push。

需要先排除或单独处理：

- `logs/backend.log`
- `logs/frontend.log`
- `tmp/imagegen/hypo-agent-poster-prompt.txt`
- `output/imagegen/*.png`
- `.plan-state/*`：如需保留规划痕迹，可单独作为 workflow planning commit；否则不要混入产品功能 release。

## 建议批次

### Batch 1：Workflow / OpenCode Adapter

目的：先固定工具入口和 Hypo-Workflow/OpenCode 适配产物，避免后续功能批次里混入大量生成文件。

候选路径：

- `AGENTS.md`
- `.opencode/`
- `opencode.json`
- `tui.json`
- `PROJECT-SUMMARY.md`

建议测试：

```text
hypo-workflow sync --check-only
```

### Batch 2：Agent Runtime Recovery / Tool Display / LLM Fallback

目的：发布工具调用失败折叠、恢复、模型 fallback、运行态事件和展示名称映射。

候选路径：

- `src/hypo_agent/core/active_recovery.py`
- `src/hypo_agent/core/litellm_runtime.py`
- `src/hypo_agent/core/model_router.py`
- `src/hypo_agent/core/model_request_options.py`
- `src/hypo_agent/core/operation_events.py`
- `src/hypo_agent/core/tool_display.py`
- `src/hypo_agent/core/tool_narration.py`
- `src/hypo_agent/core/tool_outcome.py`
- `src/hypo_agent/core/narration_observer.py`
- `src/hypo_agent/core/pipeline.py`
- `src/hypo_agent/core/skill_manager.py`
- `src/hypo_agent/gateway/ws.py`
- `web/src/composables/useChatSocket.ts`
- `web/src/composables/__tests__/useChatSocket.spec.ts`
- `web/src/types/message.ts`

建议测试：

```text
uv run pytest tests/core/test_model_router.py tests/core/test_model_request_options.py tests/core/test_litellm_runtime.py tests/core/test_active_recovery.py tests/core/test_operation_events.py tests/core/test_tool_display.py tests/core/test_tool_outcome.py tests/core/test_pipeline_tools.py tests/unit/test_pipeline_error_handling.py -q
```

### Batch 3：Channel Delivery / Attachments / Outbound CLI

目的：发布 QQ/微信/飞书文件与图片投递、通道能力声明、Agent CLI outbound send。

候选路径：

- `src/hypo_agent/cli.py`
- `src/hypo_agent/core/outbound_send.py`
- `src/hypo_agent/core/channel_adapter.py`
- `src/hypo_agent/core/channel_progress.py`
- `src/hypo_agent/core/delivery.py`
- `src/hypo_agent/channels/qq_bot_channel.py`
- `src/hypo_agent/channels/weixin/weixin_adapter.py`
- `src/hypo_agent/channels/feishu_channel.py`
- `tests/gateway/test_outbound_api.py`
- `tests/scripts/test_agent_cli_send.py`
- `tests/channels/test_channel_attachment_capabilities.py`
- `tests/core/test_outbound_send_service.py`
- `tests/core/test_channel_adapter.py`
- `tests/core/test_channel_progress.py`
- `tests/core/test_delivery_capability.py`
- `tests/gateway/test_qqbot_channel.py`
- `tests/gateway/test_qqbot_ws_channel.py`

建议测试：

```text
uv run pytest tests/gateway/test_outbound_api.py tests/scripts/test_agent_cli_send.py tests/channels/test_channel_attachment_capabilities.py tests/core/test_outbound_send_service.py tests/core/test_channel_adapter.py tests/core/test_channel_progress.py tests/core/test_delivery_capability.py tests/gateway/test_qqbot_channel.py tests/gateway/test_qqbot_ws_channel.py -q
```

### Batch 4：Image Generation Skill

目的：发布生图 skill、历史记录、通道意图和审计脚本。

候选路径：

- `src/hypo_agent/skills/image_gen_skill.py`
- `src/hypo_agent/core/image_gen_history.py`
- `scripts/audit_image_gen_cli.py`
- `tests/skills/test_image_gen_skill.py`
- `tests/skills/test_image_gen_channel_intent.py`
- `tests/skills/test_image_gen_e2e.py`
- `tests/scripts/test_audit_image_gen_cli.py`
- `docs/architecture/image-gen-capability-audit.md`
- `docs/architecture/image-gen-skill-contract.md`
- `docs/runbooks/c2-image-gen-acceptance.md`

建议测试：

```text
uv run pytest tests/skills/test_image_gen_skill.py tests/skills/test_image_gen_channel_intent.py tests/skills/test_image_gen_e2e.py tests/scripts/test_audit_image_gen_cli.py -q
```

### Batch 5：Notion Plan Dedicated Skill

目的：发布 Notion Plan 独立 skill、计划通结构读取、解析、定位、写入、重复跳过与换行摘要。

候选路径：

- `skills/hybrid/notion-plan/SKILL.md`
- `skills/hybrid/notion/SKILL.md`
- `src/hypo_agent/core/notion_plan.py`
- `src/hypo_agent/core/notion_plan_editor.py`
- `src/hypo_agent/skills/notion_plan_skill.py`
- `src/hypo_agent/channels/notion/notion_client.py`
- `src/hypo_agent/gateway/app.py`
- `src/hypo_agent/skills/__init__.py`
- `config/skills.yaml`
- `config/narration.yaml`
- `tests/core/test_notion_plan_reader.py`
- `tests/core/test_notion_plan_editor.py`
- `tests/skills/test_notion_plan_skill.py`
- `tests/channels/test_notion_client.py`
- `tests/skills/test_notion_skill.py`
- `tests/core/test_skill_catalog_repo.py`

建议测试：

```text
uv run pytest tests/core/test_notion_plan_reader.py tests/core/test_notion_plan_editor.py tests/skills/test_notion_plan_skill.py tests/channels/test_notion_client.py tests/skills/test_notion_skill.py tests/core/test_skill_catalog_repo.py -q
```

### Batch 6：Documentation / Reports / Runbooks

目的：补齐本轮架构、审查、验收和运行报告。

候选路径：

- `docs/architecture/*.md`
- `docs/reports/2026-05-03-hypo-agent-*.md`
- `docs/reports/2026-05-05-hypo-agent-*.md`
- `docs/runbooks/c1-channel-first-acceptance.md`
- `docs/runbooks/c2-image-gen-acceptance.md`
- `docs/runbooks/c3-agent-cli-plan-acceptance.md`
- `tests/docs/test_c1_acceptance_runbook.py`

建议测试：

```text
uv run pytest tests/docs/test_c1_acceptance_runbook.py -q
```

## 最终 Release Gate

分批 commit 后再执行：

```text
hypo-workflow sync --repair
uv run pytest -q
git diff --check
```

如果全部通过，再更新版本、生成 changelog、打 tag。当前 `pyproject.toml` 版本为 `1.5.0`，下一版建议从 `1.6.0` 或按补丁范围选择 `1.5.1`。
