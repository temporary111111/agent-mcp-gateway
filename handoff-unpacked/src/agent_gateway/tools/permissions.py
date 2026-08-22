"""Permission workflow MCP tools.

The gateway NEVER auto-approves permission requests. The supervisor model
lists pending requests and the human/user explicitly decides via
agent_reply_permission.
"""

from __future__ import annotations

from typing import Annotated

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from ..logging import get_logger
from ..services.delegation import DelegationService
from .helpers import tool_handler

logger = get_logger("tools.permissions")

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


def register_permission_tools(
    mcp: MCPServer, delegation: DelegationService
) -> None:
    @mcp.tool(
        title="List pending permission requests",
        annotations=READ_ONLY,
    )
    @tool_handler
    async def agent_pending_permissions(
        executor: EXECUTOR_FIELD = "opencode",
    ) -> list[dict]:
        """List permission requests the local agent is waiting on. The
        gateway never approves these automatically; a human must decide
        via agent_reply_permission."""
        return await delegation.pending_permissions(executor)

    @mcp.tool(
        title="Reply to a permission request",
        annotations=ToolAnnotations(
            read_only_hint=False,
            open_world_hint=False,
        ),
    )
    @tool_handler
    async def agent_reply_permission(
        executor: EXECUTOR_FIELD = "opencode",
        request_id: Annotated[
            str,
            Field(
                pattern=r"^per",
                description=(
                    "Permission request ID from agent_pending_permissions."
                ),
            ),
        ] = ...,
        reply: Annotated[
            str,
            Field(
                description=(
                    "Decision: 'once' (allow this one time), 'always' "
                    "(remember the rule), or 'reject' (deny)."
                ),
            ),
        ] = ...,
        message: Annotated[
            str | None,
            Field(
                description=(
                    "Optional human-readable note attached to the decision."
                ),
            ),
        ] = None,
    ) -> dict:
        """Explicitly approve or deny a pending permission request. Only a
        human should decide; never call with 'always' without explicit
        user consent."""
        return await delegation.reply_permission(
            executor, request_id, reply, message=message
        )