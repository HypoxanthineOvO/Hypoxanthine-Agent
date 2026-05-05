# C4 M5 QQBot 图文入站附件修复

## 结果

QQBot 入站图文现在会转换为 `Message.text + Message.attachments`，不再静默丢图。

## 改动

- `QQBotInboundEvent` 增加 `attachment_descriptors`。
- `QQBotChannelService._parse_inbound_event()` 会解析 `d.attachments`、`d.image`、`d.media`、`d.file_image`。
- http/https 附件会尝试下载并保存到现有 `memory/uploads` 路径；下载失败时保留原 URL 并写入 metadata。
- 无 URL 的附件不会丢失诊断，写入 `metadata.qq.unresolved_attachments` 和 `attachments_lost`。
- `handle_event()` 构造 `Message` 时填充 `attachments`，并记录 `attachments_count`。

## 支持的 payload 形态

- `attachments: [{url, filename, content_type/mime_type, size/size_bytes}]`
- `image` / `media` / `file_image` 为对象或 URL 字符串

## 验证

- `tests/gateway/test_qqbot_channel.py` 覆盖 C2C 文字+图片、纯图片、下载失败、无法解析附件 metadata、纯文字回归。
- `tests/gateway/test_qqbot_ws_channel.py` 覆盖 WebSocket 图文事件分发到 Pipeline。
- 集成标记测试命令：`uv run pytest tests/gateway/test_qqbot_channel.py tests/gateway/test_qqbot_ws_channel.py -q -m integration`：26 passed。
