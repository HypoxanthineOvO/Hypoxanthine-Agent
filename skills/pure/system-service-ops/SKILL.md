---
name: "system-service-ops"
description: "systemd service 诊断 workflow：read-first 地查看 status、journal 与最近故障。"
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
# System Service Ops 使用指南

## 定位 (Positioning)

`system-service-ops` 是 `systemd service` 的 read-first 诊断 workflow，用于在提出动作建议前先核实状态和日志。

## 适用场景 (Use When)

- 用户要查看某个 service 的当前状态、最近报错或是否需要 restart。
- 问题处于服务层，而不是整机资源层。

## 工具与接口 (Tools)

- 通过 `exec_command` 运行 `systemctl` 和 `journalctl`，并受 `exec_profile=systemd` 约束。

## 标准流程 (Workflow)

1. 运行 `systemctl status <service>` 查看当前状态。
2. 运行 `journalctl -u <service> --since "5 min ago"` 或合适时间窗查看近期日志。
3. 总结状态、最近故障和后续动作建议。

## 边界与风险 (Guardrails)

- 默认保持只读。
- 不要直接执行 `stop`、`disable`、`shutdown`、`reboot`。
- 如果判断可能需要 `restart`，先解释影响，再等待用户明确请求，或切换到明确允许重启动作的专用 skill。
