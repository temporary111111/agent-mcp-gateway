"""MCP server assembly and entry point for the agent gateway.

V2 architecture:

    ChatGPT Web / GPT-5.6 Sol
        |
        | MCP primitive tool calls
        v
    Agent Gateway
        |
        +-- deterministic filesystem tools      (workspace_open, tree, read,
        |                                        stat, find, write, replace,
        |                                        apply_patch)
        +-- deterministic search tools          (code_search)
        +-- deterministic process/test tools    (process_run, opt-in)
        +-- deterministic git tools             (status, diff, log, show)
        |
        v
    Local Windows machine / allowed repositories

GPT-5.6 Sol is the reasoning agent in direct mode. The gateway executes
deterministic tools and does not invoke another LLM. OpenCode agent
delegation is an optional compatibility mode (ENABLE_OPENCODE_AGENT=true),
disabled by default.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from . import __version__
from .config import Config
from .direct.instructions import generate_dynamic_instructions
from .executors import build_executors
from .logging import configure_logging, get_logger
from .security.auth import wrap_with_auth
from .security.paths import PathPolicy
from .services.delegation import DelegationService
from .tools.direct import register_direct_tools
from .tools.gateway import register_gateway_tools
from .workspaces.manager import WorkspaceManager

logger = get_logger("server")


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
    public_exposure: bool = False,
) -> TransportSecuritySettings:
    """Transport security for the Streamable HTTP endpoint.

    When public_exposure is True, DNS rebinding protection is disabled
    entirely (any host/origin is accepted). This is the simplified path
    for Cloudflare Quick Tunnel users.

    When public_exposure is False, localhost is always allowed and
    PUBLIC_MCP_HOST is added to the allowlist if set.
    """
    if public_exposure:
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
            allowed_hosts=[],
            allowed_origins=[],
        )

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

    # Generate dynamic instructions based on project type
    workspace_path = config.allowed_roots[0] if config.allowed_roots else None
    agents_md_content = ""
    if workspace_path:
        from .direct.agents_md import load_agents_md
        try:
            agents_md = load_agents_md(workspace_path)
            if agents_md["found"]:
                agents_md_content = agents_md["instructions"]
        except Exception:
            pass

    instructions = generate_dynamic_instructions(
        workspace_path=workspace_path,
        agents_md_content=agents_md_content,
    )

    mcp = MCPServer(
        "Agent Gateway",
        instructions=instructions,
    )

    workspaces = WorkspaceManager(path_policy)
    register_direct_tools(mcp, workspaces, config)
    logger.info("Direct mode: registered deterministic tools")

    if config.enable_opencode_agent:
        executors = build_executors(config)
        delegation = DelegationService(
            executors, path_policy, opencode_enabled=True,
            commands_enabled=config.enable_commands,
        )
        from .tools.delegation import register_delegation_tools
        from .tools.opencode import register_opencode_tools
        from .tools.permissions import register_permission_tools

        register_gateway_tools(mcp, delegation)
        register_delegation_tools(mcp, delegation)
        register_permission_tools(mcp, delegation)
        register_opencode_tools(mcp, delegation)
        logger.info(
            "OpenCode agent mode: enabled (executors: %s)",
            ", ".join(sorted(executors)),
        )
    else:
        delegation = DelegationService(
            {}, path_policy, opencode_enabled=False,
            commands_enabled=config.enable_commands,
        )
        register_gateway_tools(mcp, delegation)
        logger.info("OpenCode agent mode: disabled (default)")

    logger.info("Registered %d tools", len(mcp._tool_manager._tools))
    return mcp


def build_http_app(config: Config, mcp: MCPServer):
    """Assemble the wrapped Streamable HTTP app (with bearer auth)."""
    transport_security = build_transport_security(
        config.public_mcp_host,
        public_exposure=config.public_exposure,
    )
    app = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        transport_security=transport_security,
        host=config.mcp_host,
    )
    return wrap_with_auth(app, config.gateway_token)


def run(config: Config) -> None:
    import uvicorn

    mcp = build_server(config)
    app = build_http_app(config, mcp)

    print()
    print("========================================")
    print("Agent Gateway")
    print("========================================")
    print(f"Version         : {__version__}")
    print(f"MCP endpoint    : http://{config.mcp_host}:{config.mcp_port}/mcp")
    print(
        "Public exposure : "
        f"{'enabled (any host)' if config.public_exposure else 'disabled (localhost only)'}"
    )
    print(
        "Commands        : "
        f"{'enabled' if config.enable_commands else 'disabled'}"
    )
    print(
        "Allowed roots   : "
        f"{'; '.join(str(p) for p in config.allowed_roots) or '(none)'}"
    )
    print("========================================")
    print()

    uvicorn.run(
        app,
        host=config.mcp_host,
        port=config.mcp_port,
        log_level="warning",
    )


def main() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    config = Config.build()
    run(config)


if __name__ == "__main__":
    main()