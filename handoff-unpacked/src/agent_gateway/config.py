"""Typed, environment-driven gateway configuration."""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from .errors import ConfigError

DEFAULT_OPENCODE_URL = "http://127.0.0.1:4096"

DEFAULT_MAX_READ_BYTES = 200_000
DEFAULT_MAX_TREE_ENTRIES = 1_000
DEFAULT_MAX_SEARCH_RESULTS = 200
DEFAULT_MAX_PROCESS_OUTPUT_BYTES = 100_000
DEFAULT_PROCESS_TIMEOUT_MAX = 300


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


def _parse_int_env(
    env: Mapping[str, str], name: str, default: int, *, minimum: int = 0
) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(
            f"Environment variable {name} must be an integer, got {raw!r}."
        ) from exc
    if value < minimum:
        raise ConfigError(
            f"Environment variable {name} must be >= {minimum}, got {value}."
        )
    return value


def _parse_bool_env(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(
        f"Environment variable {name} must be a boolean (true/false), "
        f"got {raw!r}."
    )


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


_LOOPBACK_ADDRESSES = {"127.0.0.1", "::1", "localhost", "0.0.0.0", "::"}


def _is_non_loopback_bind(host: str) -> bool:
    """Return True if *host* is not a loopback-only bind address.

    Localhost names (``127.*``, ``::1``, ``localhost``) are safe.
    Anything else (``0.0.0.0``, ``::``, LAN IPs) requires a token.
    """
    host = host.strip().lower()
    if not host:
        return False
    if host in _LOOPBACK_ADDRESSES:
        return False
    try:
        addr = ipaddress.ip_address(host)
        return not addr.is_loopback
    except ValueError:
        pass
    # Hostname that is not localhost — treat as non-loopback
    return host not in ("localhost", "localhost.localdomain")


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
    enable_opencode_agent: bool = False
    enable_commands: bool = False
    gateway_token: str = ""
    insecure_no_token_opt_out: bool = False
    max_read_bytes: int = DEFAULT_MAX_READ_BYTES
    max_tree_entries: int = DEFAULT_MAX_TREE_ENTRIES
    max_search_results: int = DEFAULT_MAX_SEARCH_RESULTS
    max_process_output_bytes: int = DEFAULT_MAX_PROCESS_OUTPUT_BYTES
    process_timeout_max: int = DEFAULT_PROCESS_TIMEOUT_MAX
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

        config = cls(
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
            enable_opencode_agent=_parse_bool_env(
                source, "ENABLE_OPENCODE_AGENT", False
            ),
            enable_commands=_parse_bool_env(
                source, "AGENT_ENABLE_COMMANDS", False
            ),
            gateway_token=source.get("AGENT_GATEWAY_TOKEN", ""),
            insecure_no_token_opt_out=_parse_bool_env(
                source, "AGENT_INSECURE_NO_TOKEN_OPT_OUT", False
            ),
            max_read_bytes=_parse_int_env(
                source, "AGENT_MAX_READ_BYTES", DEFAULT_MAX_READ_BYTES,
                minimum=1,
            ),
            max_tree_entries=_parse_int_env(
                source, "AGENT_MAX_TREE_ENTRIES", DEFAULT_MAX_TREE_ENTRIES,
                minimum=1,
            ),
            max_search_results=_parse_int_env(
                source, "AGENT_MAX_SEARCH_RESULTS", DEFAULT_MAX_SEARCH_RESULTS,
                minimum=1,
            ),
            max_process_output_bytes=_parse_int_env(
                source,
                "AGENT_MAX_PROCESS_OUTPUT_BYTES",
                DEFAULT_MAX_PROCESS_OUTPUT_BYTES,
                minimum=1,
            ),
            process_timeout_max=_parse_int_env(
                source, "AGENT_PROCESS_TIMEOUT_MAX", DEFAULT_PROCESS_TIMEOUT_MAX,
                minimum=1,
            ),
            env=source,
        )
        config.validate_public_exposure()
        return config

    def validate_public_exposure(self) -> None:
        """Fail clearly when public exposure is configured without a token.

        The gateway always registers mutating tools (file_write and
        friends), so exposing it publicly with no bearer token is never
        acceptable by default. AGENT_INSECURE_NO_TOKEN_OPT_OUT exists
        only for explicitly documented local experimentation.
        """
        needs_token = False
        reason = ""
        if self.public_mcp_host:
            needs_token = True
            reason = f"PUBLIC_MCP_HOST is set ({self.public_mcp_host!r})"
        elif _is_non_loopback_bind(self.mcp_host):
            needs_token = True
            reason = f"MCP_HOST is a non-loopback address ({self.mcp_host!r})"
        if not needs_token:
            return
        if self.gateway_token:
            return
        if self.insecure_no_token_opt_out:
            return
        raise ConfigError(
            f"AGENT_GATEWAY_TOKEN is required when {reason}. "
            "Binding to a public or non-loopback interface without a bearer "
            "token is not allowed. For localhost-only development, use the "
            "default MCP_HOST (127.0.0.1). If you explicitly accept the "
            "risk, set AGENT_INSECURE_NO_TOKEN_OPT_OUT=true."
        )

    def auth_enabled(self) -> bool:
        return bool(self.opencode_password)

    def token_auth_enabled(self) -> bool:
        return bool(self.gateway_token)

    def summary(self) -> dict:
        """Non-secret summary used for logging/startup output."""
        return {
            "mcp_host": self.mcp_host,
            "mcp_port": self.mcp_port,
            "public_mcp_host": self.public_mcp_host or "(none)",
            "opencode_url": self.opencode_url,
            "opencode_auth": self.auth_enabled(),
            "opencode_agent_enabled": self.enable_opencode_agent,
            "commands_enabled": self.enable_commands,
            "token_auth": self.token_auth_enabled(),
            "allowed_roots": [str(p) for p in self.allowed_roots],
            "log_level": self.log_level,
        }