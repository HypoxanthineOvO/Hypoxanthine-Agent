# M7a Runbook: Chat View + UI Foundation

## Scope

M7a 交付以下能力：

1. Chat 渲染体系拆分（Text/Code/ToolCall/Compressed/Media/FileAttachment）
2. 统一 Markdown 渲染器（GFM、KaTeX、Mermaid、增强代码块）
3. 压缩原文取回（`/api/compressed/{cache_id}` + `compressed_meta`）
4. 安全文件读取 API（`/api/files?path=...`）
5. Naive UI 基础壳层 + 侧栏 + 暗色模式 + 快捷键
6. WebSocket 自动重连与统一错误事件
7. `RichResponse` / `ChannelAdapter` 内部抽象与 `tool_invocations` 落库

## Runtime Contracts

### REST

- `GET /api/compressed/{cache_id}`
  - `200`: 返回缓存命中的原始内容
  - `404`: 未命中缓存
- `GET /api/files?path=...`
  - 需要 Token 鉴权（`Authorization: Bearer <token>` 或 `?token=`）
  - 需要 `PermissionManager` 的 `read` 白名单许可

### WebSocket

保留既有事件：

- `assistant_chunk`
- `assistant_done`
- `tool_call_start`
- `tool_call_result`

新增/扩展：

- `tool_call_result.compressed_meta`（仅压缩时存在）
- 统一错误事件：
  - `{"type":"error","code","message","retryable","session_id"}`

## Frontend UX Defaults

- 输入框：
  - `Enter` 换行
  - `Ctrl/Cmd+Enter` 发送
  - 自动扩展到 `200px` 后滚动
- 快捷键：
  - `Esc`: 关闭展开输入框 / 折叠侧边栏
  - `Ctrl/Cmd+L`: 清空当前对话
  - `Ctrl/Cmd+N`: 新建对话
  - `Ctrl/Cmd+D`: 切换暗色/亮色
  - `Ctrl/Cmd+K`: no-op（命令面板预留）
- WS 自动重连：
  - backoff: `1s -> 2s -> 4s -> 8s -> 16s -> 30s(cap)`

## Observability

新增日志事件（`模块.动作.结果`）：

- `compressed_api.fetch.hit`
- `compressed_api.fetch.miss`
- `compressed_api.fetch.unavailable`
- `files_api.serve.hit`
- `files_api.serve.denied`
- `files_api.serve.not_found`
- `files_api.serve.unavailable`
- `ws.error.failed`
- `gateway_auth.verify.denied`

## Deployment Notes

- Nginx 需同时代理：
  - `/ws` -> `127.0.0.1:8000/ws`
  - `/api` -> `127.0.0.1:8000/api`
- 前端依赖新增 `naive-ui`、`@traptitech/markdown-it-katex`、`katex`、`mermaid`。

## Verification Commands

```bash
pytest -q
cd web && npm run test && npm run build
```

