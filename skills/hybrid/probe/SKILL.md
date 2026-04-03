---
name: "probe"
description: "Remote device inspection: list connected probe devices, take screenshots, and view process lists. Use when user wants to monitor or inspect a remote machine."
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

# Probe 使用说明

这个 skill 用于检查已连接的 probe 设备。backend 已经负责 screenshot 存储和 attachment 生成，这里的重点是选对工具并安排好调用顺序。

## 工具选择

- 先用 `probe_list_devices` 看哪些设备可用且在线。
- 当用户需要看设备当前屏幕状态时，用 `probe_screenshot`。
- 当用户想查看活跃进程或高 CPU 进程时，用 `probe_process_list`。
- 当用户想回看某台设备某天已有的 screenshot 记录时，用 `probe_list_screenshots`。

## 推荐流程

1. 从 `probe_list_devices` 开始。
2. 确认目标 `device_id`。
3. 按用户诉求调用 `probe_screenshot` 或 `probe_process_list`。
4. 如果用户问历史截图，再用 `probe_list_screenshots`。

## 参数说明

### `probe_list_devices`

- 无参数。
- 在猜测 device ID 之前先用它。

### `probe_screenshot`

- `device_id`：必填。
- 当设备活跃时，会返回 image attachment。
- 如果设备空闲或已息屏，工具可能返回 idle message，而不是图片。

### `probe_process_list`

- `device_id`：必填。
- `top_n`：可选。当用户只想看最主要的几个异常进程时，用较小的数。

### `probe_list_screenshots`

- `device_id`：必填。
- `date`：可选 `YYYY-MM-DD` 字符串。当用户要看特定某天的历史截图时使用。

## 安全与解读

- 这个 skill 仅用于 inspection。不要编造 backend 没有暴露的控制操作。
- screenshot 和 process 数据可能包含敏感信息。总结时要克制，避免过度暴露无关细节。
- 如果没有设备在线，直接说明，不要猜测。

## 常见流程

### 检查当前远程设备

1. `probe_list_devices`
2. `probe_screenshot`
3. 总结屏幕上可见的内容

### 调查资源占用

1. `probe_list_devices`
2. `probe_process_list`
3. 标出最主要的 CPU 或内存消耗者

### 回看最近截图

1. `probe_list_screenshots`
2. 说明请求的设备和日期下有哪些截图
