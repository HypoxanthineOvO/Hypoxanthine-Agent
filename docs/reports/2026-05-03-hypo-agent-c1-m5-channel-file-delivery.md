# C1-M5 渠道文件能力报告

> 时间：2026-05-03 02:26 Asia/Shanghai  
> Milestone：M5: 渠道文件能力  
> 结论：PASS，已建立统一附件能力合约和三渠道能力声明；完整 per-upload outcome 需要在 M6 主动恢复中继续接入。

## 摘要

按用户要求，M5 提前执行并优先支持 QQ/微信/飞书渠道文件能力。M5 没有重写所有渠道发送路径，而是先建立所有渠道都能声明和消费的统一合约。

新增：

- `tests/core/test_delivery_capability.py`
- `tests/channels/test_channel_attachment_capabilities.py`
- `docs/architecture/channel-file-delivery.md`

修改：

- `src/hypo_agent/core/delivery.py`
- `src/hypo_agent/channels/qq_bot_channel.py`
- `src/hypo_agent/channels/weixin/weixin_adapter.py`
- `src/hypo_agent/channels/feishu_channel.py`

## 已实现行为

`ChannelCapability`：

- 声明渠道名。
- 声明支持的附件类型。
- 声明大小限制。
- 声明 fallback 动作。

`AttachmentDeliveryOutcome`：

- 记录单个附件是否发送成功。
- 记录失败原因。
- 可携带 `recovery_action`，例如 `fallback_to_link`。

`DeliveryResult`：

- 保持旧字段兼容。
- 新增 `attachment_outcomes`。
- `combine_delivery_results` 会合并 per-attachment outcomes。

三渠道能力声明：

- QQ Bot：image/file/audio/video。
- 微信：image/file/video。
- 飞书：image/file。

## 测试结果

已验证：

```bash
uv run pytest tests/channels/test_channel_attachment_capabilities.py tests/core/test_delivery_capability.py -q
# 6 passed

uv run pytest tests/channels/test_feishu_channel.py tests/channels/test_weixin_adapter.py tests/gateway/test_qqbot_channel.py -q
# 25 passed
```

第三方 deprecation warnings 仍存在，不影响 M5。

## 未完成项

M5 仍未完成真实渠道端到端验收，原因是需要实际测试账号、机器人 token、目标用户和网络可达性。当前完成的是本机模拟和合约层。

后续还需要：

- 在每个渠道的具体上传/发送分支填充真实 `AttachmentDeliveryOutcome`。
- 在发送前基于 `ChannelCapability` 做预检查。
- 对不支持附件类型、超大小、上传失败生成明确 fallback。
- 将 ResourceRef 直接接入渠道发送入口。

## 后续建议

进入 M6，不恢复 M4。原因：用户主要不用 WebUI，当前关键收益是让 Agent 主动完成渠道发送恢复。

M6 应实现：

```text
resolve_resource
  -> validate_channel_capability
  -> send_or_upload
  -> retry_or_fallback
  -> verify_delivery
  -> give_up_explained
```

## 评估

| 维度 | 分数 | 说明 |
| --- | --- | --- |
| diff_score | 2/5 | 修改 delivery 合约和三渠道声明，未重写发送主流程。 |
| code_quality | 2/5 | 类型简单，兼容性较好。 |
| test_coverage | 3/5 | 覆盖合约、能力声明和现有三渠道模拟回归。 |
| complexity | 2/5 | 保持合约层改动，避免渠道实现大重构。 |
| architecture_drift | 2/5 | 符合渠道优先调整。 |
| overall | 2/5 | 可进入 M6 主动性状态机。 |

判定：PASS。  
下一步：进入 M6 Agent 主动性状态机。
