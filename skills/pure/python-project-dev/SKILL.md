---
name: "python-project-dev"
description: "Python project workflow：dependency sync、targeted tests 与 lint。"
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
# Python Project Dev 使用指南

## 定位 (Positioning)

`python-project-dev` 面向常规 Python 开发循环，覆盖 dependency sync、targeted tests 与 lint。

## 适用场景 (Use When)

- 用户在 Python repository 中做开发、修复或验证。
- 需要运行 `uv`、`pytest`、`ruff`、`mypy` 等常见工程命令。

## 工具与接口 (Tools)

- 通过 `exec_command` 运行 Python 工程命令，并受 `exec_profile=python-dev` 约束。

## 标准流程 (Workflow)

1. 当环境可能过旧或缺依赖时，先运行 `uv sync`。
2. 优先执行与改动最相关的最小测试范围，例如 `uv run pytest <target> -x`。
3. 需要静态检查时，再运行 `ruff check <target>` 或其他必要 lint/type-check。

## 边界与风险 (Guardrails)

- 默认选择最小可验证范围，不要无理由跑全量测试。
- 所有命令都应保持可复现，并限制在当前 repository 上下文。
- 若需要高成本全量验证，应明确说明理由和预期耗时。
