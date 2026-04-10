---
name: "tmux"
description: "持久 terminal session 管理。仅在需要跨多次调用保留 shell state 的场景使用，普通 one-shot command 优先 exec。"
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

# Tmux 使用指南

## 定位 (Positioning)

`tmux` 是 legacy 的持久 terminal backend，主要用于跨多次调用保留 shell state。普通 one-shot command 不应默认走它。

## 适用场景 (Use When)

- 需要持续存活的后台服务或会话。
- 需要长时间交互式监控，例如 `tail -f`。
- 任务必须跨多个 tool call 保留 terminal context。

## 工具与接口 (Tools)

- `tmux_send`：向持久 `tmux window` 发送命令。
- `tmux_read`：读取该 window 的最近输出。

## 标准流程 (Workflow)

1. 先判断任务是否真的需要持久 session，而不是普通 `exec`。
2. 明确要操作的 `session/window` 名称，避免混入旧上下文。
3. 用 `tmux_send` 发出命令，再用 `tmux_read` 拉取输出。
4. 对读取结果做二次判断，因为旧输出可能仍残留在 buffer 中。

## 边界与风险 (Guardrails)

- `tmux` 会累积 terminal state，旧输出可能污染当前判断。
- workflow 需要隔离时，应显式指定新的 `session/window`。
- 回复中要说明当前读取的是哪个 session，避免上下文错位。
- 如果任务只是普通 shell command，不要为了“像真人终端”而使用 `tmux`。
