# C1-M2 资源引用统一模型报告

> 时间：2026-05-03 02:16 Asia/Shanghai  
> Milestone：M2: 资源引用统一模型  
> 结论：PASS with follow-up dependencies。已建立最小 `ResourceRef` / `ResourceResolver` 契约，并将 `read_file` 缺失资源接入恢复动作；渠道完整发送能力留给 M5。

## 摘要

本轮按渠道优先调整了 M2 验收方向：资源解析首先服务“把刚才那个报告/附件发到 QQ/微信/飞书”，WebUI 只保留可消费契约，不作为主线。

新增：

- `src/hypo_agent/core/resource_resolution.py`
- `tests/core/test_resource_resolution.py`
- `tests/core/test_tool_outcome.py`
- `docs/architecture/resource-reference-resolution.md`

修改：

- `src/hypo_agent/skills/fs_skill.py`
- `src/hypo_agent/core/tool_outcome.py`
- `src/hypo_agent/core/skill_manager.py`
- `tests/skills/test_fs_skill.py`

## 已实现行为

`ResourceResolver` 支持：

- 精确绝对/相对路径。
- search roots 下的模糊文件名查找。
- 最近生成文件。
- 最近消息/上传附件。
- HTTP/HTTPS URL。
- 单候选直接 resolved。
- 多候选返回 ambiguous + `ask_user`。
- 无候选返回 not_found + `search_or_ask`。

`FileSystemSkill.read_file` 现在在文件不存在时返回结构化 metadata：

- `resource_resolution`
- `resource_candidates`
- `recovery_action`

`ToolOutcome` 现在识别带 `recovery_action` 的缺失资源错误，将其视为可恢复的 `user_input_error`，`retryable=True`，`breaker_weight=0`。

## 渠道优先影响

M2 不直接实现渠道发送，但把 M5 所需的输入契约提前建立：

```text
raw user phrase
  -> ResourceResolution
  -> ResourceRef or candidates
  -> channel capability / delivery layer
```

这避免后续渠道层继续接收 `"那个报告"`、`"刚才的文件"` 这类不可执行文本。

## 测试结果

已验证：

```bash
uv run pytest tests/core/test_resource_resolution.py tests/core/test_tool_outcome.py tests/skills/test_fs_skill.py -q
# 33 passed

uv run pytest tests/skills/test_skill_manager.py tests/core/test_pipeline_tools.py::test_pipeline_does_not_retry_permanent_read_file_errors -q
# 24 passed
```

存在第三方 `lark_oapi` / `websockets` deprecation warnings，不影响本轮判断。

## 未完成项

以下不应在 M2 内扩大范围处理：

- Pipeline 尚未把最近会话消息自动注入 `ResourceResolverContext`。
- 权限不足还没有建成显式 `ResourceResolution(status="blocked")`。
- 渠道能力声明、附件大小限制、fallback 仍属于 M5。
- 非 WebUI 渠道里的候选确认交互仍属于 M6/M7。

## 后续建议

M3 应在工具调用前后统一消费 `ResourceResolution`：

- 参数校验发现资源字段时先 resolver。
- 缺参/歧义/缺资源都返回统一 recovery envelope。
- 工具失败后不再只依赖字符串匹配判断恢复路径。

M5 应提前到 M4 前执行：

- 定义 `ChannelCapability`。
- 让 QQ/微信/飞书消费 `ResourceRef`。
- `DeliveryResult` 增加 per-attachment outcome。
- 文件发送失败时返回 fallback 动作。

M6 主动性状态机应以渠道场景为主：

```text
resolve_resource -> validate_channel_capability -> send_or_upload
  -> retry_or_fallback -> verify_delivery -> give_up_explained
```

## 评估

| 维度 | 分数 | 说明 |
| --- | --- | --- |
| diff_score | 2/5 | 新增核心模块、测试、文档，并小改 fs/outcome/skill_manager。 |
| code_quality | 2/5 | 实现范围清晰，仍是最小契约版本。 |
| test_coverage | 3/5 | 覆盖核心解析与 fs/outcome 集成；尚缺 pipeline/channel 端到端。 |
| complexity | 2/5 | 复杂度集中在新模块，没有扩散到渠道层。 |
| architecture_drift | 2/5 | 引入新核心契约，符合 M1 结论。 |
| overall | 2/5 | 可进入 M3/M5；仍有明确依赖项。 |

判定：PASS。  
下一步：进入 M3 工具调用契约与恢复循环，并在执行顺序上将 M5 渠道文件能力提前到 M4 前。
