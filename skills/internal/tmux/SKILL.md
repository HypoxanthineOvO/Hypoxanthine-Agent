---
name: "tmux"
description: "Persistent terminal session management. Legacy primitive, prefer exec for one-shot commands."
compatibility: "linux"
allowed-tools: "tmux_send tmux_read"
metadata:
  hypo.category: "internal"
  hypo.backend: "tmux"
  hypo.exec_profile:
  hypo.triggers: ""
  hypo.risk: "medium"
  hypo.dependencies: "tmux"
---

# Tmux 使用说明

这是 legacy 的持久终端 backend。它不是普通命令执行的默认路径，在常规运行里通常也是关闭的。

## 工具选择

- `tmux_send`：向持久 tmux window 发送命令。
- `tmux_read`：读取该持久 window 的最近输出。

## 什么时候适合 Tmux

- 像 `tail -f` 这样的长时间交互式监控。
- 必须跨多个 tool call 存活的后台服务或会话。
- 确实需要持久 shell 状态的场景。

## Tmux 与 Exec 的区别

- 普通一次性命令用 `exec_command`。
- 只有当命令必须在多次调用之间保持存活，或确实需要持久 terminal context 时，才用 `tmux`。
- 不要只是为了跑一次普通 shell command 就使用 `tmux`。

## 安全规则

- 这个 backend 会累积 terminal state，因此旧输出和旧会话上下文可能污染后续读取。
- 当 workflow 需要隔离时，优先创建或指定明确的 session/window 名称。
- 明确说明你正在读取哪个 session。
- 把 `tmux` 视为比 `exec` 更重、更不确定的操作路径。
