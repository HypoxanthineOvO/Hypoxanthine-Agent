---
name: "filesystem"
description: "受权限控制的文件读写与目录索引 backend。适合 document-oriented file read/write、目录扫描与 workspace 导航。"
compatibility: "linux"
allowed-tools: "read_file write_file list_directory scan_directory get_directory_index update_directory_description"
metadata:
  hypo.category: "internal"
  hypo.backend: "filesystem"
  hypo.exec_profile:
  hypo.triggers: ""
  hypo.risk: "medium"
  hypo.dependencies: "fitz,python-docx,python-pptx"
---

# Filesystem 使用指南

## 定位 (Positioning)

`filesystem` 是 document-oriented 操作的基础 backend，负责文件读写、目录遍历与 `directory index` 维护。

## 适用场景 (Use When)

- 需要读取具体文件内容或写入文本文件。
- 需要先看目录结构，再决定下一步检查哪些文件。
- 需要在大型 workspace 中借助 `directory index` 建立导航视角。

## 工具与接口 (Tools)

- `read_file`：按文件类型读取单个文件内容。
- `write_file`：创建或覆盖文本文件。
- `list_directory`：查看目录树与子项。
- `scan_directory`：基于真实文件系统刷新 `directory index`。
- `get_directory_index`：读取当前保存的索引摘要。
- `update_directory_description`：更新目录项的人类说明文字。

## 标准流程 (Workflow)

1. 在未知目录下，先用 `list_directory` 建立路径感。
2. 需要批量理解 workspace 时，优先读取或刷新 `directory index`。
3. 锁定目标后，用 `read_file` 读取具体文件。
4. 只有在用户明确要求编辑时，才使用 `write_file`。
5. 如果目录结构或说明过期，再用 `scan_directory` 和 `update_directory_description` 修正索引。

## 边界与风险 (Guardrails)

- 所有文件操作都受 `permission manager` 控制；不要在工具返回前预判一定有权或无权。
- 目录可读不代表文件可写，写入前要保持这个边界意识。
- `directory index` 是辅助导航层，不是高于真实文件系统的事实来源。
- 在改动任何内容前，优先做 read-first 检查。

## 常见模式 (Playbooks)

### 支持格式

- 纯文本类：`.md`、`.py`、`.yaml`、`.json`、`.log` 等。
- 文档类：`.pdf`、`.docx`、`.pptx`。
- 图片类：常见 image format 的基础 metadata。
