"""Consistent gateway error taxonomy.

Every failure that leaves the gateway is a :class:`GatewayError` with a
machine-readable code, a human-readable message, and an optional detail.
MCP tools convert these into short, useful messages for the supervising
model instead of dumping tracebacks.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class GatewayErrorCode(str, Enum):
    CONFIG_ERROR = "config_error"
    EXECUTOR_UNAVAILABLE = "executor_unavailable"
    UNAUTHORIZED_DIRECTORY = "unauthorized_directory"
    INVALID_SESSION = "invalid_session"
    INVALID_REQUEST = "invalid_request"
    TIMEOUT = "timeout"
    BACKEND_HTTP_ERROR = "backend_http_error"
    SESSION_BUSY = "session_busy"
    MALFORMED_RESPONSE = "malformed_response"
    PERMISSION_ERROR = "permission_error"
    INTERNAL = "internal"


class GatewayError(Exception):
    """Base class for all gateway errors."""

    code: GatewayErrorCode = GatewayErrorCode.INTERNAL

    def __init__(
        self,
        message: str,
        *,
        detail: Any = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail
        self.status_code = status_code

    def __str__(self) -> str:
        text = f"[{self.code.value}] {self.message}"
        if self.detail:
            text += f" ({self.detail})"
        return text


class ConfigError(GatewayError):
    code = GatewayErrorCode.CONFIG_ERROR


class ExecutorUnavailableError(GatewayError):
    code = GatewayErrorCode.EXECUTOR_UNAVAILABLE


class UnauthorizedDirectoryError(GatewayError):
    code = GatewayErrorCode.UNAUTHORIZED_DIRECTORY


class InvalidSessionError(GatewayError):
    code = GatewayErrorCode.INVALID_SESSION


class InvalidRequestError(GatewayError):
    code = GatewayErrorCode.INVALID_REQUEST


class GatewayTimeoutError(GatewayError):
    code = GatewayErrorCode.TIMEOUT


class BackendHTTPError(GatewayError):
    code = GatewayErrorCode.BACKEND_HTTP_ERROR


class SessionBusyError(GatewayError):
    code = GatewayErrorCode.SESSION_BUSY


class MalformedResponseError(GatewayError):
    code = GatewayErrorCode.MALFORMED_RESPONSE


class PermissionGatewayError(GatewayError):
    code = GatewayErrorCode.PERMISSION_ERROR


class InternalGatewayError(GatewayError):
    code = GatewayErrorCode.INTERNAL