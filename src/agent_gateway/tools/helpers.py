"""Shared helpers for MCP tool registration."""

from __future__ import annotations

import functools
from typing import Any, Awaitable, Callable, TypeVar

from ..errors import GatewayError
from ..logging import get_logger

logger = get_logger("tools")

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


def tool_handler(fn: F) -> F:
    """Wrap a tool implementation.

    Converts GatewayError into a short, useful ValueError (which the MCP
    SDK surfaces to the client as a tool error) and logs unexpected
    exceptions with their traceback without leaking them to callers.
    """
    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await fn(*args, **kwargs)
        except GatewayError as exc:
            raise ValueError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - last-resort guard
            logger.exception("Unexpected tool failure: %s", exc)
            raise ValueError(
                f"[internal] Unexpected gateway failure: {type(exc).__name__}."
            ) from exc

    return wrapper  # type: ignore[return-value]