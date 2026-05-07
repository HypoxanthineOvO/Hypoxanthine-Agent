# Hypo-Agent C5 Sync、Docs 与 Release 状态

## 服务重启

- 后端：`http://127.0.0.1:8765`
- 前端：`http://127.0.0.1:5178`
- 健康检查：
  - `http://127.0.0.1:8765/api/health` 返回 ok。
  - `http://127.0.0.1:5178` 返回 `200`。
- 说明：`5173` 端口被其他本地 Vite 进程占用，本次继续使用 Hypo-Agent 最近运行端口 `5178`。

## Sync

已执行：

```text
hypo-workflow sync --repair --platform opencode --project /home/heyx/Hypo-Agent
```

结果：

- OpenCode 适配器刷新到 Hypo-Workflow `12.1.0`。
- 新增 `/hw:pr` 与 `/hw:explain` 映射。
- 移除 retired `/hw:dashboard` 映射。
- `.pipeline/derived-health.yaml`：`error_count: 0`。
- 剩余 warning：`.pipeline/reports.compact.md` 缺失，原因是 `source_missing`，不阻塞本次发布。

## Docs

当前本地 `hypo-workflow` CLI 没有 `docs` 子命令，因此按 `hypo-workflow:docs` skill 契约手动检查。

已存在：

- `README.md`
- `docs/architecture/`
- `docs/plans/`
- `docs/reports/`
- `docs/runbooks/`

待后续补齐：

- `docs/user-guide.md`
- `docs/developer.md`
- `docs/platforms/`
- `docs/reference/`
- `LICENSE`

## Release

- 版本：`1.7.0`
- Changelog：已加入 `v1.7.0 - 2026-05-07`
- 发布范围：C5 recoverable failure folding、progress aggregation、transient read retry、default async runtime、OpenCode adapter refresh。
