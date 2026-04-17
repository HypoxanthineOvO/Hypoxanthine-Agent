from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import structlog
import yaml

from hypo_agent.channels.info.wewe_rss_client import WeWeRSSClient
from hypo_agent.core.config_loader import get_memory_dir, load_secrets_config
from hypo_agent.models import Attachment, SkillOutput
from hypo_agent.skills.auth.errors import AuthFlowError, QrCodeExpiredError
from hypo_agent.skills.auth.playwright_runtime import PlaywrightRuntime
from hypo_agent.skills.auth.registry import build_auth_registry
from hypo_agent.skills.auth.types import AuthContext, LoginActionResult, PendingLogin
from hypo_agent.skills.base import BaseSkill

logger = structlog.get_logger("hypo_agent.skills.auth")

_COOKIE_PLATFORMS = {"bilibili", "weibo", "zhihu", "weread"}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _normalize_platform(platform: str) -> str:
    normalized = str(platform or "").strip().lower()
    if normalized in {"b站", "bilibili", "bili"}:
        return "bilibili"
    if normalized in {"微博", "weibo"}:
        return "weibo"
    if normalized in {"知乎", "zhihu", "zhihu_pins"}:
        return "zhihu"
    if normalized in {"wewe", "wewe_rss", "wewe-rss"}:
        return "wewe_rss"
    if normalized in {"微信读书", "weread", "wechat_read", "wechat-read"}:
        return "weread"
    return normalized


def _parse_cookie_string(raw_cookie: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for chunk in str(raw_cookie or "").split(";"):
        item = chunk.strip()
        if not item or "=" not in item:
            continue
        key, value = item.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key:
            cookies[key] = value
    return cookies


class AuthSkill(BaseSkill):
    name = "auth"
    description = "平台登录管理（扫码/验证码）、Cookie 状态检查与失效清理。"
    keyword_hints = [
        "登录",
        "cookie",
        "扫码",
        "验证码",
        "过期",
        "失效",
        "重新登录",
        "auth",
        "wewe rss",
        "b站登录",
        "微博登录",
        "知乎登录",
        "微信读书登录",
    ]
    required_permissions: list[str] = []

    def __init__(
        self,
        *,
        structured_store: Any | None = None,
        secrets_path: Path | str = "config/secrets.yaml",
        qr_dir: Path | str | None = None,
        http_transport: Any | None = None,
        wewe_client_factory: Any | None = None,
        default_session_id: str = "main",
        subscription_manager: Any | None = None,
        now_fn: Any | None = None,
        auth_check_poll_attempts: int = 4,
        auth_check_poll_interval_seconds: float = 2.0,
        sleep_func: Any | None = None,
        playwright_runtime: Any | None = None,
    ) -> None:
        self.structured_store = structured_store
        self.secrets_path = Path(secrets_path)
        self.qr_dir = Path(qr_dir) if qr_dir is not None else get_memory_dir() / "auth"
        self.http_transport = http_transport
        self.wewe_client_factory = wewe_client_factory or self._default_wewe_client_factory
        self.default_session_id = str(default_session_id or "main")
        self.subscription_manager = subscription_manager
        self.now_fn = now_fn or _utc_now
        self.auth_check_poll_attempts = max(1, int(auth_check_poll_attempts))
        self.auth_check_poll_interval_seconds = max(0.0, float(auth_check_poll_interval_seconds))
        self.sleep_func = sleep_func or asyncio.sleep
        self.playwright_runtime = playwright_runtime or PlaywrightRuntime()
        self._pending_auth: dict[str, dict[str, Any]] = {}
        self._registry = build_auth_registry(http_transport=self.http_transport)

    @property
    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "auth_login",
                    "description": "发起平台登录流程（生成二维码或登录链接）。仅在用户请求登录时调用，不要在用户说'扫了/登录好了'时调用。",
                    "parameters": {
                        "type": "object",
                        "properties": {"platform": {"type": "string"}},
                        "required": ["platform"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "auth_check",
                    "description": "检查登录状态。在用户说'扫了/扫码完成/登录好了/搞定了'时调用此工具，不要调用 auth_login。",
                    "parameters": {
                        "type": "object",
                        "properties": {"platform": {"type": "string"}},
                        "required": ["platform"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "auth_verify",
                    "description": "提交平台验证码或在验证码场景下获取下一步登录指引。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "platform": {"type": "string"},
                            "code": {"type": "string"},
                        },
                        "required": ["platform", "code"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "auth_set_cookie",
                    "description": "用户手动提供 Cookie 后，保存到 secrets.yaml 并刷新订阅运行时。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "platform": {"type": "string"},
                            "cookie": {"type": "string"},
                        },
                        "required": ["platform", "cookie"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "auth_status",
                    "description": "查看各平台 Cookie 或登录配置的健康状态。",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "auth_revoke",
                    "description": "清除指定平台已保存的 Cookie。",
                    "parameters": {
                        "type": "object",
                        "properties": {"platform": {"type": "string"}},
                        "required": ["platform"],
                    },
                },
            },
        ]

    async def execute(self, tool_name: str, params: dict[str, Any]) -> SkillOutput:
        try:
            if tool_name == "auth_login":
                output = await self.auth_login(
                    str(params.get("platform") or ""),
                    session_id=str(params.get("__session_id") or self.default_session_id),
                )
                return self._to_skill_output(output)
            if tool_name == "auth_check":
                output = await self.auth_check(
                    str(params.get("platform") or ""),
                    session_id=str(params.get("__session_id") or self.default_session_id),
                )
                return self._to_skill_output(output)
            if tool_name == "auth_verify":
                output = await self.auth_verify(
                    str(params.get("platform") or ""),
                    str(params.get("code") or ""),
                )
                return self._to_skill_output(output)
            if tool_name == "auth_set_cookie":
                return SkillOutput(
                    status="success",
                    result=await self.auth_set_cookie(
                        str(params.get("platform") or ""),
                        str(params.get("cookie") or ""),
                    ),
                )
            if tool_name == "auth_status":
                return SkillOutput(status="success", result=await self.auth_status())
            if tool_name == "auth_revoke":
                return SkillOutput(
                    status="success",
                    result=await self.auth_revoke(str(params.get("platform") or "")),
                )
        except (AuthFlowError, OSError, RuntimeError, TypeError, ValueError, httpx.HTTPError) as exc:
            return SkillOutput(status="error", error_info=str(exc))
        return SkillOutput(status="error", error_info=f"Unsupported tool '{tool_name}'")

    async def auth_login(self, platform: str, *, session_id: str) -> LoginActionResult:
        normalized = _normalize_platform(platform)
        provider = self._get_provider(normalized)
        ctx = self._build_context(session_id=session_id)
        await self._cleanup_pending_if_needed(provider=provider, ctx=ctx, platform=normalized)
        output = await provider.start(ctx)
        await self._apply_action_result(platform=normalized, provider=provider, output=output, ctx=ctx)
        return output

    async def auth_check(self, platform: str, *, session_id: str) -> LoginActionResult:
        normalized = _normalize_platform(platform)
        provider = self._get_provider(normalized)
        ctx = self._build_context(session_id=session_id)
        pending_raw = await self._load_pending(normalized)
        if not pending_raw:
            return LoginActionResult(text=f"当前没有待处理的 {self._platform_display_name(normalized)} 登录，请先调用 auth_login。")
        pending = PendingLogin.from_mapping(pending_raw)
        if pending.is_expired(now=self.now_fn()):
            await provider.cleanup(ctx, pending)
            await self._clear_pending(normalized)
            return LoginActionResult(text="二维码已过期，请重新调用 auth_login。", status="expired")
        try:
            output = await provider.check(ctx, pending)
        except QrCodeExpiredError as exc:
            await provider.cleanup(ctx, pending)
            await self._clear_pending(normalized)
            return LoginActionResult(text=str(exc), status="expired")
        await self._apply_action_result(platform=normalized, provider=provider, output=output, ctx=ctx, old_pending=pending)
        return output

    async def auth_verify(self, platform: str, code: str) -> LoginActionResult:
        normalized = _normalize_platform(platform)
        provider = self._get_provider(normalized)
        cleaned_code = str(code or "").strip()
        if not cleaned_code:
            return LoginActionResult(text="验证码不能为空。", status="error")
        pending_raw = await self._load_pending(normalized)
        pending = PendingLogin.from_mapping(pending_raw) if pending_raw else None
        ctx = self._build_context(session_id=str((pending or {}).session_id if pending is not None else self.default_session_id))
        output = await provider.verify(ctx, pending, cleaned_code)
        await self._apply_action_result(platform=normalized, provider=provider, output=output, ctx=ctx, old_pending=pending)
        return output

    async def auth_set_cookie(self, platform: str, cookie: str) -> str:
        normalized = _normalize_platform(platform)
        provider = self._get_provider(normalized)
        cleaned_cookie = str(cookie or "").strip()
        if not provider.supports_cookie_import():
            raise ValueError(f"{self._platform_display_name(normalized)} 当前不支持手动导入 Cookie。")
        if not cleaned_cookie:
            raise ValueError("cookie is required")
        parsed_cookie = _parse_cookie_string(cleaned_cookie)
        if not parsed_cookie:
            raise ValueError("cookie format is invalid")
        pending_raw = await self._load_pending(normalized)
        if pending_raw:
            await provider.cleanup(self._build_context(session_id=self.default_session_id), PendingLogin.from_mapping(pending_raw))
        await self._update_cookie(normalized, cleaned_cookie)
        await self._clear_pending(normalized)
        await self._refresh_subscription_runtime()
        return f"✅ {self._platform_display_name(normalized)} Cookie 已更新！"

    async def auth_status(self) -> str:
        lines = ["🔐 平台 Cookie 状态："]
        ctx = self._build_context(session_id=self.default_session_id)
        for platform, provider in self._registry.items():
            lines.append(await provider.status(ctx))
        return "\n".join(lines)

    async def auth_revoke(self, platform: str) -> str:
        normalized = _normalize_platform(platform)
        provider = self._get_provider(normalized)
        pending_raw = await self._load_pending(normalized)
        if pending_raw:
            await provider.cleanup(self._build_context(session_id=self.default_session_id), PendingLogin.from_mapping(pending_raw))
        if normalized in _COOKIE_PLATFORMS:
            await self._clear_cookie(normalized)
        await self._clear_pending(normalized)
        await self._refresh_subscription_runtime()
        if normalized == "wewe_rss":
            return "WeWe RSS 使用服务端账号授权，当前不支持通过 auth_revoke 清除。"
        return f"已清除 {self._platform_display_name(normalized)} 的 Cookie。"

    def _to_skill_output(self, output: LoginActionResult) -> SkillOutput:
        status = "success" if output.status in {"pending", "success", "timeout", "expired"} else "error"
        return SkillOutput(status=status, result=output.text, attachments=output.attachments)

    def _get_provider(self, platform: str) -> Any:
        provider = self._registry.get(platform)
        if provider is None:
            raise ValueError(self._unknown_platform_message(platform))
        return provider

    def _build_context(self, *, session_id: str) -> AuthContext:
        return AuthContext(
            session_id=session_id,
            secrets_path=self.secrets_path,
            qr_dir=self.qr_dir,
            structured_store=self.structured_store,
            subscription_manager=self.subscription_manager,
            http_transport=self.http_transport,
            now_fn=self.now_fn,
            sleep_func=self.sleep_func,
            build_qr_attachment=self._build_qr_attachment,
            get_cookie=self._get_cookie,
            build_wewe_client=self._build_wewe_client,
            close_client=self._close_client,
            auth_check_poll_attempts=self.auth_check_poll_attempts,
            auth_check_poll_interval_seconds=self.auth_check_poll_interval_seconds,
            playwright_runtime=self.playwright_runtime,
        )

    async def _apply_action_result(
        self,
        *,
        platform: str,
        provider: Any,
        output: LoginActionResult,
        ctx: AuthContext,
        old_pending: PendingLogin | None = None,
    ) -> None:
        if old_pending is not None and output.pending is not None:
            old_context = str(old_pending.payload.get("context_id") or "")
            new_context = str(output.pending.payload.get("context_id") or "")
            if old_context != new_context or old_pending.backend_key != output.pending.backend_key:
                await provider.cleanup(ctx, old_pending)
        if output.cookie:
            await self._update_cookie(platform, output.cookie)
            if old_pending is not None:
                await provider.cleanup(ctx, old_pending)
            await self._clear_pending(platform)
            await self._refresh_subscription_runtime()
            return
        if output.clear_pending:
            if old_pending is not None:
                await provider.cleanup(ctx, old_pending)
            await self._clear_pending(platform)
            return
        if output.pending is not None:
            await self._save_pending(platform, output.pending.to_dict())

    async def _cleanup_pending_if_needed(self, *, provider: Any, ctx: AuthContext, platform: str) -> None:
        pending_raw = await self._load_pending(platform)
        if not pending_raw:
            return
        pending = PendingLogin.from_mapping(pending_raw)
        await provider.cleanup(ctx, pending)
        await self._clear_pending(platform)

    async def _update_cookie(self, platform: str, cookie_str: str) -> None:
        normalized = _normalize_platform(platform)
        if normalized not in _COOKIE_PLATFORMS:
            raise ValueError(f"unsupported cookie platform: {platform}")
        payload = self._read_secrets_payload()
        services = payload.setdefault("services", {})
        if not isinstance(services, dict):
            raise ValueError("config/secrets.yaml services must be an object")
        service = services.setdefault(normalized, {})
        if not isinstance(service, dict):
            raise ValueError(f"config/secrets.yaml services.{normalized} must be an object")
        service["cookie"] = str(cookie_str or "").strip()
        self._write_secrets_payload(payload)
        logger.info("auth.cookie_updated", platform=normalized)

    async def _clear_cookie(self, platform: str) -> None:
        normalized = _normalize_platform(platform)
        payload = self._read_secrets_payload()
        services = payload.get("services")
        if isinstance(services, dict):
            service = services.get(normalized)
            if isinstance(service, dict):
                service.pop("cookie", None)
        self._write_secrets_payload(payload)
        logger.info("auth.cookie_cleared", platform=normalized)

    def _read_secrets_payload(self) -> dict[str, Any]:
        if not self.secrets_path.exists():
            return {}
        payload = yaml.safe_load(self.secrets_path.read_text(encoding="utf-8")) or {}
        if not isinstance(payload, dict):
            raise ValueError("config/secrets.yaml 必须是对象")
        return payload

    def _write_secrets_payload(self, payload: dict[str, Any]) -> None:
        self.secrets_path.parent.mkdir(parents=True, exist_ok=True)
        self.secrets_path.write_text(
            yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

    def _get_cookie(self, platform: str) -> str:
        payload = self._read_secrets_payload()
        services = payload.get("services")
        if not isinstance(services, dict):
            return ""
        service = services.get(_normalize_platform(platform))
        if not isinstance(service, dict):
            return ""
        return str(service.get("cookie") or "").strip()

    async def _save_pending(self, platform: str, payload: dict[str, Any]) -> None:
        normalized = _normalize_platform(platform)
        self._pending_auth[normalized] = dict(payload)
        if self.structured_store is not None:
            await self._ensure_store_ready()
            await self.structured_store.set_preference(
                self._pending_preference_key(normalized),
                json.dumps(payload, ensure_ascii=False),
            )

    async def _load_pending(self, platform: str) -> dict[str, Any] | None:
        normalized = _normalize_platform(platform)
        pending = self._pending_auth.get(normalized)
        if pending is not None:
            return dict(pending)
        if self.structured_store is None:
            return None
        await self._ensure_store_ready()
        raw = await self.structured_store.get_preference(self._pending_preference_key(normalized))
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, dict):
            return None
        self._pending_auth[normalized] = dict(parsed)
        return dict(parsed)

    async def _clear_pending(self, platform: str) -> None:
        normalized = _normalize_platform(platform)
        self._pending_auth.pop(normalized, None)
        if self.structured_store is not None:
            await self._ensure_store_ready()
            await self.structured_store.delete_preference(self._pending_preference_key(normalized))

    async def _ensure_store_ready(self) -> None:
        if self.structured_store is not None and hasattr(self.structured_store, "init"):
            await self.structured_store.init()

    def _pending_preference_key(self, platform: str) -> str:
        return f"auth.pending.{platform}"

    def _load_wewe_config(self) -> Any | None:
        try:
            secrets = load_secrets_config(self.secrets_path)
        except (FileNotFoundError, OSError, ValueError):
            return None
        services = getattr(secrets, "services", None)
        return getattr(services, "wewe_rss", None) if services is not None else None

    def _build_wewe_client(self) -> Any:
        config = self._load_wewe_config()
        if config is None:
            raise ValueError("WeWe RSS 未配置，请先检查 config/secrets.yaml")
        if not str(getattr(config, "base_url", "") or "").strip():
            raise ValueError("WeWe RSS base_url 未配置")
        if not str(getattr(config, "auth_code", "") or "").strip():
            raise ValueError("WeWe RSS auth_code 未配置")
        return self.wewe_client_factory(config)

    async def _close_client(self, client: Any) -> None:
        close = getattr(client, "close", None)
        if callable(close):
            result = close()
            if hasattr(result, "__await__"):
                await result

    def _default_wewe_client_factory(self, config: Any) -> WeWeRSSClient:
        return WeWeRSSClient(
            getattr(config, "base_url"),
            getattr(config, "auth_code"),
        )

    async def _build_qr_attachment(self, prefix: str, content: str) -> Attachment | None:
        try:
            import qrcode
        except ModuleNotFoundError:
            return None
        self.qr_dir.mkdir(parents=True, exist_ok=True)
        timestamp = int(self.now_fn().timestamp())
        path = self.qr_dir / f"{prefix}_qr_{timestamp}.png"
        image = qrcode.make(content)
        image.save(path)
        return Attachment(
            type="image",
            url=str(path.resolve(strict=False)),
            filename=path.name,
            mime_type="image/png",
            size_bytes=path.stat().st_size,
        )

    async def _refresh_subscription_runtime(self) -> None:
        manager = self.subscription_manager
        if manager is None:
            return
        build_fetchers = getattr(manager, "_build_default_fetchers", None)
        build_resolver = getattr(manager, "_build_target_resolver", None)
        if callable(build_fetchers):
            manager.fetchers = build_fetchers()
        if callable(build_resolver):
            manager.target_resolver = build_resolver()

    def _platform_display_name(self, platform: str) -> str:
        normalized = _normalize_platform(platform)
        if normalized == "bilibili":
            return "B站"
        if normalized == "weibo":
            return "微博"
        if normalized == "zhihu":
            return "知乎"
        if normalized == "wewe_rss":
            return "WeWe RSS"
        if normalized == "weread":
            return "微信读书"
        return platform

    def _unknown_platform_message(self, platform: str) -> str:
        requested = str(platform or "").strip() or "unknown"
        return (
            f"未识别平台 {requested}。"
            "当前可处理的平台：bilibili、wewe_rss、weibo、zhihu、weread。"
        )
