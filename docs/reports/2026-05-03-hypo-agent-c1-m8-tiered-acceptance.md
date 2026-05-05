# C1-M8 分级测试与上线验收报告

> 时间：2026-05-03 02:34 Asia/Shanghai  
> Milestone：M8: 分级测试与上线验收  
> 结论：PASS for local channel-first gates。真实渠道 smoke 未执行，因其需要显式 opt-in 和外部发送确认。

## 摘要

M8 建立了渠道优先验收矩阵，并执行默认本机 gates。

新增：

- `docs/runbooks/c1-channel-first-acceptance.md`
- `tests/docs/test_c1_acceptance_runbook.py`

## 默认本机验收结果

执行命令：

```bash
uv run pytest tests/core/test_resource_resolution.py tests/core/test_tool_outcome.py tests/core/test_delivery_capability.py tests/core/test_active_recovery.py tests/core/test_operation_events.py tests/skills/test_skill_manager.py tests/skills/test_fs_skill.py tests/channels/test_channel_attachment_capabilities.py tests/channels/test_feishu_channel.py tests/channels/test_weixin_adapter.py tests/gateway/test_qqbot_channel.py tests/docs/test_c1_acceptance_runbook.py -q
```

结果：

- 96 passed。
- 仅有第三方 deprecation warnings。

覆盖：

- 资源解析。
- 工具恢复 metadata。
- required 参数前置校验。
- 渠道附件能力声明。
- 主动恢复决策。
- operation event payload。
- QQ/微信/飞书本机模拟文件/图片路径。
- 验收 runbook 的 opt-in 边界。

## 未执行项

未执行真实 QQ/微信/飞书发送 smoke。

原因：

- 会产生外部副作用。
- 需要真实 app id、secret、bot token、target user/chat/openid。
- 需要用户明确授权测试账号发送。

未执行 M4 网页/浏览器验收。

原因：

- 用户明确当前主要不用 WebUI，优先渠道。
- M4 已记录为 deferred。

## 当前风险

- 主动恢复还没有接入 pipeline 主循环。
- operation event 还没有接入 WebSocket 或渠道 outbound 消息。
- 每个真实上传分支还未完整记录 `AttachmentDeliveryOutcome`。
- 用户确认后的 resume token 尚未实现。
- 真实渠道 smoke 未跑，不能声明真实外部发送已通过。

## 回滚建议

如需回滚本轮 C1 改动，优先回滚新增核心合约文件和对应接入点：

- `src/hypo_agent/core/resource_resolution.py`
- `src/hypo_agent/core/active_recovery.py`
- `src/hypo_agent/core/operation_events.py`
- `src/hypo_agent/core/delivery.py`
- `src/hypo_agent/core/skill_manager.py`
- `src/hypo_agent/skills/fs_skill.py`
- 三渠道 `attachment_capability` 声明

但不建议整体回滚，因为当前改动保持向后兼容，且默认 gates 已通过。

## 后续建议

下一轮应开新 Cycle 或 Patch，聚焦“编排接入”：

1. Pipeline 消费 `ResourceResolution` 和 `ActiveRecoveryDecision`。
2. 对候选确认生成可恢复 operation id / resume token。
3. 渠道消息输出 `OperationEvent` 的人类可读版本。
4. 真实 QQ/微信/飞书 smoke 在显式授权后执行。
5. 再恢复 M4 网页读取架构。

## 评估

| 维度 | 分数 | 说明 |
| --- | --- | --- |
| diff_score | 1/5 | 新增 runbook 和文档测试。 |
| code_quality | 2/5 | 验收边界明确。 |
| test_coverage | 3/5 | 默认本机 gates 覆盖 C1 关键契约；真实外部 smoke 未执行。 |
| complexity | 1/5 | 低复杂度。 |
| architecture_drift | 1/5 | 验收文档不改变架构。 |
| overall | 2/5 | C1 本机验收通过，真实渠道验收待 opt-in。 |

判定：PASS。  
C1 状态：Completed with deferred M4 and external smoke pending explicit opt-in。
