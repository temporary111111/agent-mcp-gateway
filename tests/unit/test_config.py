"""Unit tests for configuration parsing and validation."""

from __future__ import annotations

import pytest

from agent_gateway.config import Config, parse_allowed_roots
from agent_gateway.errors import ConfigError


def test_defaults() -> None:
    config = Config.from_env({})
    assert config.mcp_host == "127.0.0.1"
    assert config.mcp_port == 8000
    assert config.opencode_url == "http://127.0.0.1:4096"
    assert config.allowed_roots == ()
    assert config.auth_enabled() is False
    assert config.log_level == "INFO"


def test_custom_values() -> None:
    config = Config.from_env(
        {
            "MCP_HOST": "0.0.0.0",
            "MCP_PORT": "9001",
            "PUBLIC_MCP_HOST": "https://example.trycloudflare.com",
            "AGENT_GATEWAY_TOKEN": "s3cr3t-token",
            "OPENCODE_URL": "http://127.0.0.1:5000/",
            "OPENCODE_USERNAME": "opencode",
            "OPENCODE_PASSWORD": "secret",
            "AGENT_ALLOWED_ROOTS": "C:/a;C:/b",
            "LOG_LEVEL": "DEBUG",
        }
    )
    assert config.mcp_host == "0.0.0.0"
    assert config.mcp_port == 9001
    assert config.public_mcp_host == "https://example.trycloudflare.com"
    assert config.opencode_url == "http://127.0.0.1:5000"
    assert config.auth_enabled() is True
    assert len(config.allowed_roots) == 2
    assert config.log_level == "DEBUG"


def test_invalid_port_rejected() -> None:
    for bad in ("0", "70000", "abc"):
        with pytest.raises(ConfigError):
            Config.from_env({"MCP_PORT": bad})


def test_invalid_opencode_url_rejected() -> None:
    with pytest.raises(ConfigError):
        Config.from_env({"OPENCODE_URL": "not-a-url"})


def test_invalid_log_level_rejected() -> None:
    with pytest.raises(ConfigError):
        Config.from_env({"LOG_LEVEL": "SHOUTY"})


def test_timeout_parsing() -> None:
    config = Config.from_env(
        {
            "OPENCODE_CONNECT_TIMEOUT": "1.5",
            "OPENCODE_READ_TIMEOUT": "30",
            "OPENCODE_WRITE_TIMEOUT": "10",
            "OPENCODE_POOL_TIMEOUT": "2",
        }
    )
    assert config.opencode_connect_timeout == 1.5
    assert config.opencode_read_timeout == 30.0
    assert config.opencode_write_timeout == 10.0
    assert config.opencode_pool_timeout == 2.0


def test_invalid_timeout_rejected() -> None:
    with pytest.raises(ConfigError):
        Config.from_env({"OPENCODE_CONNECT_TIMEOUT": "later"})


def test_allowed_roots_parsing() -> None:
    roots = parse_allowed_roots("C:/a; C:/b ;")
    assert [str(r) for r in roots] == [r"C:\a", r"C:\b"]
    assert parse_allowed_roots("") == ()
    assert parse_allowed_roots(None) == ()


def test_summary_never_contains_password() -> None:
    config = Config.from_env({"OPENCODE_PASSWORD": "hunter2"})
    summary = config.summary()
    assert "hunter2" not in str(summary)
    assert summary["opencode_auth"] is True