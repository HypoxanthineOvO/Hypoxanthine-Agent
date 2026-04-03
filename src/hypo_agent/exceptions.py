from __future__ import annotations

from typing import Any


class HypoAgentError(Exception):
    """所有 Hypo-Agent 域异常的基类。"""

    def __init__(
        self,
        message: str = "",
        *,
        operation: str | None = None,
        **context: Any,
    ) -> None:
        super().__init__(message)
        self.message = str(message or "").strip()
        self.operation = str(operation or "").strip() or None
        self.context = {key: value for key, value in context.items() if value not in (None, "", [], {}, ())}

    def __str__(self) -> str:
        parts: list[str] = []
        if self.operation:
            parts.append(self.operation)
        if self.message:
            parts.append(self.message)
        if self.context:
            context_text = ", ".join(f"{key}={value!r}" for key, value in sorted(self.context.items()))
            parts.append(context_text)
        return " | ".join(parts) if parts else self.__class__.__name__


class ConfigError(HypoAgentError):
    """配置缺失 / 格式错误 / 环境变量无效。"""


class ModelError(HypoAgentError):
    """LLM 调用失败 / 超时 / 模型不可用 / 响应解析错误。"""


class ChannelError(HypoAgentError):
    """渠道连接 / 发送 / 认证失败（QQ / 微信 / 飞书 / Email / WebUI）。"""


class SkillError(HypoAgentError):
    """Skill 执行 / 权限 / 超时。"""


class StorageError(HypoAgentError):
    """SQLite / L3 结构化存储 / 文件系统。"""


class ExternalServiceError(HypoAgentError):
    """Tavily / TrendRadar / Notion API / iLink / 外部 HTTP 服务。"""
