from __future__ import annotations

import uvicorn

from hypo_agent.core.config_loader import get_port
from hypo_agent.core.logging import configure_logging
from hypo_agent.gateway.app import create_app
from hypo_agent.gateway.settings import load_gateway_settings


def build_app():
    configure_logging()
    settings = load_gateway_settings()
    return create_app(
        auth_token=settings.auth_token,
        security=settings.security,
        channels=settings.channels,
    )


def run(host: str = "0.0.0.0", port: int | None = None) -> None:
    app = build_app()
    resolved_port = int(port) if port is not None else get_port()
    uvicorn.run(app, host=host, port=resolved_port, log_level="info")


if __name__ == "__main__":
    run()
