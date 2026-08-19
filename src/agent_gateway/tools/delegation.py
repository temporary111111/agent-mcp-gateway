"""Delegation lifecycle MCP tools.

Start -> status -> messages -> diff -> continue/abort. All task dispatch
is asynchronous: tools return immediately and never block on agent work.
"""

from __future__ import annotations

from typing import Annotated

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from ..logging import get_logger
from ..services.delegation import DEFAULT_EXECUTOR, DelegationService
from .helpers import tool_handler

logger = get_logger("tools.delegation")

READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    open_world_hint=False,
)

EXECUTOR_FIELD = Annotated[
    str,
    Field(
        description=(
            "Executor to delegate to. Only 'opencode' exists in this "
            "version."
        ),
    ),
]


def _executor_default() -> str:
    return DEFAULT_EXECUTOR


def register_delegation_tools(
    mcp: MCPServer, delegation: DelegationService
) -> None:
    @mcp.tool(
        title="Start a delegated agent task",
        annotations=ToolAnnotations(
            read_only_hint=False,
            open_world_hint=False,
        ),
    )
    @tool_handler
    async def agent_start_task(
        executor: EXECUTOR_FIELD = "opencode",
        task: Annotated[
            str,
            Field(
                min_length=1,
                description=(
                    "High-level goal for the local agent. The agent "
                    "decides which shell/file/git operations to perform."
                ),
            ),
        ] = ...,
        directory: Annotated[
            str,
            Field(
                description=(
                    "Absolute path to the task directory. Must be inside "
                    "an operator-allowed root."
                ),
            ),
        ] = ...,
        title: Annotated[
            str | None,
            Field(description="Optional human-readable session title."),
        ] = None,
        agent: Annotated[
            str | None,
            Field(
                description=(
                    "OpenCode agent to use (e.g. 'build', 'plan'). Defaults "
                    "to the backend default."
                ),
            ),
        ] = None,
    ) -> dict:
        """Create a persistent session in `directory`, dispatch `task` to
        the executor asynchronously, and return the session ID
        immediately. Poll agent_status afterwards; never block here."""
        return await delegation.start_task(
            executor,
            task,
            directory,
            title=title,
            agent=agent,
        )

    @mcp.tool(
        title="Continue a delegated task",
        annotations=ToolAnnotations(
            read_only_hint=False,
            open_world_hint=False,
        ),
    )
    @tool_handler
    async def agent_continue(
        executor: EXECUTOR_FIELD = "opencode",
        session_id: Annotated[
            str,
            Field(
                pattern=r"^ses",
                description="Session ID returned by agent_start_task.",
            ),
        ] = ...,
        task: Annotated[
            str,
            Field(
                min_length=1,
                description=(
                    "Follow-up instruction for the running session."
                ),
            ),
        ] = ...,
        agent: Annotated[
            str | None,
            Field(description="Optional OpenCode agent override."),
        ] = None,
    ) -> dict:
        """Send a follow-up prompt to an existing session asynchronously.
        Returns immediately; poll agent_status for completion."""
        return await delegation.continue_task(
            executor, session_id, task, agent=agent
        )

    @mcp.tool(
        title="Agent session status",
        annotations=READ_ONLY,
    )
    @tool_handler
    async def agent_status(
        executor: EXECUTOR_FIELD = "opencode",
        session_id: Annotated[
            str,
            Field(
                pattern=r"^ses",
                description="Session ID returned by agent_start_task.",
            ),
        ] = ...,
    ) -> dict:
        """Return the current state of a delegated session: busy, idle, or
        retry, plus any pending permission requests for that session."""
        return await delegation.status(executor, session_id)

    @mcp.tool(
        title="Agent session details",
        annotations=READ_ONLY,
    )
    @tool_handler
    async def agent_session(
        executor: EXECUTOR_FIELD = "opencode",
        session_id: Annotated[
            str,
            Field(
                pattern=r"^ses",
                description="Session ID returned by agent_start_task.",
            ),
        ] = ...,
    ) -> dict:
        """Return metadata about a delegated session: title, agent,
        directory, and change summary."""
        return await delegation.session(executor, session_id)

    @mcp.tool(
        title="Agent session messages",
        annotations=READ_ONLY,
    )
    @tool_handler
    async def agent_messages(
        executor: EXECUTOR_FIELD = "opencode",
        session_id: Annotated[
            str,
            Field(
                pattern=r"^ses",
                description="Session ID returned by agent_start_task.",
            ),
        ] = ...,
        limit: Annotated[
            int,
            Field(
                ge=1,
                le=200,
                description="Maximum number of messages to return.",
            ),
        ] = 50,
        before: Annotated[
            str | None,
            Field(
                pattern=r"^msg",
                description="Return messages older than this message ID.",
            ),
        ] = None,
    ) -> list[dict]:
        """Retrieve messages from a delegated session, newest first, with
        text and tool-call parts."""
        return await delegation.messages(
            executor, session_id, limit=limit, before=before
        )

    @mcp.tool(
        title="Agent session diff",
        annotations=READ_ONLY,
    )
    @tool_handler
    async def agent_diff(
        executor: EXECUTOR_FIELD = "opencode",
        session_id: Annotated[
            str,
            Field(
                pattern=r"^ses",
                description="Session ID returned by agent_start_task.",
            ),
        ] = ...,
        message_id: Annotated[
            str | None,
            Field(
                pattern=r"^msg",
                description=(
                    "Limit the diff to the changes produced by a specific "
                    "message."
                ),
            ),
        ] = None,
    ) -> list[dict]:
        """Return the file changes (diffs) the agent produced, with per-file
        patches."""
        return await delegation.diff(
            executor, session_id, message_id=message_id
        )

    @mcp.tool(
        title="Abort agent session",
        annotations=ToolAnnotations(
            read_only_hint=False,
            open_world_hint=False,
        ),
    )
    @tool_handler
    async def agent_abort(
        executor: EXECUTOR_FIELD = "opencode",
        session_id: Annotated[
            str,
            Field(
                pattern=r"^ses",
                description="Session ID returned by agent_start_task.",
            ),
        ] = ...,
    ) -> dict:
        """Abort a busy delegated session and stop ongoing agent work."""
        return await delegation.abort(executor, session_id)