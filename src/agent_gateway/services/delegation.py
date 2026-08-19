"""Delegation orchestration.

Sits between the MCP tools and the executor abstraction. Owns the
executor registry and the directory path policy, and exposes the generic
agent lifecycle used by the MCP tools:

    start -> status -> messages -> diff -> continue | abort
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from ..errors import GatewayError
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


class DelegationService:
    def __init__(
        self,
        executors: Mapping[str, Executor],
        path_policy: PathPolicy,
    ) -> None:
        self._executors = dict(executors)
        self._path_policy = path_policy
        self._sessions: dict[str, DelegatedSession] = {}

    def executor(self, name: str) -> Executor:
        return require_executor(self._executors, name)

    def remember(self, session: DelegatedSession) -> None:
        self._sessions[session.session_id] = session

    def forget(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def lookup(self, session_id: str) -> DelegatedSession | None:
        return self._sessions.get(session_id)

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
            from ..errors import InvalidRequestError

            raise InvalidRequestError("Task text must not be empty.")

        session = await executor.create_session(
            resolved,
            title=title or "Delegated task",
            agent=agent,
        )
        self.remember(
            DelegatedSession(
                executor=executor_name,
                session_id=session.id,
                directory=str(resolved),
                title=session.title,
                agent=session.agent,
            )
        )
        await executor.send_prompt(
            session.id,
            task,
            agent=agent,
            directory=resolved,
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
        known = self.lookup(session_id)
        directory = known.directory if known else None
        await executor.send_prompt(
            session_id,
            task,
            agent=agent,
            directory=directory,
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
        status = await executor.status(session_id)
        pending = await executor.pending_permissions()
        pending_mine = [
            p
            for p in pending
            if p.session_id == session_id
        ]
        return {
            "executor": executor_name,
            "session_id": session_id,
            "state": status.state,
            "detail": status.detail,
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
        aborted = await executor.abort(session_id)
        return {
            "executor": executor_name,
            "session_id": session_id,
            "aborted": aborted,
        }

    async def pending_permissions(self, executor_name: str) -> list[dict]:
        executor = self.executor(executor_name)
        requests = await executor.pending_permissions()
        return [
            {
                "id": r.id,
                "session_id": r.session_id,
                "permission": r.permission,
                "patterns": r.patterns,
                "metadata": r.metadata,
                "tool": r.tool,
            }
            for r in requests
        ]

    async def reply_permission(
        self,
        executor_name: str,
        request_id: str,
        reply: str,
        *,
        message: str | None = None,
    ) -> dict:
        if reply not in _PERMISSION_REPLIES:
            from ..errors import InvalidRequestError

            raise InvalidRequestError(
                f"Invalid permission reply {reply!r}; must be one of "
                f"{', '.join(_PERMISSION_REPLIES)}."
            )
        executor = self.executor(executor_name)
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
