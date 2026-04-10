---
name: "code-run"
description: "一次性临时脚本执行（Python / shell），可用时优先走 bwrap sandbox。适合多行代码、快速数据处理与临时计算。"
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

# Code Run 使用指南

## 定位 (Positioning)

`code-run` 用于一次性临时脚本执行。它适合多行 `Python` 或 `shell` 代码、快速数据处理、格式转换和临时计算，并在可用时优先走 `bwrap sandbox`。

## 适用场景 (Use When)

- 逻辑天然是多行脚本，而不是单条 command。
- one-liner 已经开始影响可读性。
- 需要临时脚本文件，但不需要把代码沉淀成项目源码。

## 工具与接口 (Tools)

- `run_code`：把输入代码写入 temp file，执行后捕获输出并清理临时文件。
- 当前 runtime 支持的语言通常是 `python` 和 `shell`。

## 标准流程 (Workflow)

1. 先确认任务适合 one-shot script，而不是项目内正式代码改动。
2. 把逻辑写成尽量小的临时脚本，只覆盖当前任务必需的步骤。
3. 优先让脚本直接输出结果或生成最小必要文件。
4. 执行后检查输出和退出状态，再决定是否需要第二轮修正。

## 边界与风险 (Guardrails)

- 不要把 `run_code` 用于长时间运行的 process、daemon 或持续交互任务。
- `bwrap sandbox` 只是优先路径，不是绝对保证；当 `bwrap` 不可用或失败时，runtime 可能 fallback 到普通 shell execution。
- 文件系统访问仍受 writable path 与 blocked path 约束，不要假设脚本拥有完整宿主机权限。
- 这个 backend 面向临时执行，不是写可复用项目代码的默认方式。

## 常见模式 (Playbooks)

### `code-run` vs `exec`

1. 单条命令或短 pipeline 走 `exec_command`。
2. 多行脚本、临时数据处理、一次性计算优先走 `run_code`。
