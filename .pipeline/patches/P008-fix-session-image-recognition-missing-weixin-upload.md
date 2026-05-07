# P008: 修复会话中图片识别失败的问题
- 严重级: critical
- 状态: open
- 发现于: C4/Patch
- 创建时间: 05月07日 12:23
- 改动: (待填写)
- 测试: (待填写)
- 关联: (无)
- resolved_by: null
- related: []
- supersedes: []

## 描述

会话中微信图片识别失败，诊断记录显示 Agent 调用 `filesystem.read_file` 读取 `/tmp/weixin/o9cq808jv68ZmOGLuAh4Yt0rna6g/image.png` 时返回 `File not found`。同一轮还出现微信图片回传上传重试和 QQ 文件 fallback 过大，但直接导致识图失败的是入站图片路径不可恢复。

## 诊断线索

- `skill.invoke.fail`: `File not found: /tmp/weixin/o9cq808jv68ZmOGLuAh4Yt0rna6g/image.png`
- `weixin.adapter.image_retry`: 图片回传上传 CDN 500 后重试
- `qq_bot.file.fallback_to_text`: QQ 文件上传 413 后降级文本
