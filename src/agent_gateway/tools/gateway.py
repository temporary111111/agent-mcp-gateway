"""Gateway-level MCP tools: health and executor discovery."""

from __future__ import annotations

from typing import Annotated

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from ..logging import get_logger
from ..services.delegation import DelegationService
from .helpers import tool_handler

logger = get_logger("tools.gateway")

READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    open_world_hint=False,
)


def register_gateway_tools(mcp: MCPServer, delegation: DelegationService) -> None:
    @mcp.tool(
        title="Gateway health",
        annotations=READ_ONLY,
    )
    @tool_handler
    async def gateway_health() -> dict:
        """Check gateway health and the reachability of every configured
        executor backend. Call this first when backend availability is
        uncertain."""
        return await delegation.gateway_health()

    @mcp.tool(
        title="List agent executors",
        annotations=READ_ONLY,
    )
    @tool_handler
    async def agent_executors() -> list[dict]:
        """List the local agent executors available for delegated work and
        their extra capabilities."""
        return await delegation.list_executors()