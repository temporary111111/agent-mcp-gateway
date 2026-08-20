"""OpenCode executor: adapts the OpenCode headless server to the Executor interface."""

from __future__ import annotations

import re
from pathlib import Path

from ...config import Config
from ...errors import (
    GatewayError,
    InvalidRequestError,
    InvalidSessionError,
    SessionBusyError,
)
from ...logging import get_logger
from ..base import (
    Executor,
    ExecutorHealth,
    FileDiff,
    MessageInfo,
    PermissionRequestInfo,
    SessionInfo,
    SessionStatusInfo,
)
from .client import OpenCodeClient

logger = get_logger("opencode.executor")

SESSION_ID_RE = re.compile(r"^ses")
MESSAGE_ID_RE = re.compile(r"^msg")
PERMISSION_ID_RE = re.compile(r"^per")

PERMISSION_REPLIES = ("once", "always", "reject")

_MAX_TEXT_PART_CHARS = 4000
_MAX_TOOL_CALL_CHARS = 500


def validate_session_id(session_id: str) -> str:
    if not session_id or not SESSION_ID_RE.match(session_id):
        raise InvalidSessionError(
            "Invalid session ID.",
            detail="OpenCode session IDs start with 'ses'.",
        )
    return session_id


def validate_message_id(message_id: str) -> str:
    if not message_id or not MESSAGE_ID_RE.match(message_id):
        raise InvalidRequestError(
            "Invalid message ID.",
            detail="OpenCode message IDs start with 'msg'.",
        )
    return message_id


def validate_permission_id(request_id: str) -> str:
    if not request_id or not PERMISSION_ID_RE.match(request_id):
        raise InvalidRequestError(
            "Invalid permission request ID.",
            detail="OpenCode permission IDs start with 'per'.",
        )
    return request_id


def validate_permission_reply(reply: str) -> str:
    if reply not in PERMISSION_REPLIES:
        raise InvalidRequestError(
            f"Invalid permission reply {reply!r}; must be one of "
            f"{', '.join(PERMISSION_REPLIES)}."
        )
    return reply


class OpenCodeExecutor(Executor):
    name = "opencode"

    def __init__(self, config: Config, client: OpenCodeClient | None = None) -> None:
        self._config = config
        self._client = client or OpenCodeClient(config)

    @property
    def url(self) -> str:
        return self._config.opencode_url

    def capabilities(self) -> set[str]:
        return {"agents", "providers"}

    async def health(self) -> ExecutorHealth:
        try:
            health = await self._client.health()
            return ExecutorHealth(
                available=True,
                healthy=health.healthy,
                version=health.version,
            )
        except GatewayError as exc:
            logger.warning("OpenCode health check failed: %s", exc)
            return ExecutorHealth(
                available=False,
                healthy=False,
                detail=str(exc),
            )

    async def create_session(
        self,
        directory: Path,
        *,
        title: str | None = None,
        agent: str | None = None,
    ) -> SessionInfo:
        detail = await self._client.create_session(
            str(directory),
            title=title,
            agent=agent,
        )
        logger.info(
            "OpenCode session created: session=%s directory=%s",
            detail.id,
            directory,
        )
        return SessionInfo(
            id=detail.id,
            directory=directory.as_posix(),
            title=detail.title,
            agent=detail.agent,
            summary=detail.summary or {},
        )

    async def send_prompt(
        self,
        session_id: str,
        task: str,
        *,
        agent: str | None = None,
        directory: Path | None = None,
    ) -> None:
        validate_session_id(session_id)
        if not task or not task.strip():
            raise InvalidRequestError("Task text must not be empty.")
        try:
            await self._client.send_prompt(
                session_id,
                task,
                agent=agent,
                directory=str(directory) if directory else None,
            )
        except GatewayError as exc:
            if isinstance(exc, SessionBusyError):
                raise
            if exc.code.value == "backend_http_error" and exc.status_code == 400:
                status = await self.status(session_id)
                if status.state == "busy":
                    raise SessionBusyError(
                        "Session is busy; wait until agent_status reports "
                        "idle before continuing.",
                        detail=session_id,
                    ) from exc
            raise
        logger.info("OpenCode prompt dispatched: session=%s", session_id)

    async def status(self, session_id: str) -> SessionStatusInfo:
        validate_session_id(session_id)
        status_map = await self._client.session_status_map()
        entry = status_map.get(session_id)
        if entry is not None:
            return SessionStatusInfo(
                state=entry.type,
                detail={
                    k: v
                    for k, v in entry.model_dump().items()
                    if v is not None and k != "type"
                },
            )
        detail = await self._client.session_detail(session_id)
        return SessionStatusInfo(state="idle", detail={"title": detail.title})

    async def session_info(self, session_id: str) -> SessionInfo:
        validate_session_id(session_id)
        detail = await self._client.session_detail(session_id)
        return SessionInfo(
            id=detail.id,
            directory=detail.directory or "",
            title=detail.title,
            agent=detail.agent,
            summary=detail.summary or {},
        )

    async def messages(
        self,
        session_id: str,
        *,
        limit: int = 50,
        before: str | None = None,
    ) -> list[MessageInfo]:
        validate_session_id(session_id)
        if before:
            validate_message_id(before)
        entries = await self._client.messages(session_id, limit=limit, before=before)
        return [self._render_message(entry) for entry in entries]

    @staticmethod
    def _render_message(entry) -> MessageInfo:
        info = entry.info or {}
        parts: list[dict] = []
        for part in entry.parts:
            kind = part.type or "unknown"
            if kind == "text" and part.text:
                text = part.text
                if len(text) > _MAX_TEXT_PART_CHARS:
                    text = text[:_MAX_TEXT_PART_CHARS] + "...[truncated]"
                parts.append({"type": "text", "text": text})
            elif kind == "tool":
                parts.append(
                    {
                        "type": "tool",
                        "tool": part.tool,
                        "callID": part.callID,
                        "state": part.state if isinstance(part.state, str) else None,
                    }
                )
            elif kind == "agent":
                parts.append({"type": "agent", "name": part.name})
            elif kind in ("step-start", "step-finish", "reasoning"):
                parts.append({"type": kind})
            else:
                parts.append({"type": kind})
        return MessageInfo(
            id=info.get("id"),
            role=info.get("role", "unknown"),
            time_created=(
                info.get("time", {}).get("created")
                if isinstance(info.get("time"), dict)
                else None
            ),
            agent=info.get("agent"),
            model=info.get("modelID"),
            finish=info.get("finish"),
            parts=parts,
        )

    async def diff(
        self,
        session_id: str,
        *,
        message_id: str | None = None,
    ) -> list[FileDiff]:
        validate_session_id(session_id)
        if message_id:
            validate_message_id(message_id)
        diffs = await self._client.diff(session_id, message_id=message_id)
        return [
            FileDiff(
                file=item.file,
                status=item.status,
                additions=item.additions,
                deletions=item.deletions,
                patch=item.patch,
            )
            for item in diffs
        ]

    async def abort(self, session_id: str) -> bool:
        validate_session_id(session_id)
        aborted = await self._client.abort(session_id)
        logger.info("OpenCode session aborted: session=%s", session_id)
        return aborted

    async def is_completed(self, session_id: str) -> bool:
        """Completion heuristic: idle AND the last assistant turn finished.

        An idle state alone is NOT proof of completion: after a tool-call
        turn the session rests briefly with finish=\"tool-calls\" before
        processing results. Only finish=\"stop\" means the session work is
        actually done and the diff snapshot is final.
        """
        status = await self.status(session_id)
        if status.state in ("busy", "retry"):
            return False
        messages = await self.messages(session_id, limit=5)
        assistant = [m for m in messages if m.role == "assistant"]
        if not assistant:
            return True
        last = assistant[-1]  # messages are oldest-first
        return last.finish == "stop"

    async def find_origin_message_id(
        self, session_id: str, max_attempts: int = 5
    ) -> str | None:
        """Best-effort capture of the newest user message ID.

        prompt_async returns 204 with no body, so the originating user
        message is discovered by listing messages shortly after dispatch.
        Message-scoped diffs use this ID when available.
        """
        import asyncio

        for attempt in range(max_attempts):
            try:
                messages = await self.messages(session_id, limit=20)
            except GatewayError:
                return None
            user_messages = [m for m in messages if m.role == "user"]
            if user_messages:
                return user_messages[0].id
            await asyncio.sleep(0.2)
        logger.warning(
            "Could not capture originating user message ID for session %s",
            session_id,
        )
        return None

    async def pending_permissions(self) -> list[PermissionRequestInfo]:
        requests = await self._client.pending_permissions()
        return [
            PermissionRequestInfo(
                id=item.id,
                session_id=item.sessionID,
                permission=item.permission,
                patterns=item.patterns,
                metadata=item.metadata,
                tool=item.tool,
            )
            for item in requests
        ]

    async def reply_permission(
        self,
        request_id: str,
        reply: str,
        *,
        message: str | None = None,
    ) -> bool:
        validate_permission_id(request_id)
        validate_permission_reply(reply)
        result = await self._client.reply_permission(
            request_id, reply, message=message
        )
        logger.info(
            "OpenCode permission replied: request=%s reply=%s", request_id, reply
        )
        return result

    async def list_agents(self) -> list[dict]:
        agents = await self._client.agents()
        return [
            {
                "name": agent.name,
                "description": agent.description,
                "mode": agent.mode,
                "hidden": agent.hidden,
            }
            for agent in agents
            if not agent.hidden
        ]

    async def list_providers(self) -> dict:
        providers = await self._client.providers()
        return {
            "connected": providers.connected,
            "default": providers.default,
            "providers": [
                {
                    "id": provider.id,
                    "name": provider.name,
                    "source": provider.source,
                    "model_count": len(provider.models),
                }
                for provider in providers.all
            ],
        }