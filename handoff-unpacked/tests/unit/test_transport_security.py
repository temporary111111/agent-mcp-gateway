"""Unit tests for MCP transport security and public host handling."""

from __future__ import annotations

from agent_gateway.server import build_transport_security, normalize_public_host


def test_normalize_public_host_variants() -> None:
    assert normalize_public_host("") == ""
    assert normalize_public_host("  ") == ""
    assert normalize_public_host("example.trycloudflare.com") == (
        "example.trycloudflare.com"
    )
    assert normalize_public_host("https://foo.trycloudflare.com") == (
        "foo.trycloudflare.com"
    )
    assert normalize_public_host("https://foo.trycloudflare.com/some/path") == (
        "foo.trycloudflare.com"
    )


def test_localhost_only_by_default() -> None:
    security = build_transport_security("")
    assert "127.0.0.1" in security.allowed_hosts
    assert "localhost:*" in security.allowed_hosts
    assert "http://127.0.0.1:*" in security.allowed_origins
    assert security.enable_dns_rebinding_protection is True


def test_public_host_added() -> None:
    host = "abc-123.trycloudflare.com"
    security = build_transport_security(host)
    assert host in security.allowed_hosts
    assert f"{host}:*" in security.allowed_hosts
    assert f"https://{host}" in security.allowed_origins
    assert f"https://{host}:*" in security.allowed_origins


def test_dns_rebinding_protection_never_disabled() -> None:
    security = build_transport_security("example.trycloudflare.com")
    assert security.enable_dns_rebinding_protection is True