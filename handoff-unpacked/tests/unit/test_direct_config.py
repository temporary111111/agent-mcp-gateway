"""Unit tests for V2 direct-mode configuration."""

from __future__ import annotations

import pytest

from agent_gateway.config import Config
from agent_gateway.errors import ConfigError
from agent_gateway.logging import redact_env


def test_direct_mode_defaults() -> None:
    config = Config.from_env({})
    assert config.enable_opencode_agent is False
    assert config.enable_commands is False
    assert config.gateway_token == ""
    assert config.token_auth_enabled() is False
    assert config.max_read_bytes == 200_000
    assert config.max_tree_entries == 1_000
    assert config.max_search_results == 200
    assert config.max_process_output_bytes == 100_000
    assert config.process_timeout_max == 300


def test_boolean_flags_parsed() -> None:
    config = Config.from_env(
        {
            "ENABLE_OPENCODE_AGENT": "true",
            "AGENT_ENABLE_COMMANDS": "1",
            "AGENT_INSECURE_NO_TOKEN_OPT_OUT": "yes",
        }
    )
    assert config.enable_opencode_agent is True
    assert config.enable_commands is True
    assert config.insecure_no_token_opt_out is True


def test_invalid_boolean_rejected() -> None:
    with pytest.raises(ConfigError):
        Config.from_env({"AGENT_ENABLE_COMMANDS": "maybe"})


def test_limit_parsing() -> None:
    config = Config.from_env(
        {
            "AGENT_MAX_READ_BYTES": "1024",
            "AGENT_MAX_TREE_ENTRIES": "50",
            "AGENT_MAX_SEARCH_RESULTS": "10",
            "AGENT_MAX_PROCESS_OUTPUT_BYTES": "2048",
            "AGENT_PROCESS_TIMEOUT_MAX": "60",
        }
    )
    assert config.max_read_bytes == 1024
    assert config.max_tree_entries == 50
    assert config.max_search_results == 10
    assert config.max_process_output_bytes == 2048
    assert config.process_timeout_max == 60


def test_invalid_limit_rejected() -> None:
    with pytest.raises(ConfigError):
        Config.from_env({"AGENT_MAX_READ_BYTES": "0"})
    with pytest.raises(ConfigError):
        Config.from_env({"AGENT_PROCESS_TIMEOUT_MAX": "-5"})


def test_public_exposure_requires_token() -> None:
    with pytest.raises(ConfigError):
        Config.from_env({"PUBLIC_MCP_HOST": "abc.trycloudflare.com"})
    config = Config.from_env(
        {
            "PUBLIC_MCP_HOST": "abc.trycloudflare.com",
            "AGENT_GATEWAY_TOKEN": "tok",
        }
    )
    assert config.token_auth_enabled() is True


def test_public_exposure_opt_out_documented() -> None:
    config = Config.from_env(
        {
            "PUBLIC_MCP_HOST": "abc.trycloudflare.com",
            "AGENT_INSECURE_NO_TOKEN_OPT_OUT": "true",
        }
    )
    assert config.token_auth_enabled() is False


def test_localhost_needs_no_token() -> None:
    config = Config.from_env({"AGENT_ALLOWED_ROOTS": "C:/x"})
    assert config.token_auth_enabled() is False


def test_token_never_in_summary() -> None:
    config = Config.from_env(
        {
            "AGENT_GATEWAY_TOKEN": "super-secret-token-xyz",
            "PUBLIC_MCP_HOST": "abc.trycloudflare.com",
        }
    )
    assert "super-secret-token-xyz" not in str(config.summary())
    assert config.summary()["token_auth"] is True


def test_token_redacted_in_env_mapping() -> None:
    redacted = redact_env({"AGENT_GATEWAY_TOKEN": "super-secret-token-xyz"})
    assert "super-secret-token-xyz" not in str(redacted)
    assert "su***yz" in redacted["AGENT_GATEWAY_TOKEN"]