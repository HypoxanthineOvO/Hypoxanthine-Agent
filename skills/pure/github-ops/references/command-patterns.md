# GitHub Ops Command Patterns

这份 reference 收录 `gh` CLI 的常用只读命令模板，优先面向 `PR`、`issue`、`Actions` 与 repository metadata。

## 基本约定

- 默认优先 `--json`
- 默认加 `--limit`，避免输出过长
- 默认保持 read-only

## 查看 Pull Requests

```bash
gh pr list --limit 10 --json number,title,state,author,updatedAt,url
```

适用：
- 查看 open PR
- 快速做 PR overview

查看单个 PR：

```bash
gh pr view <number> --json number,title,body,state,author,commits,reviewRequests,reviews,url
```

## 查看 Issues

```bash
gh issue list --limit 10 --json number,title,state,author,updatedAt,url
```

查看单个 issue：

```bash
gh issue view <number> --json number,title,body,state,author,assignees,labels,updatedAt,url
```

## 查看 Actions Runs

```bash
gh run list --limit 10 --json databaseId,displayTitle,status,conclusion,workflowName,headBranch,createdAt,url
```

查看某个 run：

```bash
gh run view <run-id> --json databaseId,status,conclusion,workflowName,jobs,url
```

## 查看仓库信息

```bash
gh repo view --json name,description,defaultBranchRef,isPrivate,url
```

适用：
- 确认当前目录关联的 GitHub 仓库
- 获取默认分支和仓库基本信息

## 推荐最小流程

查看当前仓库 open PR：

```bash
gh repo view --json name,url,defaultBranchRef
gh pr list --limit 10 --json number,title,state,author,updatedAt,url
```

查看最近 workflow runs：

```bash
gh run list --limit 10 --json databaseId,displayTitle,status,conclusion,workflowName,headBranch,createdAt,url
```
