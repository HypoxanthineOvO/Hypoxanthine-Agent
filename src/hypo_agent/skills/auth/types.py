from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal

from hypo_agent.models import Attachment


def _to_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@dataclass
class AuthContext:
    session_id: str
    secrets_path: Path
    qr_dir: Path
    structured_store: Any | None
    subscription_manager: Any | None
    http_transport: Any | None
    now_fn: Callable[[], datetime]
    sleep_func: Callable[[float], Awaitable[None]]
    build_qr_attachment: Callable[[str, str], Awaitable[Attachment | None]]
    get_cookie: Callable[[str], str]
    build_wewe_client: Callable[[], Any]
    close_client: Callable[[Any], Awaitable[None]]
    auth_check_poll_attempts: int
    auth_check_poll_interval_seconds: float
    playwright_runtime: Any | None = None


@dataclass
class PendingLogin:
    platform: str
    provider_key: str
    backend_key: str
    session_id: str
    created_at: str
    expires_at: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = {
            "platform": self.platform,
            "provider_key": self.provider_key,
            "backend_key": self.backend_key,
            "session_id": self.session_id,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "payload": dict(self.payload),
        }
        for key, value in self.payload.items():
            data.setdefault(key, value)
        return data

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "PendingLogin":
        payload = data.get("payload")
        if not isinstance(payload, dict):
            payload = {
                key: value
                for key, value in data.items()
                if key
                not in {
                    "platform",
                    "provider_key",
                    "backend_key",
                    "session_id",
                    "created_at",
                    "expires_at",
                    "payload",
                }
            }
        return cls(
            platform=str(data.get("platform") or data.get("provider_key") or ""),
            provider_key=str(data.get("provider_key") or data.get("platform") or ""),
            backend_key=str(data.get("backend_key") or ""),
            session_id=str(data.get("session_id") or "main"),
            created_at=str(data.get("created_at") or ""),
            expires_at=str(data.get("expires_at") or "") or None,
            payload=dict(payload),
        )

    def is_expired(self, *, now: datetime) -> bool:
        if not self.expires_at:
            return False
        try:
            expires_at = _to_utc(datetime.fromisoformat(self.expires_at.replace("Z", "+00:00")))
        except ValueError:
            return False
        current = _to_utc(now)
        if expires_at is None or current is None:
            return False
        return current >= expires_at


@dataclass
class LoginActionResult:
    text: str
    attachments: list[Attachment] = field(default_factory=list)
    status: Literal["pending", "success", "expired", "timeout", "need_verify", "error"] = "pending"
    cookie: str | None = None
    pending: PendingLogin | None = None
    clear_pending: bool = False


@dataclass
class BrowserLoginSession:
    context_id: str
    platform: str
    created_at: str


@dataclass
class PlaywrightPlatformConfig:
    platform: str
    login_url: str
    entry_actions: list[dict[str, Any]] = field(default_factory=list)
    qr_targets: list[dict[str, Any]] = field(default_factory=list)
    success_cookies: list[str] = field(default_factory=list)
    cookie_domains: list[str] = field(default_factory=list)
    risk_texts: list[str] = field(default_factory=list)
    qr_wait_seconds: int = 30
    login_wait_seconds: int = 60
