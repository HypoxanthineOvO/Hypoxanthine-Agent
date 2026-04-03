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
# probe/SKILL Guide

Use this skill as described by the frontmatter description: Remote device inspection: list connected probe devices, take screenshots, and view process lists. Use when user wants to monitor or inspect a remote machine.

## Tools

- Allowed tools: probe_list_devices probe_screenshot probe_process_list probe_list_screenshots
- Follow the listed tools in scope and summarize results for the user.

## Workflow

Use the hybrid backend intentionally, keep the tool sequence concrete, and explain results clearly.

## Safety

Stay within the exposed backend capability boundary and avoid unnecessary broad queries or writes.
