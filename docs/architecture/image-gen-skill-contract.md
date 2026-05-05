# ImageGenSkill 契约设计

> C2 M0 — 2026-05-04 | 基于 `scripts/audit_image_gen_cli.py` 审计结果

## 1. 技能概览

```python
class ImageGenSkill(BaseSkill):
    name = "image_gen"
    description = "Generate and edit images via GPT-Image-2. Supports text-to-image, image editing, inpainting, and batch generation."
    required_permissions: list[str] = []
```

## 2. 工具定义

### 2.1 generate_image

文字生图工具。对应 `gpt_image_cli generate`。

```json
{
  "type": "function",
  "function": {
    "name": "generate_image",
    "description": "Generate an image from a text description using GPT-Image-2.",
    "parameters": {
      "type": "object",
      "properties": {
        "prompt": {
          "type": "string",
          "description": "Text description of the image to generate."
        },
        "size": {
          "type": "string",
          "enum": ["1024x1024", "1536x1024", "1024x1536", "auto"],
          "default": "1024x1024",
          "description": "Image dimensions."
        },
        "quality": {
          "type": "string",
          "enum": ["low", "medium", "high", "auto"],
          "default": "medium",
          "description": "Image quality level."
        },
        "n": {
          "type": "integer",
          "minimum": 1,
          "maximum": 4,
          "default": 1,
          "description": "Number of images to generate."
        },
        "style": {
          "type": "string",
          "description": "Artistic style (e.g. 'watercolor', 'photorealistic', 'anime')."
        },
        "negative": {
          "type": "string",
          "description": "Elements to exclude from the generated image."
        }
      },
      "required": ["prompt"]
    }
  }
}
```

### 2.2 edit_image

图片编辑工具。对应 `gpt_image_cli edit`。

```json
{
  "type": "function",
  "function": {
    "name": "edit_image",
    "description": "Edit an existing image using GPT-Image-2. Supports style transfer, element addition/removal, and inpainting.",
    "parameters": {
      "type": "object",
      "properties": {
        "image_url": {
          "type": "string",
          "description": "Path or URL of the source image to edit."
        },
        "prompt": {
          "type": "string",
          "description": "Description of the desired edit or transformation."
        },
        "mask_url": {
          "type": "string",
          "description": "Path or URL of a mask image for inpainting (white = edit area)."
        },
        "input_fidelity": {
          "type": "string",
          "enum": ["low", "medium", "high"],
          "default": "medium",
          "description": "How closely to follow the original image."
        },
        "size": {
          "type": "string",
          "enum": ["1024x1024", "1536x1024", "1024x1536", "auto"],
          "default": "auto"
        },
        "quality": {
          "type": "string",
          "enum": ["low", "medium", "high", "auto"],
          "default": "medium"
        },
        "n": {
          "type": "integer",
          "minimum": 1,
          "maximum": 4,
          "default": 1
        }
      },
      "required": ["image_url", "prompt"]
    }
  }
}
```

## 3. SkillOutput 契约

### 成功

```python
SkillOutput(
    status="success",
    result={
        "image_paths": ["/abs/path/to/image1.png"],
        "prompt": "original prompt text",
        "size": "1024x1024",
        "quality": "medium",
        "duration_ms": 4500,
    },
    attachments=[
        Attachment(
            type="image",
            url="/abs/path/to/image1.png",
            filename="image1.png",
            mime_type="image/png",
            size_bytes=123456,
        )
    ],
    metadata={
        "tool": "generate_image",
        "model": "gpt-image-2",
        "cli_subcommand": "generate",
    },
)
```

### 失败（API 错误）

```python
SkillOutput(
    status="error",
    error_info="GPT Image 2 API returned content policy violation: ...",
    result=None,
    attachments=[],
    metadata={
        "tool": "generate_image",
        "error_type": "content_policy",
        "retryable": False,
        "recovery_action": {
            "type": "modify_prompt",
            "reason": "content_policy_violation",
            "message": "Your prompt was rejected by the content filter. Please rephrase your request.",
        },
    },
)
```

### 失败（网络/超时，可重试）

```python
SkillOutput(
    status="error",
    error_info="Connection timed out after 30s",
    result=None,
    attachments=[],
    metadata={
        "tool": "generate_image",
        "error_type": "network_timeout",
        "retryable": True,
        "retry_count": 2,
        "recovery_action": {
            "type": "retry",
            "reason": "network_timeout",
            "message": "Image generation timed out. Retrying...",
        },
    },
)
```

### 部分成功（多图中部分失败）

```python
SkillOutput(
    status="partial",
    result={
        "image_paths": ["/abs/path/to/image1.png"],
        "failed_count": 1,
        "total_requested": 2,
    },
    attachments=[
        Attachment(type="image", url="/abs/path/to/image1.png", ...)
    ],
    metadata={
        "tool": "generate_image",
        "error_type": "partial_generation",
        "recovery_action": {
            "type": "report_partial",
            "reason": "partial_success",
            "message": "Generated 1 of 2 requested images.",
        },
    },
)
```

## 4. 错误分类与 Recovery Action

| 错误类型 | 可重试 | Recovery Action | 说明 |
|----------|--------|-----------------|------|
| `cli_not_found` | ❌ | `setup_required` | 告知用户运行 `gpt_image_cli setup` |
| `cli_not_configured` | ❌ | `setup_required` | env 文件缺失或权限错误 |
| `network_timeout` | ✅ (2次) | `retry` | 指数退避 2s/4s |
| `api_rate_limit` | ✅ (2次) | `retry` | 指数退避 5s/10s |
| `content_policy` | ❌ | `modify_prompt` | 告知用户修改 prompt |
| `invalid_params` | ❌ | `report_error` | 参数错误，返回具体说明 |
| `partial_generation` | ❌ | `report_partial` | 部分成功，告知已完成数量 |
| `image_download_failed` | ❌ | `report_error` | URL 参考图下载失败 |
| `unsupported_format` | ❌ | `report_error` | 图片格式不支持 |

## 5. OperationEvent 扩展

### 新增事件类型

```python
OperationEventType = Literal[
    "resource_candidates",    # 已有
    "recovery_action",        # 已有
    "channel_delivery",       # 已有
    "verify_result",          # 已有
    "image_generation",       # 新增
    "image_delivery",         # 新增
]
```

### image_generation 事件

```python
OperationEvent(
    operation_id="img_gen_20260504_001",
    session_id="session_abc",
    event_type="image_generation",
    status="success",           # "success" | "failed" | "timeout"
    delivery={
        "prompt": "a cat wearing sunglasses",
        "size": "1024x1024",
        "quality": "medium",
        "n": 1,
        "output_paths": ["/abs/path/to/cat.png"],
        "duration_ms": 4500,
    },
    recovery_action=None,
)
```

### image_delivery 事件

```python
OperationEvent(
    operation_id="img_del_20260504_001",
    session_id="session_abc",
    event_type="image_delivery",
    status="success",           # "success" | "failed" | "partial"
    channel="qq",
    delivery={
        "image_path": "/abs/path/to/cat.png",
        "target_channel": "qq",
        "delivery_method": "attachment",
        "duration_ms": 1200,
    },
    recovery_action=None,
)
```

## 6. CLI 命令构建示例

### generate_image → gpt_image_cli generate

```bash
gpt_image_cli generate \
  --model gpt-image-2 \
  --prompt "a cat wearing sunglasses, photorealistic" \
  --size 1024x1024 \
  --quality medium \
  --n 1 \
  --out /abs/output/imagegen/img_20260504_001.png
```

### edit_image → gpt_image_cli edit

```bash
gpt_image_cli edit \
  --model gpt-image-2 \
  --image /abs/path/to/source.png \
  --prompt "add a hat to the cat" \
  --input-fidelity medium \
  --size auto \
  --quality medium \
  --n 1 \
  --out /abs/output/imagegen/edit_20260504_001.png
```

## 7. 与现有架构的集成点

| 组件 | 集成方式 |
|------|----------|
| `BaseSkill` | 继承，实现 `tools` 属性和 `execute()` 方法 |
| `SkillManager` | 在 `_register_enabled_skills()` 中注册 |
| `SkillOutput` | 成功/失败时返回附件和 recovery_action |
| `ChannelCapability` | 检查渠道是否支持 `image` 附件类型 |
| `ActiveRecovery` | 渠道发送失败时的重试/fallback |
| `OperationEvent` | 新增 `image_generation` 和 `image_delivery` 事件 |
| `RichResponse` | 通过 `SkillOutput.attachments` 传递图片 |
| `config/skills.yaml` | `image_gen.enabled: true` |
