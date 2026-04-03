---
name: "coder"
description: "Delegate coding tasks to Hypo-Coder. Use when user requests code changes, feature implementation, bug fixes, or code review in a project managed by Hypo-Coder."
compatibility: "linux"
allowed-tools: "coder_submit_task coder_task_status coder_list_tasks coder_abort_task coder_health"
metadata:
  hypo.category: "hybrid"
  hypo.backend: "coder"
  hypo.exec_profile:
  hypo.triggers: "coder,编码,写代码,修复,实现,代码审查,提交任务,codex,开发任务,代码任务"
  hypo.risk: "medium"
  hypo.dependencies: "hypo-coder-api"
---

# Coder 使用说明

当用户希望把工作委托给 Hypo-Coder，而不是在当前 agent 会话里直接完成时，使用这个 skill。

## 工具选择

- 用 `coder_submit_task` 创建新的 coding task。
- 用 `coder_task_status` 查看某个 task 的进度或最终结果。
- 用 `coder_list_tasks` 查看最近任务，也可以按状态过滤。
- 当用户要求停止运行中或排队中的任务时，用 `coder_abort_task`。
- 在委托前需要确认服务可用性时，用 `coder_health`。

## 委托流程

1. 把用户请求翻译成具体的 task 描述。
2. 调用 `coder_submit_task`。
3. 不要立刻轮询。第一次查状态通常放在约 30 秒后。
4. 后续用 `coder_task_status` 跟进。第一次之后，大约每 60 秒查一次。
5. 任务完成后，向用户总结结果、变更文件和测试状态。

## Task 描述最佳实践

一个好的 `prompt` 应包含：

- 目标 repository 或 working directory
- 要实现的目标或要修复的 bug
- 修改后期望的行为
- 关键 file path、module 或 component
- 测试要求或要运行的命令
- 例如保留无关 worktree 改动之类的约束

避免使用像 “fix the project” 或 “make it better” 这样模糊的 prompt。

## 参数说明

### `coder_submit_task`

- `prompt`：必填 task 描述，要写明确。
- `working_directory`：必填 repository 路径。
- `model`：可选 model override。除非有明确理由，否则保持默认。

### `coder_task_status`

- `task_id`：必填。
- 既可用于进度查询，也可用于获取最终总结。

### `coder_list_tasks`

- `status`：可选过滤条件，例如 `queued`、`running`、`completed` 或 `failed`。

### `coder_abort_task`

- `task_id`：必填。
- 只在用户明确要求停止，或任务显然已经过时时使用。

### `coder_health`

- 无参数。

## 轮询建议

- 第一次检查：提交后约 30 秒。
- 后续检查：约每 60 秒一次。
- 如果任务运行超过 10 分钟，要明确告诉用户它仍在运行，而不是无限静默等待。

## 常见流程

### 提交新的实现任务

1. 确认这个请求适合委托。
2. 组装详细的 `prompt`。
3. 调用 `coder_submit_task`。

### 查询任务进度

1. 用 `task_id` 调用 `coder_task_status`。
2. 如果仍在运行，汇报已运行时长，并以合理频率继续轮询。

### 终止过期任务

1. 确认用户确实要停止任务。
2. 调用 `coder_abort_task`。
