---
name: "filesystem"
description: "Permission-controlled file read/write and directory indexing. The foundation for all document-oriented operations."
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

# Filesystem 使用说明

这个 backend 负责文件读取、写入、目录检查，以及为项目导航提供提示的 directory index。

## 工具选择

- `read_file`：按格式感知方式读取单个文件。
- `write_file`：当确实需要写入时，创建或覆盖文本文件。
- `list_directory`：在决定下一步读什么之前，先查看目录树。
- `scan_directory`：根据真实文件系统重建部分 directory index。
- `get_directory_index`：读取当前保存的 index。
- `update_directory_description`：编辑某个已索引目录项的人类描述。

## 权限模型

- 所有文件系统操作都经过 permission manager。
- 读写权限取决于配置的 whitelist 规则和 blocked path。
- 在工具真正返回 permission error 之前，不要预先声称没有权限。
- 即使目录可读，也不代表写入一定被允许。

## Directory Index 说明

- directory index 是对已扫描目录及其描述的 YAML 摘要。
- `scan_directory` 会刷新真实树结构，并在可能时保留人工描述。
- `update_directory_description` 用于在不重新扫描全部内容的情况下改进这层 metadata。
- 在大型 workspace 中批量读文件之前，先利用 index 建立方向感。

## 支持格式

- `.md`、`.py`、`.yaml`、`.json`、`.log` 等纯文本类格式，以及类似文本文件。
- 通过 PyMuPDF 提取的 `.pdf`。
- 通过 `python-docx` 读取的 `.docx`。
- 通过 `python-pptx` 读取的 `.pptx`。
- 常见图片格式的基础 metadata。

## 安全规则

- 在修改任何东西之前，优先使用 `read_file` 和 `list_directory`。
- 只有在用户明确要求编辑时才使用 `write_file`。
- 把 directory index 当成辅助记忆，而不是高于真实文件系统的事实来源。
