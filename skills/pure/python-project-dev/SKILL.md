---
name: "python-project-dev"
description: "Targeted Python project workflow for dependency sync, tests, and lint."
compatibility: "linux"
allowed-tools: "exec_command"
metadata:
  hypo.category: "pure"
  hypo.backend: "exec"
  hypo.exec_profile: "python-dev"
  hypo.triggers: "pytest,ruff,mypy,uv,pip,python project,test failure,lint,测试结果"
  hypo.risk: "low"
  hypo.dependencies: "uv,pytest,ruff,mypy,pip"
---
# Python Project Dev 使用说明

这个 skill 用于常规 Python 开发循环。

在这个工作流里，依赖、测试和 lint 命令都通过 `exec_command` 执行。

默认流程：

1. 当环境可能过旧时，用 `uv sync` 同步依赖。
2. 用 `uv run pytest <target> -x` 跑最小相关测试目标。
3. 当 lint 反馈重要时，运行 `ruff check <target>`。

安全规则：

- 优先选择能验证改动的最小测试范围。
- 除非确有必要，否则不要默认跑全量测试。
- 保持命令可复现，并限制在 repository 本地上下文中。
