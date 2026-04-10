---
name: "exec"
description: "核心 shell execution backend。所有 CLI-based skill 的底层一次性 subprocess primitive。"
compatibility: "linux"
allowed-tools: "exec_command exec_script"
metadata:
  hypo.category: "internal"
  hypo.backend: "exec"
  hypo.exec_profile: "default"
  hypo.triggers: ""
  hypo.risk: "high"
  hypo.dependencies: "bash,python"
---

# Exec 使用指南

## 定位 (Positioning)

`exec` 是最基础的 shell execution backend，负责在新的 subprocess 中运行 one-shot command 或临时脚本。凡是 CLI-based skill 需要落到命令执行层，通常都会经过它。

## 适用场景 (Use When)

- 任务本质上是一次性 shell command、短 pipeline 或解释器调用。
- 需要 `exec_profile` 对命令前缀做约束。
- 不需要持久 terminal state，也不需要跨多次调用保留 session。

## 工具与接口 (Tools)

- `exec_command`：执行单条 command，适合现成 CLI 调用。
- `exec_script`：执行临时多行脚本，适合比 one-liner 更清晰的场景。

## 标准流程 (Workflow)

1. 先判断任务是否真的是 one-shot execution，而不是 `tmux` 或 `code-run` 场景。
2. 能用现成命令表达时，优先选择 `exec_command`。
3. 当逻辑需要多行脚本，但仍适合一次性执行时，改用 `exec_script`。
4. 根据当前 skill 或 runtime 配置带上合适的 `exec_profile`。
5. 总结关键输出，不要把截断后的原始输出当成完整事实。

## 边界与风险 (Guardrails)

- `exec` 不是 `sandbox`，它会继承当前进程环境和宿主机上下文。
- 除非 profile 明确允许且用户明确要求，否则不要执行 destructive action 或不可逆系统变更。
- 对可能卡住的 interactive command，优先改成非交互模式，例如 `-y`、`--no-pager` 或明确输入参数。
- `default` profile 仍会拦截明显危险的前缀，例如 `rm -rf /`、`shutdown`、`reboot`、`mkfs`、`dd if=`。

## 常见模式 (Playbooks)

### `exec` vs `code-run`

1. 单条命令、短 shell pipeline、现成 CLI 调用，走 `exec`。
2. 需要临时脚本文件，或希望优先获得 `bwrap sandbox` 的场景，走 `code-run`。

### `exec` vs `tmux`

1. 如果任务需要跨多次调用保留 shell state，切到 `tmux`。
2. 如果只是普通 one-shot command，不要为了“像终端”而使用 `tmux`。
