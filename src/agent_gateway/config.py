"""Typed, environment-driven gateway configuration."""

from __future__ import annotations

import ipaddress
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .errors import ConfigError

DEFAULT_OPENCODE_URL = "http://127.0.0.1:4096"

DEFAULT_MAX_READ_BYTES = 200_000
DEFAULT_MAX_TREE_ENTRIES = 1_000
DEFAULT_MAX_SEARCH_RESULTS = 200
DEFAULT_MAX_PROCESS_OUTPUT_BYTES = 100_000
DEFAULT_PROCESS_TIMEOUT_MAX = 300

CONFIG_DIR = Path.home() / ".agent-gateway"
CONFIG_FILE = CONFIG_DIR / "config.json"


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
    public_exposure: bool = False
    max_read_bytes: int = DEFAULT_MAX_READ_BYTES
    max_tree_entries: int = DEFAULT_MAX_TREE_ENTRIES
    max_search_results: int = DEFAULT_MAX_SEARCH_RESULTS
    max_process_output_bytes: int = DEFAULT_MAX_PROCESS_OUTPUT_BYTES
    process_timeout_max: int = DEFAULT_PROCESS_TIMEOUT_MAX
    env: dict[str, str] = field(default_factory=dict, repr=False)

    @classmethod
    def from_file(cls, path: Path | None = None) -> dict[str, Any]:
        """Load config values from a JSON file. Returns a dict of values
        found in the file (empty dict if file doesn't exist or is invalid).
        """
        path = path or CONFIG_FILE
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise ConfigError(
                f"Failed to read config file {path}: {exc}"
            ) from exc
        if not isinstance(data, dict):
            raise ConfigError(
                f"Config file {path} must contain a JSON object."
            )
        return data

    @classmethod
    def build(
        cls,
        *,
        folder: str | None = None,
        commands: bool | None = None,
        public: bool | None = None,
        port: int | None = None,
        config_file: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> "Config":
        """Build config with priority: CLI args > config file > env vars > defaults.

        CLI args (folder, commands, public, port) override config file values,
        which override environment variables, which override defaults.
        """
        # Layer 1: env vars (backwards compat with .env)
        source = dict(os.environ if env is None else env)

        # Layer 2: config file
        file_values = cls.from_file(config_file)
        for key, value in file_values.items():
            env_key = key.upper()
            if env_key not in source:
                source[env_key] = str(value) if not isinstance(value, bool) else ("true" if value else "false")

        # Layer 3: CLI args override config file and env
        if folder:
            source["AGENT_ALLOWED_ROOTS"] = folder
        if commands is not None:
            source["AGENT_ENABLE_COMMANDS"] = "true" if commands else "false"
        if public is not None:
            source["PUBLIC_EXPOSURE"] = "true" if public else "false"
        if port is not None:
            source["MCP_PORT"] = str(port)

        return cls.from_env(source)

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

        # Parse allowed_roots: support both string (env var) and list (config file)
        raw_roots = source.get("AGENT_ALLOWED_ROOTS", "")
        if raw_roots.startswith("["):
            # JSON array from config file
            try:
                roots_list = json.loads(raw_roots)
                allowed_roots = tuple(
                    Path(os.path.abspath(os.path.expanduser(str(r))))
                    for r in roots_list
                    if r
                )
            except (json.JSONDecodeError, TypeError):
                allowed_roots = parse_allowed_roots(raw_roots)
        else:
            allowed_roots = parse_allowed_roots(raw_roots)

        config = cls(
            mcp_host=source.get("MCP_HOST", "127.0.0.1").strip() or "127.0.0.1",
            mcp_port=mcp_port,
            public_mcp_host=source.get("PUBLIC_MCP_HOST", "").strip(),
            opencode_url=opencode_url,
            opencode_username=source.get("OPENCODE_USERNAME", "").strip(),
            opencode_password=source.get("OPENCODE_PASSWORD", ""),
            allowed_roots=allowed_roots,
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
            public_exposure=_parse_bool_env(
                source, "PUBLIC_EXPOSURE", False
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
        """Validate public exposure settings."""
        # New simplified path: public_exposure flag
        if self.public_exposure:
            return  # No token required, no host checking

        # Legacy path: PUBLIC_MCP_HOST or non-loopback bind
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
            "public_exposure": self.public_exposure,
            "public_mcp_host": self.public_mcp_host or "(none)",
            "opencode_url": self.opencode_url,
            "opencode_auth": self.auth_enabled(),
            "opencode_agent_enabled": self.enable_opencode_agent,
            "commands_enabled": self.enable_commands,
            "token_auth": self.token_auth_enabled(),
            "allowed_roots": [str(p) for p in self.allowed_roots],
            "log_level": self.log_level,
        }