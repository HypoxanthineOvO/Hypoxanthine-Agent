from __future__ import annotations

import uvicorn

from hypo_agent.gateway.app import create_app
from hypo_agent.gateway.settings import load_gateway_settings


def run(host: str = "0.0.0.0", port: int = 8000) -> None:
    settings = load_gateway_settings()
    app = create_app(auth_token=settings.auth_token, security=settings.security)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    run()
