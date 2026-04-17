from __future__ import annotations

from datetime import datetime

from hypo_agent.utils.timeutil import now_local

RUNTIME_MODEL_CONTEXT_HEADING = "## 当前运行环境"


def build_runtime_model_context(
    *,
    model_display_name: str,
    model_id: str | None,
    task_type: str | None,
    primary_model_display_name: str | None = None,
    current_time: datetime | None = None,
) -> str:
    now = current_time or now_local()
    lines = [RUNTIME_MODEL_CONTEXT_HEADING]
    lines.append(
        f"- 当前模型: {model_display_name} ({str(model_id or model_display_name).strip() or model_display_name})"
    )
    lines.append(f"- 路由类型: {str(task_type or 'chat').strip() or 'chat'}")
    lines.append(f"- 服务器时间: {now.isoformat(timespec='seconds')}")
    primary_name = str(primary_model_display_name or "").strip()
    if primary_name and primary_name != model_display_name:
        lines.append(
            f"- ⚠️ 注意: 本次回复使用了备用模型（主模型 {primary_name} 暂时不可用）"
        )
    return "\n".join(lines)

