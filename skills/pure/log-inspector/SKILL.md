---
name: "log-inspector"
description: "日志与历史诊断 workflow：检查 recent logs、tool history、session transcript 与 error summary。"
compatibility: "linux"
allowed-tools: "exec_command, read_file, list_directory"
metadata:
  hypo.category: "pure"
  hypo.backend: "exec"
  hypo.exec_profile: "log-inspect"
  hypo.triggers: "日志,错误日志,journalctl,recent logs,tool history,session history,error summary"
  hypo.risk: "low"
  hypo.dependencies: "journalctl,sqlite3,jq,grep"
---
# Log Inspector 使用指南

## 定位 (Positioning)

`log-inspector` 是 recent logs、tool history、session transcript 与 error summary 的统一诊断 workflow。

## 适用场景 (Use When)

- 用户要检查最近故障、运行历史或工具调用失败模式。
- 需要联合看 `journalctl`、SQLite 记录与 `memory/sessions/*.jsonl`。

## 工具与接口 (Tools)

- `exec_command`：运行日志与数据库查询命令。
- `list_directory`：定位候选 session 文件。
- `read_file`：读取具体 `jsonl transcript`。

## 标准流程 (Workflow)

1. 从较窄时间窗口开始查看 service 日志，例如最近 `30 min`。
2. 查询 SQLite 中的 tool invocation 历史，定位高频失败项。
3. 如需复盘具体对话，再定位并读取对应 `session transcript`。
4. 最终输出模式总结，而不是简单堆原始日志。

## 边界与风险 (Guardrails)

- 保持只读。
- 优先缩小时间窗口，减少噪音。
- 输出应以错误模式、重复故障和上下文关联为主，不要无差别转储大量日志。
