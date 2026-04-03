---
name: "code-run"
description: "Sandboxed code execution for temporary scripts. Prefers bwrap isolation when available."
compatibility: "linux"
allowed-tools: "run_code"
metadata:
  hypo.category: "internal"
  hypo.backend: "code_run"
  hypo.exec_profile:
  hypo.triggers: ""
  hypo.risk: "medium"
  hypo.dependencies: "bash,python,bwrap(optional)"
---

# Code Run 使用说明

当任务需要写入一个临时脚本文件并执行一次时，使用这个 backend。它适合多行 Python 或 shell 片段、快速数据处理和一次性计算。

## 工具职责

- `run_code` 会把提供的代码写入 temp file，执行它，捕获输出，然后删除该文件。
- 支持的语言是运行时支持的 `python` 和 `shell`。

## Code Run 与 Exec 的区别

- 直接 shell command 用 `exec_command`。
- 当逻辑天然是多行，或更适合写成临时脚本时，用 `run_code`。
- 如果你在长 one-liner 和短脚本之间犹豫，只要可读性开始变差，就优先 `run_code`。

## Sandbox 行为

- `code_run` 在可用时优先使用 `bwrap` 隔离。
- sandbox 会绑定选定的可写路径，并基于 permission manager 屏蔽 blocked path。
- 根据运行时 wrapper 的实现，network sharing 可能仍然开启，所以这不是完美的安全边界。
- 如果 `bwrap` 不可用或运行时失败，backend 可能回退到无 sandbox 的 shell 路径。这是风险较高任务中必须记住的实现约束。

## 安全规则

- 不要把 `run_code` 用于长时间运行的进程。
- 所有文件系统访问都应视为受运行时 writable path 集合和 blocked path masking 约束。
- 脚本要尽量小、只服务当前任务。这个 backend 用于一次性执行，不是写可复用项目代码。
