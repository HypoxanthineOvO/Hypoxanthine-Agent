---
name: "host-inspection"
description: "只读 host inspection workflow：查看 CPU、memory、disk、port 与 process 状态。"
compatibility: "linux"
allowed-tools: "exec_command"
metadata:
  hypo.category: "pure"
  hypo.backend: "exec"
  hypo.exec_profile: "host-inspect"
  hypo.triggers: "disk,memory,cpu,load,port,process,host inspection,server health,服务器,磁盘,内存"
  hypo.risk: "low"
  hypo.dependencies: "df,free,ps,ss,uptime"
---
# Host Inspection 使用指南

## 定位 (Positioning)

`host-inspection` 用于快速、只读地检查宿主机健康状态与资源使用情况。

## 适用场景 (Use When)

- 用户要看 `CPU`、`memory`、`disk`、`load`、`port` 或 `process` 状态。
- 需要系统健康概览，但还不到修改服务或配置的阶段。

## 工具与接口 (Tools)

- 通过 `exec_command` 执行只读系统命令。

## 标准流程 (Workflow)

1. 先看 `uptime` 获取 load 概况。
2. 用 `df -h`、`free -h` 检查磁盘和内存。
3. 用 `ps aux --sort=-%mem | head -20` 或类似命令定位资源占用者。
4. 用 `ss -tlnp` 查看监听端口和相关进程。

## 边界与风险 (Guardrails)

- 仅限 read-only inspection。
- 不要杀进程、改配置或重启服务。
- 若问题已经升级到服务层诊断，应切换到 `system-service-ops` 或更专门的 workflow。
