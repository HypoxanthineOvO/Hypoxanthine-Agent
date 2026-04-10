---
name: "coder"
description: "将 coding task 委托给 Hypo-Coder。用户请求 code change、feature implementation、bug fix 或 code review，且任务适合异步委托时使用。"
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

# Coder 使用指南

## 定位 (Positioning)

`coder` 用于把 coding work 异步委托给 `Hypo-Coder`，适合 code change、feature implementation、bug fix 与 code review 这类不必在当前会话内同步完成的任务。

## 适用场景 (Use When)

- 用户明确希望“提交任务”“委托给 coder”或适合后台执行的开发工作。
- 任务需要较长执行时间、独立工作目录或异步跟踪。

## 工具与接口 (Tools)

- `coder_submit_task`：提交新的 coding task。
- `coder_task_status`：查询任务进度或最终结果。
- `coder_list_tasks`：查看最近任务并按状态过滤。
- `coder_abort_task`：停止运行中或排队中的任务。
- `coder_health`：检查后端服务可用性。

## 标准流程 (Workflow)

1. 先判断任务是否适合委托，而不是当前 agent 直接完成。
2. 把用户需求整理成具体 `prompt`，写清目标、范围、约束与测试要求。
3. 调用 `coder_submit_task`。
4. 不要立刻轮询；第一次查询通常放在约 `30s` 后，之后约每 `60s` 查询一次。
5. 完成后向用户总结结果、变更文件和测试状态。

## 参数约定 (Parameters)

- `coder_submit_task.prompt` 应包含 `working directory`、目标行为、关键文件、测试要求和重要约束。
- `coder_submit_task.working_directory` 必填。
- `coder_submit_task.model` 只有在有明确理由时才 override。
- `coder_task_status.task_id`、`coder_abort_task.task_id` 都必须准确传入。
- `coder_list_tasks.status` 可选，常见值包括 `queued`、`running`、`completed`、`failed`。

## 边界与风险 (Guardrails)

- 避免提交像“fix the project”这类模糊 prompt。
- 如果任务超过 `10 min` 仍在运行，要明确告诉用户当前状态，而不是静默等待。
- `abort` 只在用户明确要求停止，或任务明显过时时使用。
