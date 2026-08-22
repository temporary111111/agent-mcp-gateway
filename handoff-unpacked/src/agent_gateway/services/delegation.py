"""Delegation orchestration (optional OpenCode agent mode).

Sits between the MCP tools and the executor abstraction. Owns the
executor registry and the directory path policy, and exposes the generic
agent lifecycle used by the MCP tools:

    start -> status -> messages -> diff -> continue | abort

Authorization: every operation on an existing session re-verifies that
the session's real directory is still inside AGENT_ALLOWED_ROOTS. The
in-memory registry is only a cache; after a gateway restart the backend
session metadata is fetched and re-authorized before any access.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from ..errors import (
    GatewayError,
    InvalidRequestError,
    PermissionGatewayError,
    UnauthorizedDirectoryError,
)
from ..executors import require_executor
from ..executors.base import Executor
from ..logging import get_logger
from ..security.paths import PathPolicy

logger = get_logger("delegation")

DEFAULT_EXECUTOR = "opencode"

_PERMISSION_REPLIES = ("once", "always", "reject")


@dataclass
class DelegatedSession:
    executor: str
    session_id: str
    directory: str
    title: str | None = None
    agent: str | None = None
    origin_message_id: str | None = None


class DelegationService:
    def __init__(
        self,
        executors: Mapping[str, Executor],
        path_policy: PathPolicy,
        *,
        opencode_enabled: bool = False,
        commands_enabled: bool = False,
    ) -> None:
        self._executors = dict(executors)
        self._path_policy = path_policy
        self._opencode_enabled = opencode_enabled
        self._commands_enabled = commands_enabled
        self._sessions: dict[str, DelegatedSession] = {}

    def executor(self, name: str) -> Executor:
        return require_executor(self._executors, name)

    def remember(self, session: DelegatedSession) -> None:
        self._sessions[session.session_id] = session

    def forget(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def lookup(self, session_id: str) -> DelegatedSession | None:
        return self._sessions.get(session_id)

    # ------------------------------------------------------------------
    # Session authorization (checked at the point of every use)
    # ------------------------------------------------------------------

    async def _authorize_session(
        self, executor: Executor, session_id: str
    ) -> str:
        """Return the authorized directory for *session_id*.

        Uses the in-memory registry when present, otherwise fetches
        session metadata from the backend (post-restart) and re-checks
        containment. Fails closed when the directory cannot be verified.
        """
        known = self.lookup(session_id)
        if known is not None:
            directory = known.directory
        else:
            try:
                info = await executor.session_info(session_id)
            except GatewayError:
                raise
            if not info.directory:
                raise PermissionGatewayError(
                    "Cannot verify the session directory; access denied.",
                    detail=session_id,
                )
            directory = info.directory
            self.remember(
                DelegatedSession(
                    executor=executor.name,
                    session_id=session_id,
                    directory=directory,
                    title=info.title,
                    agent=info.agent,
                )
            )
        try:
            resolved = self._path_policy.resolve_task_directory(
                directory, must_exist=False
            )
        except (UnauthorizedDirectoryError, InvalidRequestError) as exc:
            raise UnauthorizedDirectoryError(
                "Session directory is no longer inside the allowed roots; "
                "access denied.",
                detail=directory,
            ) from exc
        return str(resolved)

    # ------------------------------------------------------------------
    # Gateway / executor discovery
    # ------------------------------------------------------------------

    async def gateway_health(self) -> dict:
        executors: list[dict] = []
        for name, executor in sorted(self._executors.items()):
            health = await executor.health()
            executors.append(
                {
                    "executor": name,
                    "available": health.available,
                    "healthy": health.healthy,
                    "version": health.version,
                    "detail": health.detail,
                }
            )
        return {
            "status": "ok",
            "mode": "direct",
            "opencode_agent_enabled": self._opencode_enabled,
            "commands_enabled": self._commands_enabled,
            "executors": executors,
            "allowed_roots": self._path_policy.allowed_roots(),
        }

    async def list_executors(self) -> list[dict]:
        return [
            {
                "executor": name,
                "capabilities": sorted(executor.capabilities()),
            }
            for name, executor in sorted(self._executors.items())
        ]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start_task(
        self,
        executor_name: str,
        task: str,
        directory: str,
        *,
        title: str | None = None,
        agent: str | None = None,
    ) -> dict:
        executor = self.executor(executor_name)
        resolved = self._path_policy.resolve_task_directory(directory)
        if not task or not task.strip():
            raise InvalidRequestError("Task text must not be empty.")

        session = await executor.create_session(
            resolved,
            title=title or "Delegated task",
            agent=agent,
        )
        await executor.send_prompt(
            session.id,
            task,
            agent=agent,
            directory=resolved,
        )
        origin_message_id = await executor.find_origin_message_id(session.id)
        self.remember(
            DelegatedSession(
                executor=executor_name,
                session_id=session.id,
                directory=str(resolved),
                title=session.title,
                agent=session.agent,
                origin_message_id=origin_message_id,
            )
        )
        logger.info(
            "Task delegated: executor=%s session=%s directory=%s",
            executor_name,
            session.id,
            resolved,
        )
        return {
            "executor": executor_name,
            "session_id": session.id,
            "directory": str(resolved),
            "title": session.title,
            "status": "running",
            "note": (
                "Task accepted asynchronously. Poll agent_status; then use "
                "agent_messages and agent_diff."
            ),
        }

    async def continue_task(
        self,
        executor_name: str,
        session_id: str,
        task: str,
        *,
        agent: str | None = None,
    ) -> dict:
        executor = self.executor(executor_name)
        directory = await self._authorize_session(executor, session_id)
        if not task or not task.strip():
            raise InvalidRequestError("Task text must not be empty.")
        await executor.send_prompt(
            session_id,
            task,
            agent=agent,
            directory=directory,
        )
        known = self.lookup(session_id)
        if known is not None:
            known.origin_message_id = await executor.find_origin_message_id(
                session_id
            )
        logger.info(
            "Task continued: executor=%s session=%s", executor_name, session_id
        )
        return {
            "executor": executor_name,
            "session_id": session_id,
            "directory": directory,
            "status": "running",
            "note": (
                "Follow-up accepted asynchronously. Poll agent_status."
            ),
        }

    async def status(self, executor_name: str, session_id: str) -> dict:
        executor = self.executor(executor_name)
        await self._authorize_session(executor, session_id)
        status = await executor.status(session_id)
        pending = await executor.pending_permissions()
        pending_mine = [
            p for p in pending if p.session_id == session_id
        ]
        completed = None
        if status.state == "idle":
            completed = await executor.is_completed(session_id)
        return {
            "executor": executor_name,
            "session_id": session_id,
            "state": status.state,
            "detail": status.detail,
            "completed": completed,
            "pending_permissions": [
                {
                    "id": p.id,
                    "permission": p.permission,
                    "patterns": p.patterns,
                }
                for p in pending_mine
            ],
        }

    async def session(self, executor_name: str, session_id: str) -> dict:
        executor = self.executor(executor_name)
        await self._authorize_session(executor, session_id)
        info = await executor.session_info(session_id)
        known = self.lookup(session_id)
        return {
            "executor": executor_name,
            "session_id": info.id,
            "directory": info.directory or (known.directory if known else None),
            "title": info.title,
            "agent": info.agent,
            "summary": info.summary,
        }

    async def messages(
        self,
        executor_name: str,
        session_id: str,
        *,
        limit: int = 50,
        before: str | None = None,
    ) -> list[dict]:
        executor = self.executor(executor_name)
        await self._authorize_session(executor, session_id)
        messages = await executor.messages(
            session_id, limit=limit, before=before
        )
        return [
            {
                "id": m.id,
                "role": m.role,
                "time_created": m.time_created,
                "agent": m.agent,
                "model": m.model,
                "finish": m.finish,
                "parts": m.parts,
            }
            for m in messages
        ]

    async def diff(
        self,
        executor_name: str,
        session_id: str,
        *,
        message_id: str | None = None,
    ) -> list[dict]:
        executor = self.executor(executor_name)
        await self._authorize_session(executor, session_id)
        known = self.lookup(session_id)
        if message_id is None and known is not None:
            # OpenCode scopes the diff to the message that triggered the
            # change: the origin (user) message. Assistant messages carry
            # no diffs of their own.
            message_id = known.origin_message_id
        diffs = await executor.diff(session_id, message_id=message_id)
        return [
            {
                "file": d.file,
                "status": d.status,
                "additions": d.additions,
                "deletions": d.deletions,
                "patch": d.patch,
            }
            for d in diffs
        ]

    async def abort(self, executor_name: str, session_id: str) -> dict:
        executor = self.executor(executor_name)
        await self._authorize_session(executor, session_id)
        aborted = await executor.abort(session_id)
        return {
            "executor": executor_name,
            "session_id": session_id,
            "aborted": aborted,
        }

    # ------------------------------------------------------------------
    # Permissions
    # ------------------------------------------------------------------

    async def pending_permissions(self, executor_name: str) -> list[dict]:
        """List pending requests, restricted to authorized sessions.

        Requests whose session cannot be verified as inside the allowed
        roots are dropped; they are never surfaced and never approvable.
        """
        executor = self.executor(executor_name)
        requests = await executor.pending_permissions()
        authorized: list[dict] = []
        for request in requests:
            if not request.session_id:
                continue
            try:
                await self._authorize_session(executor, request.session_id)
            except GatewayError:
                logger.warning(
                    "Dropping permission request for unauthorized session: "
                    "request=%s session=%s",
                    request.id,
                    request.session_id,
                )
                continue
            authorized.append(
                {
                    "id": request.id,
                    "session_id": request.session_id,
                    "permission": request.permission,
                    "patterns": request.patterns,
                    "metadata": request.metadata,
                    "tool": request.tool,
                }
            )
        return authorized

    async def reply_permission(
        self,
        executor_name: str,
        request_id: str,
        reply: str,
        *,
        message: str | None = None,
    ) -> dict:
        if reply not in _PERMISSION_REPLIES:
            raise InvalidRequestError(
                f"Invalid permission reply {reply!r}; must be one of "
                f"{', '.join(_PERMISSION_REPLIES)}."
            )
        executor = self.executor(executor_name)
        requests = await executor.pending_permissions()
        request = next(
            (r for r in requests if r.id == request_id), None
        )
        if request is None:
            raise PermissionGatewayError(
                "Permission request not found or no longer pending.",
                detail=request_id,
            )
        if not request.session_id:
            raise PermissionGatewayError(
                "Permission request has no session; cannot verify "
                "authorization; reply denied.",
                detail=request_id,
            )
        await self._authorize_session(executor, request.session_id)
        result = await executor.reply_permission(
            request_id, reply, message=message
        )
        return {
            "executor": executor_name,
            "request_id": request_id,
            "reply": reply,
            "processed": result,
        }

    async def executor_capability(
        self, executor_name: str, capability: str
    ) -> bool:
        executor = self.executor(executor_name)
        return capability in executor.capabilities()

    def health_failure(self, exc: GatewayError) -> dict:
        return {
            "status": "error",
            "detail": str(exc),
        }