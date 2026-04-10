---
name: "git-workflow"
description: "安全的 Git workflow：inspect status / diff / branch / history，并在用户明确要求时执行 commit 或 push。"
compatibility: "linux"
allowed-tools: "exec_command"
metadata:
  hypo.category: "pure"
  hypo.backend: "exec"
  hypo.exec_profile: "git"
  hypo.triggers: "git,commit,branch,diff,push,stash,merge,status,repo,历史"
  hypo.risk: "medium"
  hypo.dependencies: "git"
---
# Git Workflow 使用指南

## 定位 (Positioning)

`git-workflow` 是面向现有 repository 的安全 Git workflow，强调先 inspect，再决定是否做 `commit`、`push` 或其他写操作。

## 适用场景 (Use When)

- 用户要查看 `status`、`diff`、`branch`、`history`。
- 用户要在已有仓库里做受控的 `stage`、`commit`、`push`。

## 工具与接口 (Tools)

- 通过 `exec_command` 运行 Git 命令，并受 `exec_profile=git` 约束。

## 标准流程 (Workflow)

1. 先运行 `git status --short` 了解 worktree 状态。
2. 用 `git diff` 或 `git diff --cached` 检查变更内容。
3. 用 `git log --oneline -20` 查看最近历史。
4. 只有在用户明确要求时，才继续 `stage`、`commit` 或 `push`。

## 边界与风险 (Guardrails)

- 禁止使用 `git reset --hard`。
- 禁止使用 `git push --force`。
- 不要覆盖、丢弃或回滚用户未明确授权处理的改动。
- 如果 worktree 已脏且用户请求高风险操作，先解释冲突和影响，再继续。
