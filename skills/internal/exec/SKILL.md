---
name: "exec"
description: "Core shell command execution. The runtime primitive behind all CLI-based Skills."
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

# Exec 使用说明

这是所有 CLI-based skill 背后的核心一次性命令执行器。当任务需要在新的 subprocess 中执行 shell，且不需要持久终端状态时，使用它。

## 工具选择

- 普通 shell command 用 `exec_command`。
- 当任务更适合写成临时多行脚本，而不是单条 shell command 时，用 `exec_script`。
- 如果任务需要持久 shell session 或逐步交互的 terminal，不要强行走 `exec`；那是 `tmux_send` 和 `tmux_read` 的边界。

## Execution Profiles 说明

- 很多 pure CLI skill 会带着 `exec_profile` 调用 `exec_command` 或 `exec_script`。
- profile 决定哪些命令前缀被允许，哪些被拒绝。
- 当 `git-workflow` 或 `system-service-ops` 这类 pure skill 激活时，runtime 应自动传入该 skill 的 profile。
- 如果没有显式 profile，则使用 `default` profile。

## 安全规则

- `default` profile 会保持向后兼容，但仍会拒绝已知的破坏性前缀，例如 `rm -rf /`、`shutdown`、`reboot`、`mkfs` 和 `dd if=`。
- 除非当前 profile 明确允许，而且用户明确要求，否则绝不要用 `exec` 做破坏性或不可逆的系统变更。
- `exec` 不是 sandbox。它会继承当前进程环境，并在当前机器上下文中运行。
- 交互式命令可能一直挂到 timeout。除非确有必要，否则优先使用 `-y` 这类非交互参数或管道确认。

## Exec 与 Code Run 的区别

- 对已存在为命令形式的一次性 shell command 或解释器调用，用 `exec`。
- 当你需要临时脚本文件，并希望在可用时获得偏 sandbox 的 `bwrap` 行为时，用 `code_run`。
- 简单说：单条命令或短 shell pipeline 走 `exec`；一次性脚本执行走 `code_run`。

## 输出与超时

- 每次调用都在新的 subprocess 中运行。
- 输出会因安全原因被截断。
- timeout 是硬性执行的：先发 SIGTERM，宽限后再发 SIGKILL。
