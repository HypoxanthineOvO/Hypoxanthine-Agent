# C4 M3 统一工具名展示映射

## 结果

已建立共享工具展示 registry，并让后端事件与前端 fallback 使用同一批高频工具名。

## 改动

- `tool_display.py` 覆盖 M1 高频工具：Notion、文件/目录、命令、Web、图片、提醒、记忆、邮件、Agent/repair。
- `tool_narration.py` 改为复用 registry 的 `display_name`，不再维护第二份 label override。
- `channel_progress.py` 使用 registry 生成最终失败摘要。
- WebUI 使用后端 `display_name` 优先；旧事件走前端 fallback 映射，避免历史事件崩溃。

## 兼容策略

- `config/narration.yaml` 仍可渲染具体 narration 模板；registry 负责默认展示名和状态文案。
- 未知工具显示人类化名称，例如 `mystery_tool` -> `mystery tool`，空工具名 -> `处理当前任务`。

## 验证

- `tests/core/test_tool_display.py` 覆盖高频工具、未知工具 fallback 和 M1 错误分类。
- `tests/core/test_channel_adapter.py` 覆盖事件 enrichment。
- `web/src/composables/__tests__/useChatSocket.spec.ts` 覆盖 WebUI display name 优先与旧事件 fallback。
