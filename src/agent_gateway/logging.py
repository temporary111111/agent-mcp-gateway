"""Observability: logging setup with secret redaction guarantees."""

from __future__ import annotations

import logging
import sys

SENSITIVE_ENV_KEYS = {
    "OPENCODE_PASSWORD",
    "OPENCODE_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "OPENCODE_TOKEN",
    "AGENT_GATEWAY_TOKEN",
}

_ROOT_LOGGER = "agent_gateway"


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    root = logging.getLogger(_ROOT_LOGGER)
    root.setLevel(level.upper())
    root.handlers.clear()
    root.addHandler(handler)
    root.propagate = False


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{_ROOT_LOGGER}.{name}")


def redact(value: str) -> str:
    """Mask a sensitive value for logging."""
    if not value:
        return ""
    if len(value) <= 4:
        return "***"
    return value[:2] + "***" + value[-2:]


def redact_env(mapping: dict[str, str]) -> dict[str, str]:
    """Return an env mapping safe to log (sensitive values masked)."""
    return {
        key: (redact(value) if key in SENSITIVE_ENV_KEYS else value)
        for key, value in mapping.items()
    }