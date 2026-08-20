"""Unit tests for bearer-token transport auth."""

from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from agent_gateway.security.auth import (
    MCP_PATH,
    parse_bearer,
    validate_bearer,
    wrap_with_auth,
)


def test_parse_bearer() -> None:
    assert parse_bearer("Bearer abc123") == "abc123"
    assert parse_bearer("bearer abc123") == "abc123"
    assert parse_bearer("Bearer  abc ") == "abc"
    assert parse_bearer(None) is None
    assert parse_bearer("") is None
    assert parse_bearer("Basic abc") is None
    assert parse_bearer("Bearer") is None
    assert parse_bearer("Bearer a b") is None


def test_validate_bearer_constant_time() -> None:
    assert validate_bearer("Bearer tok-1", "tok-1") is True
    assert validate_bearer("Bearer tok-2", "tok-1") is False
    assert validate_bearer(None, "tok-1") is False
    assert validate_bearer("Bearer tok-1", "") is False


def _make_app() -> Starlette:
    from starlette.routing import Route

    async def mcp_ok(request):
        return JSONResponse({"ok": True})

    return Starlette(routes=[Route("/mcp", mcp_ok)])


def test_middleware_rejects_missing_token() -> None:
    wrapped = wrap_with_auth(_make_app(), "s3cr3t")
    client = TestClient(wrapped)
    response = client.get(f"{MCP_PATH}")
    assert response.status_code == 401


def test_middleware_rejects_wrong_token() -> None:
    wrapped = wrap_with_auth(_make_app(), "s3cr3t")
    client = TestClient(wrapped)
    response = client.get(
        f"{MCP_PATH}", headers={"Authorization": "Bearer wrong"}
    )
    assert response.status_code == 401


def test_middleware_accepts_correct_token() -> None:
    wrapped = wrap_with_auth(_make_app(), "s3cr3t")
    client = TestClient(wrapped)
    response = client.get(
        f"{MCP_PATH}", headers={"Authorization": "Bearer s3cr3t"}
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_middleware_bypassed_when_no_token_configured() -> None:
    wrapped = wrap_with_auth(_make_app(), "")
    assert wrapped is not None
    client = TestClient(wrapped)
    response = client.get(f"{MCP_PATH}")
    assert response.status_code == 200


def test_middleware_401_body_never_echoes_token() -> None:
    wrapped = wrap_with_auth(_make_app(), "s3cr3t")
    client = TestClient(wrapped)
    response = client.get(f"{MCP_PATH}")
    body = response.text
    assert "s3cr3t" not in body