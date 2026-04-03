---
name: "host-inspection"
description: "Read-only host inspection workflow for system health and resource usage."
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
# Host Inspection 使用说明

这个 skill 用于快速、只读的 host 诊断。

在这个工作流里，所有只读检查命令都通过 `exec_command` 执行。

默认流程：

```bash
uptime
df -h
free -h
ps aux --sort=-%mem | head -20
ss -tlnp
```

安全规则：

- 仅限只读。
- 不要杀进程。
- 不要通过这个 skill 编辑配置或重启服务。
