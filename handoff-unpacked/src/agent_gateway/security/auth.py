"""Bearer-token authentication for the MCP transport.

The MCP SDK 2.x auth support is OAuth-oriented; for a deterministic
bearer-token model we wrap the Streamable HTTP app with a small ASGI
middleware. The token is compared in constant time and is never logged
or echoed in any response.
"""

from __future__ import annotations

import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

MCP_PATH = "/mcp"

_UNAUTHORIZED = JSONResponse(
    {"error": "unauthorized", "detail": "Missing or invalid bearer token."},
    status_code=401,
    headers={"WWW-Authenticate": "Bearer"},
)


def parse_bearer(authorization: str | None) -> str | None:
    """Extract the token from an ``Authorization: Bearer <token>`` header.

    Returns None when the header is absent or malformed.
    """
    if not authorization:
        return None
    scheme, _, rest = authorization.partition(" ")
    if scheme.strip().lower() != "bearer":
        return None
    token = rest.strip()
    if not token or " " in token:
        return None
    return token


def validate_bearer(authorization: str | None, expected_token: str) -> bool:
    """Constant-time comparison of the presented bearer token."""
    if not expected_token:
        return False
    token = parse_bearer(authorization)
    if token is None:
        return False
    return secrets.compare_digest(token, expected_token)


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Require ``Authorization: Bearer <token>`` on every MCP request."""

    def __init__(self, app, token: str) -> None:
        super().__init__(app)
        self._token = token

    async def dispatch(self, request: Request, call_next) -> JSONResponse:
        if request.url.path == MCP_PATH or request.url.path.startswith(
            MCP_PATH + "/"
        ):
            if not validate_bearer(
                request.headers.get("authorization"), self._token
            ):
                return _UNAUTHORIZED
        return await call_next(request)


def wrap_with_auth(app, token: str):
    """Wrap a Starlette/ASGI app with bearer-token auth when a token is set."""
    if not token:
        return app
    return BearerAuthMiddleware(app, token)


__all__ = [
    "BearerAuthMiddleware",
    "MCP_PATH",
    "parse_bearer",
    "validate_bearer",
    "wrap_with_auth",
]