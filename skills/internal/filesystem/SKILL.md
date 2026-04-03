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
# filesystem/SKILL Guide

Use this internal skill as described by the frontmatter description: Permission-controlled file read/write and directory indexing. The foundation for all document-oriented operations.

## Tools

- Allowed tools: read_file write_file list_directory scan_directory get_directory_index update_directory_description
- Treat these tools as internal runtime contracts rather than user-facing branded workflows.

## Workflow

Use the listed internal primitives carefully, keep arguments explicit, and return normalized results.

## Safety

Preserve backend boundaries, avoid leaking internal payload details, and keep execution scoped to the request.
