"""Executor abstraction.

An *executor* is a backend that can run delegated agent work on the local
machine. Version 1 implements the OpenCode executor. The interface is kept
deliberately small and pragmatic so future executors (Codex, Claude Code)
can be added without changing the MCP tool surface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ExecutorHealth:
    available: bool
    healthy: bool
    version: str | None = None
    detail: str | None = None


@dataclass
class SessionInfo:
    id: str
    directory: str
    title: str | None = None
    agent: str | None = None
    summary: dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionStatusInfo:
    state: str  # "busy" | "idle" | "retry"
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class MessageInfo:
    id: str
    role: str
    time_created: int | None = None
    agent: str | None = None
    model: str | None = None
    finish: str | None = None
    parts: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class FileDiff:
    file: str | None = None
    status: str | None = None
    additions: int = 0
    deletions: int = 0
    patch: str | None = None


@dataclass
class PermissionRequestInfo:
    id: str
    session_id: str
    permission: str
    patterns: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    tool: dict[str, Any] | None = None


class Executor(ABC):
    """Interface every agent backend must implement."""

    name: str = "abstract"

    @abstractmethod
    async def health(self) -> ExecutorHealth:
        """Check backend reachability and health."""

    @abstractmethod
    async def create_session(
        self,
        directory: Path,
        *,
        title: str | None = None,
        agent: str | None = None,
    ) -> SessionInfo:
        """Create a persistent session bound to *directory*."""

    @abstractmethod
    async def send_prompt(
        self,
        session_id: str,
        task: str,
        *,
        agent: str | None = None,
        directory: Path | None = None,
    ) -> None:
        """Dispatch a prompt asynchronously. Returns without waiting."""

    @abstractmethod
    async def status(self, session_id: str) -> SessionStatusInfo:
        """Return the current session state (busy/idle/retry)."""

    @abstractmethod
    async def session_info(self, session_id: str) -> SessionInfo:
        """Return session metadata."""

    @abstractmethod
    async def messages(
        self,
        session_id: str,
        *,
        limit: int = 50,
        before: str | None = None,
    ) -> list[MessageInfo]:
        """Return session messages."""

    @abstractmethod
    async def diff(
        self,
        session_id: str,
        *,
        message_id: str | None = None,
    ) -> list[FileDiff]:
        """Return file changes produced by a message (or the session)."""

    @abstractmethod
    async def abort(self, session_id: str) -> bool:
        """Abort an in-flight session."""

    @abstractmethod
    async def pending_permissions(self) -> list[PermissionRequestInfo]:
        """List permission requests awaiting an explicit decision."""

    @abstractmethod
    async def reply_permission(
        self,
        request_id: str,
        reply: str,
        *,
        message: str | None = None,
    ) -> bool:
        """Reply to a permission request: once | always | reject."""

    def capabilities(self) -> set[str]:
        """Extra capabilities exposed via executor-specific tools."""
        return set()