"""Error mapping for the OpenCode HTTP backend."""

from __future__ import annotations

import httpx
import pydantic

from ...errors import (
    BackendHTTPError,
    ExecutorUnavailableError,
    GatewayError,
    GatewayTimeoutError,
    InvalidSessionError,
    MalformedResponseError,
    PermissionGatewayError,
)

_BODY_SNIPPET_LIMIT = 300


def _body_snippet(response: httpx.Response) -> str:
    try:
        text = response.text
    except Exception:
        return ""
    text = " ".join(text.split())
    if len(text) > _BODY_SNIPPET_LIMIT:
        text = text[:_BODY_SNIPPET_LIMIT] + "..."
    return text


def map_transport_error(exc: Exception, context: str) -> GatewayError:
    """Map httpx transport-level failures into gateway errors."""
    if isinstance(exc, httpx.ConnectError):
        return ExecutorUnavailableError(
            f"OpenCode backend is unreachable: {context}.",
            detail=str(exc),
        )
    if isinstance(exc, (httpx.TimeoutException,)):
        return GatewayTimeoutError(
            f"OpenCode backend timed out: {context}.",
            detail=str(exc),
        )
    if isinstance(exc, GatewayError):
        return exc
    return BackendHTTPError(
        f"OpenCode backend request failed: {context}.",
        detail=str(exc),
    )


def map_status_error(exc: httpx.HTTPStatusError, context: str) -> GatewayError:
    status = exc.response.status_code
    snippet = _body_snippet(exc.response)

    if status in (401, 403):
        return PermissionGatewayError(
            f"OpenCode backend rejected credentials ({status}): {context}.",
            detail=snippet or None,
            status_code=status,
        )
    if status == 404:
        return InvalidSessionError(
            f"OpenCode session not found: {context}.",
            detail=snippet or None,
            status_code=status,
        )
    return BackendHTTPError(
        f"OpenCode backend returned HTTP {status} for {context}.",
        detail=snippet or None,
        status_code=status,
    )


def map_validation_error(exc: pydantic.ValidationError, context: str) -> GatewayError:
    return MalformedResponseError(
        f"OpenCode backend returned an unexpected payload: {context}.",
        detail=str(exc.errors()[:3]),
    )