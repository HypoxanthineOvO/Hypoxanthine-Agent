---
name: "hypo-agent-ops"
description: "Hypo-Agent 运维 workflow：默认走 test-mode smoke，并对 service action 保持 guardrails。"
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
# Hypo-Agent Ops 使用指南

## 定位 (Positioning)

`hypo-agent-ops` 面向 Hypo-Agent 自身的运维检查，默认采用 `test mode` 和 guarded service action。

## 适用场景 (Use When)

- 用户要做 agent `smoke`、查看服务状态、检查日志或排查主动消息链路。
- 需要验证本地/测试实例，而不是默认碰生产实例。

## 工具与接口 (Tools)

- 通过 `exec_command` 运行 `smoke`、`systemctl`、`journalctl` 等命令，并受 `exec_profile=hypo-agent` 约束。

## 标准流程 (Workflow)

1. 默认先跑 test-mode smoke：
   `HYPO_TEST_MODE=1 uv run python scripts/agent_cli.py --port 8766 smoke`
2. 再检查 `systemctl status hypo-agent` 和近期 `journalctl`。
3. 只有在解释影响并确认必要后，才考虑 `restart`。

## 边界与风险 (Guardrails)

- 默认端口是 `8766`，不要默认用生产端口 `8765`。
- 非经明确说明，不要把 test smoke 和生产验收混用。
- 保持 read-first、diagnose-first，再考虑 service action。
