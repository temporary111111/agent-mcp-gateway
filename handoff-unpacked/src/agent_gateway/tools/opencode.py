"""OpenCode-specific diagnostic MCP tools.

These expose information that is not represented by the generic agent
lifecycle. They are diagnostics only; the primary orchestration should use
the generic agent_* tools.
"""

from __future__ import annotations

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from ..errors import ExecutorUnavailableError
from ..logging import get_logger
from ..services.delegation import DelegationService
from .helpers import tool_handler

logger = get_logger("tools.opencode")

READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    open_world_hint=False,
)


def _opencode_executor(delegation: DelegationService):
    from ..executors.opencode.executor import OpenCodeExecutor

    executor = delegation.executor("opencode")
    if not isinstance(executor, OpenCodeExecutor):
        raise ExecutorUnavailableError(
            "The 'opencode' executor is not available."
        )
    return executor


def register_opencode_tools(
    mcp: MCPServer, delegation: DelegationService
) -> None:
    @mcp.tool(
        title="OpenCode health detail",
        annotations=READ_ONLY,
    )
    @tool_handler
    async def opencode_health() -> dict:
        """Detailed health information for the local OpenCode backend:
        reachability, health flag, version, and URL."""
        executor = _opencode_executor(delegation)
        health = await executor.health()
        return {
            "executor": "opencode",
            "url": executor.url,
            "available": health.available,
            "healthy": health.healthy,
            "version": health.version,
            "detail": health.detail,
        }

    @mcp.tool(
        title="List OpenCode agents",
        annotations=READ_ONLY,
    )
    @tool_handler
    async def opencode_agents() -> list[dict]:
        """List the agents defined in the local OpenCode backend, with
        their mode (primary/subagent) and description."""
        executor = _opencode_executor(delegation)
        return await executor.list_agents()

    @mcp.tool(
        title="List OpenCode providers",
        annotations=READ_ONLY,
    )
    @tool_handler
    async def opencode_providers() -> dict:
        """List the model providers configured in the local OpenCode
        backend. Model details are intentionally excluded."""
        executor = _opencode_executor(delegation)
        return await executor.list_providers()