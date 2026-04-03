---
name: "git-workflow"
description: "Safe git inspection and commit workflow for an existing repository."
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
# Git Workflow 使用说明

这个 skill 用于常规 repository 工作。

在这个工作流里，使用 `exec_command` 运行 git 命令。

默认流程：

1. 先用 `git status --short` 了解 worktree 状态。
2. 用 `git diff` 或 `git diff --cached` 检查变更。
3. 用 `git log --oneline -20` 查看最近历史。
4. 只有在用户明确要求时，才继续做 stage、commit 或 push。

安全规则：

- 禁止使用 `git reset --hard`。
- 禁止使用 `git push --force`。
- 不要覆盖或丢弃用户未暂存的改动。
- 如果 worktree 是脏的，而用户又要求高风险操作，先解释冲突再继续。

推荐命令顺序：

```bash
git status --short
git diff
git log --oneline -20
```
