---
name: "notion"
description: "Read and write Notion pages and databases. Use when user wants to create, query, update, or search content in their Notion workspace."
compatibility: "linux"
allowed-tools: "notion_get_schema notion_read_page notion_write_page notion_update_page notion_query_db notion_create_entry notion_search"
metadata:
  hypo.category: "hybrid"
  hypo.backend: "notion"
  hypo.exec_profile:
  hypo.triggers: "notion,笔记,页面,数据库,记录,写入,创建页面,查询,工作区,写到notion,读取页面"
  hypo.risk: "medium"
  hypo.dependencies: "notion-client"
---
# notion/SKILL Guide

Use this skill as described by the frontmatter description: Read and write Notion pages and databases. Use when user wants to create, query, update, or search content in their Notion workspace.

## Tools

- Allowed tools: notion_get_schema notion_read_page notion_write_page notion_update_page notion_query_db notion_create_entry notion_search
- Follow the listed tools in scope and summarize results for the user.

## Workflow

Use the hybrid backend intentionally, keep the tool sequence concrete, and explain results clearly.

## Safety

Stay within the exposed backend capability boundary and avoid unnecessary broad queries or writes.
