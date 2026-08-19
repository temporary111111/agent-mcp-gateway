"""MCP server assembly and entry point for the agent gateway.

Dependency flow:

    MCP tools -> DelegationService -> Executor interface
                                    -> OpenCodeExecutor -> OpenCodeClient
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from . import __version__
from .config import Config
from .executors import build_executors
from .logging import configure_logging, get_logger
from .security.paths import PathPolicy
from .services.delegation import DelegationService
from .tools.delegation import register_delegation_tools
from .tools.gateway import register_gateway_tools
from .tools.opencode import register_opencode_tools
from .tools.permissions import register_permission_tools

logger = get_logger("server")

GATEWAY_INSTRUCTIONS = (
    "You are connected to a local Agent Gateway. The gateway delegates "
    "coding and system work to local agent executors (currently OpenCode) "
    "running on the user's machine.\n"
    "\n"
    "Workflow:\n"
    "- Start with gateway_health / agent_executors if availability is "
    "uncertain.\n"
    "- agent_start_task returns a session ID immediately; tasks run "
    "asynchronously. Never assume a task is finished without checking "
    "agent_status.\n"
    "- Poll agent_status; when it reports idle, read results with "
    "agent_messages and agent_diff, then agent_continue if more work is "
    "needed. Use agent_abort to stop runaway work.\n"
    "- Permissions are NEVER auto-approved. If agent_status or "
    "agent_pending_permissions shows pending requests, surface them to "
    "the user and only act on explicit instructions via "
    "agent_reply_permission.\n"
    "- Tasks may only run inside operator-allowed directories; requests "
    "outside those roots are rejected.\n"
    "- Do not claim files were modified unless agent_diff shows changes."
)


def normalize_public_host(value: str) -> str:
    """Extract a bare hostname from a public URL or host string."""
    value = value.strip()
    if not value:
        return ""
    if "://" in value:
        parsed = urlparse(value)
        return parsed.netloc
    return value.split("/", 1)[0]


def build_transport_security(
    public_mcp_host: str,
) -> TransportSecuritySettings:
    """Transport security for the Streamable HTTP endpoint.

    Localhost is always allowed; PUBLIC_MCP_HOST (e.g. the hostname of a
    Cloudflare Quick Tunnel) is added to allowed hosts and origins. DNS
    rebinding protection stays enabled - this is what makes Cloudflare
    forwarding work without disabling security checks.
    """
    allowed_hosts = [
        "127.0.0.1",
        "127.0.0.1:*",
        "localhost",
        "localhost:*",
    ]
    allowed_origins = [
        "http://127.0.0.1:*",
        "http://localhost:*",
    ]

    public_host = normalize_public_host(public_mcp_host)
    if public_host:
        allowed_hosts.extend([public_host, f"{public_host}:*"])
        allowed_origins.extend(
            [
                f"https://{public_host}",
                f"https://{public_host}:*",
            ]
        )

    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )


def build_server(config: Config) -> MCPServer:
    """Assemble the MCP server from configuration."""
    configure_logging(config.log_level)
    logger.info("Starting agent gateway v%s", __version__)
    logger.info("Configuration: %s", config.summary())

    path_policy = PathPolicy(config.allowed_roots)
    logger.info(
        "Allowed task roots: %s",
        path_policy.allowed_roots() or "(none - fail closed)",
    )

    executors = build_executors(config)
    logger.info("Registered executors: %s", ", ".join(sorted(executors)))

    delegation = DelegationService(executors, path_policy)

    mcp = MCPServer(
        "Agent Gateway",
        instructions=GATEWAY_INSTRUCTIONS,
    )

    register_gateway_tools(mcp, delegation)
    register_delegation_tools(mcp, delegation)
    register_permission_tools(mcp, delegation)
    register_opencode_tools(mcp, delegation)

    logger.info("Registered %d tools", len(mcp._tool_manager._tools))
    return mcp


def run(config: Config) -> None:
    mcp = build_server(config)
    transport_security = build_transport_security(config.public_mcp_host)

    print()
    print("========================================")
    print("Agent Gateway")
    print("========================================")
    print(f"Version         : {__version__}")
    print(f"MCP endpoint    : http://{config.mcp_host}:{config.mcp_port}/mcp")
    print(
        "Public MCP host : "
        f"{config.public_mcp_host or '(localhost only)'}"
    )
    print(
        "Allowed roots   : "
        f"{'; '.join(str(p) for p in config.allowed_roots) or '(none)'}"
    )
    print("========================================")
    print()

    mcp.run(
        transport="streamable-http",
        host=config.mcp_host,
        port=config.mcp_port,
        stateless_http=True,
        json_response=True,
        transport_security=transport_security,
    )


def main() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    config = Config.from_env()
    run(config)


if __name__ == "__main__":
    main()