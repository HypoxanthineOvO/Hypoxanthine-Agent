# C1-M6 Agent 主动性状态机报告

> 时间：2026-05-03 02:29 Asia/Shanghai  
> Milestone：M6: Agent 主动性状态机  
> 结论：PASS，已建立渠道优先主动恢复决策核心；pipeline 编排接入留给后续。

## 摘要

M6 将“主动性”落成可测试状态机核心，而不是继续依赖提示词。当前实现聚焦渠道文件发送场景。

新增：

- `src/hypo_agent/core/active_recovery.py`
- `tests/core/test_active_recovery.py`
- `docs/architecture/agent-active-recovery-state-machine.md`

## 已实现行为

`ActiveRecoveryStateMachine` 支持：

- 资源歧义：进入 `ask_user` / `confirm_resource`。
- 资源缺失：进入 `ask_user` / `clarify_resource`。
- 资源 blocked：进入 `give_up_explained`。
- 渠道不支持附件类型：进入 `fallback`，选择 capability 声明的 fallback。
- 发送失败且预算未耗尽：进入 `retry` / `retry_upload`。
- 发送失败且预算耗尽：进入 `give_up_explained`。
- 发送成功：进入 `verify_result` / `verify_delivery`。

## 测试结果

已验证：

```bash
uv run pytest tests/core/test_active_recovery.py tests/core/test_delivery_capability.py tests/channels/test_channel_attachment_capabilities.py tests/core/test_resource_resolution.py tests/core/test_tool_outcome.py -q
# 17 passed

uv run pytest tests/skills/test_skill_manager.py tests/skills/test_fs_skill.py tests/channels/test_feishu_channel.py tests/channels/test_weixin_adapter.py tests/gateway/test_qqbot_channel.py -q
# 76 passed
```

第三方 deprecation warnings 仍存在，不影响 M6。

## 限制

M6 当前是决策核心，不直接：

- 调用工具。
- 发送渠道消息。
- 修改 pipeline 状态。
- 持久化 resume token。
- 处理用户确认后的继续执行。

这些需要后续在编排层接入。

## 下一步建议

进入 M7，但按用户偏好降级 WebUI 工作量：

- 优先整理后端事件/API envelope。
- 让渠道消息也能表达候选确认、fallback、retry、give_up。
- WebUI 只保持类型兼容，不做重 UI。

M4 网页阅读继续暂缓。

## 评估

| 维度 | 分数 | 说明 |
| --- | --- | --- |
| diff_score | 2/5 | 新增独立状态机核心和测试。 |
| code_quality | 2/5 | 决策核心简单可测，但尚未编排集成。 |
| test_coverage | 3/5 | 覆盖主要状态转移；缺端到端渠道流程。 |
| complexity | 2/5 | 避免直接改 pipeline 大循环。 |
| architecture_drift | 2/5 | 符合渠道优先主动性目标。 |
| overall | 2/5 | 可进入 M7 契约可观测性。 |

判定：PASS。  
下一步：进入 M7 前后端/渠道事件契约与可观测性。
