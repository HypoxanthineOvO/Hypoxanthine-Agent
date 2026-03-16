# M4 Tool Calling Connectivity Runbook

## Scope

用于排查以下问题：

- 模型是否可连通（鉴权 / API Base / 模型名）
- 模型是否返回 `tool_calls`
- Pipeline / SkillManager 是否实际触发工具执行

## Prerequisites

- Python 3.12 + `uv`
- 已配置 `config/models.yaml`、`config/secrets.yaml`

## 1) 单模型工具调用探测

```bash
uv run python /tmp/test_litellm_tool_call.py
```

判定规则：

- 报 `AuthenticationError` / `APIConnectionError`：连通性未通过
- 返回文本但 `tool_calls=None`：模型可用但未触发工具调用
- `tool_calls` 非空：工具调用能力正常

## 2) 仓库内全模型巡检

```bash
uv run scripts/check_models.py
```

常用参数：

```bash
uv run scripts/check_models.py --models KimiK25
uv run scripts/check_models.py --timeout 8
uv run scripts/check_models.py --no-tool-call
```

退出码：

- `0`: 所有模型连通且返回 `tool_calls`
- `1`: 存在连通失败
- `2`: 连通成功但存在 `tool_calls=0`

## 3) 端到端工具调用验证

1. 启动后端：

```bash
uv run python -m hypo_agent
```

2. 用 WebSocket 发送：

```json
{"type":"user_message","text":"请运行 echo hello"}
```

3. 检查后端日志：

- `react.start` / `react.round`
- `call_with_tools.request` / `call_with_tools.response_raw`
- `skill.invoke.start`

若 `react.round tool_calls=0` 且无 `skill.invoke.start`，优先回到步骤 1/2 校验 provider 兼容性。
