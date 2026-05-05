# C1-M7 前后端/渠道契约与可观测性报告

> 时间：2026-05-03 02:31 Asia/Shanghai  
> Milestone：M7: 前后端契约与可观测性  
> 结论：PASS，已建立后端 operation event envelope；WebUI 展示未作为主线展开。

## 摘要

按用户要求，M7 不做 WebUI 重 UI，优先建立后端/API/渠道可共用事件契约。

新增：

- `src/hypo_agent/core/operation_events.py`
- `tests/core/test_operation_events.py`
- `docs/architecture/operation-event-contract.md`

## 已实现行为

`OperationEvent` 可序列化：

- `resource_candidates`
- `channel_delivery`

payload 统一包含：

- `type=operation_event`
- `event_type`
- `operation_id`
- `session_id`
- `status`
- `timestamp`
- `candidates`
- `delivery`
- `recovery_action`

该 envelope 可被 WebSocket、渠道消息、日志或后续 trace 存储复用。

## 测试结果

已验证：

```bash
uv run pytest tests/core/test_operation_events.py tests/core/test_active_recovery.py tests/core/test_delivery_capability.py tests/core/test_resource_resolution.py tests/core/test_tool_outcome.py -q
# 16 passed

uv run pytest tests/skills/test_skill_manager.py tests/skills/test_fs_skill.py tests/channels/test_channel_attachment_capabilities.py tests/channels/test_feishu_channel.py tests/channels/test_weixin_adapter.py tests/gateway/test_qqbot_channel.py -q
# 79 passed
```

第三方 deprecation warnings 仍存在，不影响 M7。

## 限制

M7 当前没有把 operation event 接入：

- WebSocket stream。
- QQ/微信/飞书 outbound 消息。
- 持久化 trace store。
- WebUI 组件。

这是有意范围控制：用户明确主要不用 WebUI，当前收益最高的是先统一事件结构。

## 后续建议

M8 应建立分级验收：

- L1：核心契约单测。
- L2：fs/tool/channel 模拟集成。
- L3：pipeline/channel operation event 回放。
- L4：真实 QQ/微信/飞书 smoke，默认 opt-in。

M4 网页阅读仍可继续暂缓，除非渠道场景需要 URL 内容读取。

## 评估

| 维度 | 分数 | 说明 |
| --- | --- | --- |
| diff_score | 1/5 | 新增小型事件 envelope 和测试。 |
| code_quality | 2/5 | 简单可序列化，未过度设计。 |
| test_coverage | 3/5 | 覆盖两类核心事件；缺实际 emitter 接入。 |
| complexity | 1/5 | 低复杂度。 |
| architecture_drift | 2/5 | 与渠道优先可观测性目标一致。 |
| overall | 2/5 | 可进入 M8 分级验收。 |

判定：PASS。  
下一步：进入 M8 分级测试与上线验收。
