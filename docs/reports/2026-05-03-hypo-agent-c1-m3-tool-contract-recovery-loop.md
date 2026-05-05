# C1-M3 工具调用契约与恢复循环报告

> 时间：2026-05-03 02:20 Asia/Shanghai  
> Milestone：M3: 工具调用契约与恢复循环  
> 结论：PASS，完成最小恢复 envelope 和 required 参数前置校验；完整主动恢复状态机留给 M6。

## 摘要

M3 在 M2 `ResourceResolution` 基础上补齐了工具层最小恢复契约。现在缺少必填参数时，`SkillManager` 会在调用 skill 前返回结构化恢复动作，而不是让工具内部抛异常或返回零散错误。

新增：

- `docs/architecture/tool-contract-recovery-loop.md`

修改：

- `src/hypo_agent/core/skill_manager.py`
- `tests/skills/test_skill_manager.py`

沿用 M2 修改：

- `src/hypo_agent/core/tool_outcome.py`
- `src/hypo_agent/skills/fs_skill.py`
- `src/hypo_agent/core/resource_resolution.py`

## 已实现行为

`SkillManager.invoke` 现在读取工具 schema 的 `parameters.required`：

- 缺必填参数时不执行 skill。
- 返回 `SkillOutput(status="error")`。
- 填充 `metadata.missing_fields`。
- 填充 `metadata.recovery_action`。
- 附加 outcome 元数据：`outcome_class=user_input_error`、`retryable=True`、`breaker_weight=0`。

这与 M2 的资源恢复保持一致：只要工具输出包含明确恢复动作，`ToolOutcome` 就不会把它当成不可恢复死错误。

## 测试结果

已验证：

```bash
uv run pytest tests/skills/test_skill_manager.py tests/core/test_tool_outcome.py tests/core/test_resource_resolution.py -q
# 30 passed

uv run pytest tests/core/test_pipeline_tools.py::test_pipeline_does_not_retry_permanent_read_file_errors tests/core/test_pipeline_tools.py::test_pipeline_retries_retryable_tool_once_after_failure -q
# 2 passed
```

仍有第三方 deprecation warnings，不影响 M3。

## 限制

M3 只实现 JSON Schema `required` 子集，不处理：

- 类型校验。
- enum/min/max。
- nested object。
- oneOf/anyOf。
- operation resume token。
- pipeline 自动继续恢复动作。

这些应在 M6 主动状态机或后续增强中处理，避免 M3 过度扩大。

## 渠道优先调整

用户已确认主线优先渠道，不主要使用 WebUI。因此后续建议：

1. 将 M5 渠道文件能力提前到 M4 前执行。
2. M4 网页阅读后移。
3. M7 降级为 API/事件契约优先，WebUI 展示次要。

M5 应基于 M2/M3 契约实现：

- `ChannelCapability`
- per-attachment `DeliveryResult`
- 文件上传失败恢复动作
- 不支持附件时的 fallback 动作

## 评估

| 维度 | 分数 | 说明 |
| --- | --- | --- |
| diff_score | 2/5 | 小范围修改 SkillManager 和测试，新增架构文档。 |
| code_quality | 2/5 | 实现简单明确，但仅覆盖 required 子集。 |
| test_coverage | 3/5 | 覆盖缺参、outcome、resource 相关回归；未覆盖 full schema。 |
| complexity | 2/5 | 没有引入复杂状态机。 |
| architecture_drift | 2/5 | 与 M1/M2 目标一致。 |
| overall | 2/5 | 可进入渠道能力 Milestone。 |

判定：PASS。  
下一步：按渠道优先，进入 M5 渠道文件能力。
