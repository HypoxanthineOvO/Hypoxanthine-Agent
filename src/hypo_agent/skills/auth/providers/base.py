from __future__ import annotations

from typing import Protocol

from hypo_agent.skills.auth.types import AuthContext, LoginActionResult, PendingLogin


class LoginProvider(Protocol):
    platform: str

    async def start(self, ctx: AuthContext) -> LoginActionResult: ...
    async def check(self, ctx: AuthContext, pending: PendingLogin) -> LoginActionResult: ...
    async def verify(
        self,
        ctx: AuthContext,
        pending: PendingLogin | None,
        code: str,
    ) -> LoginActionResult: ...
    async def status(self, ctx: AuthContext) -> str: ...
    async def cleanup(self, ctx: AuthContext, pending: PendingLogin) -> None: ...
    def supports_cookie_import(self) -> bool: ...
