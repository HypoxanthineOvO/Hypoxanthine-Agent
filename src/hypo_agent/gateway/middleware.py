from __future__ import annotations

from urllib.parse import parse_qs


class WsTokenAuthMiddleware:
    def __init__(self, app, auth_token: str, ws_path: str = "/ws") -> None:
        self.app = app
        self.auth_token = auth_token
        self.ws_path = ws_path

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "websocket" and scope["path"] == self.ws_path:
            query_string = scope.get("query_string", b"").decode("utf-8")
            token = (parse_qs(query_string).get("token") or [""])[0]
            if token != self.auth_token:
                await send({"type": "websocket.close", "code": 4401})
                return
        await self.app(scope, receive, send)
