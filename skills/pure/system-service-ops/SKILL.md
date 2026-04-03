---
name: "system-service-ops"
description: "Read-first diagnosis workflow for systemd services."
compatibility: "linux"
allowed-tools: "exec_command"
metadata:
  hypo.category: "pure"
  hypo.backend: "exec"
  hypo.exec_profile: "systemd"
  hypo.triggers: "systemctl,service,journalctl,daemon,unit,service status,systemd,服务,服务状态,状态"
  hypo.risk: "medium"
  hypo.dependencies: "systemctl,journalctl"
---
# System Service Ops 使用说明

这个 skill 用于在提出动作建议前，先检查 service。

在这个工作流里，service status 和 journal 检查命令都通过 `exec_command` 执行。

默认流程：

1. 运行 `systemctl status <service>`。
2. 运行 `journalctl -u <service> --since "5 min ago"`。
3. 总结当前状态、最近故障，以及是否可能需要 restart。

安全规则：

- 默认保持只读。
- 不要发出 stop、disable、shutdown、reboot 命令。
- 如果确实需要 restart，先解释影响，再等待用户请求，或切换到明确允许 restart 的专用运维 skill。
