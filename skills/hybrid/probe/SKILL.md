---
name: "probe"
description: "远程设备 inspection：列出 device、抓取 screenshot、查看 process list。用户要监控或排查远程机器时使用。"
compatibility: "linux"
allowed-tools: "probe_list_devices probe_screenshot probe_process_list probe_list_screenshots"
metadata:
  hypo.category: "hybrid"
  hypo.backend: "probe"
  hypo.exec_profile:
  hypo.triggers: "probe,探针,截图,远程,设备,设备列表,进程列表,屏幕,截屏,监控"
  hypo.risk: "low"
  hypo.dependencies: "probe-server"
---

# Probe 使用指南

## 定位 (Positioning)

`probe` 用于远程设备 inspection，覆盖设备列表、实时 `screenshot`、`process list` 与历史截图查询。

## 适用场景 (Use When)

- 用户要看远程设备当前屏幕状态。
- 用户要检查某台设备的进程或资源占用线索。
- 用户要回看某天的历史截图。

## 工具与接口 (Tools)

- `probe_list_devices`：列出在线设备。
- `probe_screenshot`：获取当前屏幕截图。
- `probe_process_list`：查看当前进程列表。
- `probe_list_screenshots`：列出历史截图记录。

## 标准流程 (Workflow)

1. 默认先用 `probe_list_devices` 确认设备在线状态与 `device_id`。
2. 按用户诉求调用 `probe_screenshot` 或 `probe_process_list`。
3. 如需历史记录，再调用 `probe_list_screenshots`。
4. 输出时总结关键观察点，而不是逐项转储全部细节。

## 参数约定 (Parameters)

- `probe_screenshot.device_id` 必填；设备空闲时可能只返回 idle message。
- `probe_process_list.device_id` 必填，`top_n` 可用于压缩输出。
- `probe_list_screenshots.device_id` 必填，`date` 可选，格式为 `YYYY-MM-DD`。

## 边界与风险 (Guardrails)

- 这是 inspection skill，不要编造 backend 没有暴露的控制操作。
- `screenshot` 和 `process` 数据可能包含敏感信息，摘要时要克制。
- 如果没有设备在线，直接说明，不要猜测设备状态。
