from __future__ import annotations

import logging

import structlog

from hypo_agent.core.config_loader import is_test_mode
from hypo_agent.core.recent_logs import install_recent_log_handler


def configure_logging(level: str = "INFO", json_logs: bool = True) -> None:
    """Configure structlog for application-wide structured logging."""
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=False)
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer = (
        structlog.processors.JSONRenderer()
        if json_logs
        else structlog.dev.ConsoleRenderer()
    )

    logging.basicConfig(
        format="%(message)s",
        level=getattr(logging, level.upper(), logging.INFO),
    )
    install_recent_log_handler()

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *shared_processors,
            renderer,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    structlog.contextvars.clear_contextvars()
    if is_test_mode():
        structlog.contextvars.bind_contextvars(mode="test")
