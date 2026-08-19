"""Executor registry: maps executor names to implementations."""

from __future__ import annotations

from ..config import Config
from ..errors import ExecutorUnavailableError
from .base import Executor
from .opencode.executor import OpenCodeExecutor


def build_executors(config: Config) -> dict[str, Executor]:
    """Build the executor registry for the configured gateway."""
    return {
        "opencode": OpenCodeExecutor(config),
    }


def require_executor(
    registry: dict[str, Executor],
    name: str,
) -> Executor:
    executor = registry.get(name)
    if executor is None:
        raise ExecutorUnavailableError(
            f"Unknown executor {name!r}; available: "
            f"{', '.join(sorted(registry)) or '(none)'}."
        )
    return executor