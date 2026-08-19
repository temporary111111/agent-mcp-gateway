"""Typed, environment-driven gateway configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from .errors import ConfigError

DEFAULT_OPENCODE_URL = "http://127.0.0.1:4096"


def _parse_float_env(
    env: Mapping[str, str],
    name: str,
    default: float,
    *,
    minimum: float = 0.0,
) -> float:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(
            f"Environment variable {name} must be a number, got {raw!r}."
        ) from exc
    if value < minimum:
        raise ConfigError(
            f"Environment variable {name} must be >= {minimum}, got {value}."
        )
    return value


def _parse_int_env(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(
            f"Environment variable {name} must be an integer, got {raw!r}."
        ) from exc


def parse_allowed_roots(raw: str | None) -> tuple[Path, ...]:
    """Parse AGENT_ALLOWED_ROOTS into absolute, normalized paths.

    Separator is os.pathsep (``;`` on Windows, ``:`` on POSIX). Empty
    values yield an empty tuple, which means "no directory is allowed".
    """
    if not raw:
        return ()
    roots: list[Path] = []
    for part in raw.split(os.pathsep):
        part = part.strip().strip('"').strip("'")
        if not part:
            continue
        roots.append(Path(os.path.abspath(os.path.expanduser(part))))
    return tuple(roots)


@dataclass(frozen=True)
class Config:
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8000
    public_mcp_host: str = ""
    opencode_url: str = DEFAULT_OPENCODE_URL
    opencode_username: str = ""
    opencode_password: str = ""
    allowed_roots: tuple[Path, ...] = ()
    log_level: str = "INFO"
    opencode_connect_timeout: float = 5.0
    opencode_read_timeout: float = 60.0
    opencode_write_timeout: float = 30.0
    opencode_pool_timeout: float = 5.0
    env: dict[str, str] = field(default_factory=dict, repr=False)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Config":
        source = dict(os.environ if env is None else env)

        mcp_port = _parse_int_env(source, "MCP_PORT", 8000)
        if not 1 <= mcp_port <= 65535:
            raise ConfigError(f"MCP_PORT must be 1..65535, got {mcp_port}.")

        opencode_url = (
            source.get("OPENCODE_URL", DEFAULT_OPENCODE_URL).strip().rstrip("/")
        )
        if not opencode_url.startswith(("http://", "https://")):
            raise ConfigError(
                f"OPENCODE_URL must be an http(s) URL, got {opencode_url!r}."
            )

        log_level = source.get("LOG_LEVEL", "INFO").strip().upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ConfigError(f"Unsupported LOG_LEVEL {log_level!r}.")

        return cls(
            mcp_host=source.get("MCP_HOST", "127.0.0.1").strip() or "127.0.0.1",
            mcp_port=mcp_port,
            public_mcp_host=source.get("PUBLIC_MCP_HOST", "").strip(),
            opencode_url=opencode_url,
            opencode_username=source.get("OPENCODE_USERNAME", "").strip(),
            opencode_password=source.get("OPENCODE_PASSWORD", ""),
            allowed_roots=parse_allowed_roots(
                source.get("AGENT_ALLOWED_ROOTS")
            ),
            log_level=log_level,
            opencode_connect_timeout=_parse_float_env(
                source, "OPENCODE_CONNECT_TIMEOUT", 5.0
            ),
            opencode_read_timeout=_parse_float_env(
                source, "OPENCODE_READ_TIMEOUT", 60.0
            ),
            opencode_write_timeout=_parse_float_env(
                source, "OPENCODE_WRITE_TIMEOUT", 30.0
            ),
            opencode_pool_timeout=_parse_float_env(
                source, "OPENCODE_POOL_TIMEOUT", 5.0
            ),
            env=source,
        )

    def auth_enabled(self) -> bool:
        return bool(self.opencode_password)

    def summary(self) -> dict:
        """Non-secret summary used for logging/startup output."""
        return {
            "mcp_host": self.mcp_host,
            "mcp_port": self.mcp_port,
            "public_mcp_host": self.public_mcp_host or "(none)",
            "opencode_url": self.opencode_url,
            "opencode_auth": self.auth_enabled(),
            "allowed_roots": [str(p) for p in self.allowed_roots],
            "log_level": self.log_level,
        }