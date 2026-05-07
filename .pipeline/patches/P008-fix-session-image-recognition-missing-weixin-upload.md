# P008: 修复会话中图片识别失败的问题
- 严重级: critical
- 状态: closed
- 发现于: C4/Patch
- 创建时间: 05月07日 12:23
- 修复时间: 05月07日 12:28
- 改动: `src/hypo_agent/skills/fs_skill.py` — `read_file` 在原始路径缺失时会从 `memory/uploads` 和已授权读目录解析到真实附件；`src/hypo_agent/channels/weixin/weixin_channel.py` — 微信入站图片 fallback 文件名加入发送者标识；`src/hypo_agent/channels/weixin/ilink_client.py` — 兼容嵌套更新 payload、缺失 `message_type` 和别名字段，避免图片消息未进入入站队列。
- 测试: ✅ `uv run pytest tests/channels/test_ilink_client.py tests/channels/test_weixin_media.py tests/skills/test_fs_skill.py -q`（47 passed）；✅ `git diff --check` 通过。
- commit: `84af203`
- 关联: (无)
- resolved_by: P008
- related: []
- supersedes: []

## 描述

会话中微信图片识别失败，诊断记录显示 Agent 调用 `filesystem.read_file` 读取 `/tmp/weixin/o9cq808jv68ZmOGLuAh4Yt0rna6g/image.png` 时返回 `File not found`。同一轮还出现微信图片回传上传重试和 QQ 文件 fallback 过大，但直接导致识图失败的是入站图片路径不可恢复。

## 诊断线索

- `skill.invoke.fail`: `File not found: /tmp/weixin/o9cq808jv68ZmOGLuAh4Yt0rna6g/image.png`
- `weixin.adapter.image_retry`: 图片回传上传 CDN 500 后重试
- `qq_bot.file.fallback_to_text`: QQ 文件上传 413 后降级文本
