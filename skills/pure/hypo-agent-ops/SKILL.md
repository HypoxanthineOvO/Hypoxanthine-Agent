---
name: "hypo-agent-ops"
description: "Hypo-Agent operational workflow focused on test-mode smoke and guarded service actions."
compatibility: "linux"
allowed-tools: "exec_command"
metadata:
  hypo.category: "pure"
  hypo.backend: "exec"
  hypo.exec_profile: "hypo-agent"
  hypo.triggers: "hypo-agent,agent smoke,8766,heartbeat issue,agent service,systemctl restart hypo-agent,smoke,smoke test,测试模式"
  hypo.risk: "medium"
  hypo.dependencies: "uv,journalctl,systemctl"
---
# Hypo-Agent Ops 使用说明

这个 skill 用于 Hypo-Agent 运维检查。

在这个工作流里，使用 `exec_command` 运行 smoke、status、log 和受控 service 命令。

默认流程：

1. 先优先使用 test mode：

```bash
HYPO_TEST_MODE=1 uv run python scripts/agent_cli.py --port 8766 smoke
```

2. 检查 service 状态和日志：

```bash
systemctl status hypo-agent
journalctl -u hypo-agent --since "10 min ago"
```

3. 只有在说明影响并确认确有必要后，才进行 restart。

安全规则：

- 默认使用端口 `8766`，不要默认用生产端口 `8765`。
- 除非用户明确要求，否则不要触碰生产验收。
- 保持先诊断、后动作的顺序，并谨慎使用 restart。
