---
name: "github-ops"
description: "GitHub CLI workflow：查看 pull request、issue、Actions run 与远程仓库状态。用户提到 GitHub、PR、issue、Actions、workflow run 或 gh CLI 时使用。"
compatibility: "linux"
allowed-tools: "exec_command"
metadata:
  hypo.category: "pure"
  hypo.backend: "exec"
  hypo.exec_profile: "git"
  hypo.triggers: "github,GitHub,gh,pr,pull request,issue,issues,actions,workflow run,run status,仓库pr,远程仓库"
  hypo.risk: "medium"
  hypo.dependencies: "gh"
---
# GitHub Ops 使用指南

## 定位 (Positioning)

`github-ops` 面向 GitHub 远程对象与平台状态，覆盖 `pull request`、`issue`、`Actions run` 与 repository metadata 查询。

## 与 Git Workflow 的边界

- 本地 repository 工作树、`git status`、`diff`、`commit`、`push`，优先使用 `git-workflow`。
- GitHub 远程对象、PR/issue/Actions、`gh` CLI 操作，使用 `github-ops`。

## 适用场景 (Use When)

- 用户要看 open PR、某个 issue、workflow run 状态或远程仓库信息。
- 用户明确提到 `gh`、GitHub PR、issue、Actions。

## 工具与接口 (Tools)

- 通过 `exec_command` 调用 `gh` CLI，并受扩展后的 `exec_profile=git` 约束。
- 默认优先使用 `--json` 与 `--limit`，减少纯文本解析和超长输出。
- 常用命令模板见 `references/command-patterns.md`。

## 标准流程 (Workflow)

1. 先判断用户要查的是 PR、issue、Actions run 还是 repo metadata。
2. 优先使用结构化命令，例如 `gh pr list --json ...`、`gh issue list --json ...`、`gh run list --json ...`。
3. 如果用户没有给 repo 上下文，先确认当前目录对应的 remote，或让 `gh` 使用默认仓库上下文。
4. 输出时提炼状态、标题、作者、更新时间和关键链接，不要原样倾倒整段 JSON。

## 参数约定 (Parameters)

- PR 列表：
  `gh pr list --limit 10 --json number,title,state,author,updatedAt,url`
- 单个 PR：
  `gh pr view <number> --json number,title,body,state,author,commits,reviewRequests,reviews,url`
- issue 列表：
  `gh issue list --limit 10 --json number,title,state,author,updatedAt,url`
- 单个 issue：
  `gh issue view <number> --json number,title,body,state,author,assignees,labels,updatedAt,url`
- Actions runs：
  `gh run list --limit 10 --json databaseId,displayTitle,status,conclusion,workflowName,headBranch,createdAt,url`
- repo 信息：
  `gh repo view --json name,description,defaultBranchRef,isPrivate,url`

## 边界与风险 (Guardrails)

- 默认保持 read-first。未经用户明确要求，不要执行 merge、close、reopen、edit、review submit 等写操作。
- 优先 `--json` 输出；除非命令不支持，否则不要依赖脆弱的纯文本格式。
- 当未登录或 repo 上下文不明确时，要明确说明阻塞点，而不是猜测结果。
- 如果用户要做高风险 GitHub 写操作，先解释影响，再等待明确确认。
