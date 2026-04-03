---
name: "log-inspector"
description: "CLI playbook for recent logs, tool history, session history, and error summaries."
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
# Log Inspector 使用说明

当用户要求检查最近故障或运行历史时，使用这个 skill。

CLI 日志和数据库查询使用 `exec_command`，定位 session 文件使用 `list_directory`，检查具体 session transcript 使用 `read_file`。

推荐流程：

1. 最近 service 日志：

```bash
journalctl -u hypo-agent --since "30 min ago" --no-pager
```

2. 从 SQLite 读取 tool invocation 历史：

```bash
sqlite3 hypo.db "SELECT created_at, skill_name, tool_name, status, error_info FROM tool_invocations ORDER BY id DESC LIMIT 50;"
```

3. 从 `memory/sessions/*.jsonl` 读取 session 历史：

- 用 `list_directory` 定位候选 session 文件。
- 当 session 已知时，用 `read_file` 读取具体 `.jsonl` 文件。

4. 快速聚合：

```bash
journalctl -u hypo-agent --since "6 hours ago" --no-pager | grep -Ei "error|exception|traceback"
sqlite3 hypo.db "SELECT skill_name, tool_name, COUNT(*) FROM tool_invocations WHERE status != 'success' GROUP BY skill_name, tool_name ORDER BY COUNT(*) DESC;"
```

安全规则：

- 只读。
- 优先从较窄的时间窗口开始。
- 应总结模式，而不是倾倒过多原始日志。
