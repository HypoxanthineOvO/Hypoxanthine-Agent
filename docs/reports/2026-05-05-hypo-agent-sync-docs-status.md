# Hypo-Agent 重启、Sync 与 Docs 状态

## 重启结果

- 后端已恢复运行：`http://127.0.0.1:8765`
- 前端已恢复运行：`http://127.0.0.1:5178`
- 健康检查：
  - `http://127.0.0.1:8765/api/health` 返回 `200`
  - `http://127.0.0.1:5178` 返回 `200`
- `5173` 端口被 `/home/heyx/Hypoxanthine-Bill/web` 的前端进程占用，因此 Hypo-Agent 前端改用空闲端口 `5178`，未终止其他项目进程。

## Sync 结果

已执行：

```text
hypo-workflow sync
hypo-workflow sync --check-only
hypo-workflow sync --repair
```

修复后 `.pipeline/derived-health.yaml` 状态：

- `error_count: 0`
- `stale_count: 1`
- 已刷新：
  - `.pipeline/PROGRESS.compact.md`
  - `.pipeline/metrics.compact.yaml`
  - `PROJECT-SUMMARY.md`
- 剩余 warning：
  - `.pipeline/reports.compact.md` 缺失
  - 原因：`source_missing`

## Docs 检查结果

当前安装的 `hypo-workflow` CLI 没有 `docs` 子命令，执行 `hypo-workflow docs check` 返回 `Unknown command: docs`。因此本次按本地 `hypo-workflow:docs` 技能契约做了手动检查。

已存在：

- `README.md`
- `docs/`
- `docs/architecture/`
- `docs/plans/`
- `docs/reports/`
- `docs/runbooks/`

缺口：

- `docs/user-guide.md`
- `docs/developer.md`
- `docs/platforms/`
- `docs/reference/`
- `LICENSE`

## 建议

- 短期：保持本次 Sync 结果，剩余 `.pipeline/reports.compact.md` warning 不阻塞当前 Agent 使用。
- 后续：单独开一次 docs repair/generate，把用户指南、开发者指南、平台指南、参考文档与 LICENSE 补齐。
