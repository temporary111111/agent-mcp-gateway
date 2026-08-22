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
from .executors import build_executors
from .logging import configure_logging, get_logger
from .security.auth import wrap_with_auth
from .security.paths import PathPolicy
from .services.delegation import DelegationService
from .tools.direct import register_direct_tools
from .tools.gateway import register_gateway_tools
from .workspaces.manager import WorkspaceManager

logger = get_logger("server")

GATEWAY_INSTRUCTIONS = (
    "You are connected to a local Agent Gateway. You (the supervising "
    "model) are the reasoning agent; the gateway only executes "
    "deterministic tools and does not invoke another LLM.\n"
    "\n"
    "DIRECT WORKFLOW (default):\n"
    "- workspace_open(<allowed directory>) to bind a workspace and get a "
    "workspace_id.\n"
    "- Inspect: workspace_tree, file_read, file_stat, file_find, "
    "code_search.\n"
    "- Decide the next action yourself. Write with file_write, edit with "
    "file_replace or file_apply_patch (pass expected_sha256 from your last "
    "read to prevent stale overwrites).\n"
    "- Run tests/builds with process_run (only when the operator enabled "
    "AGENT_ENABLE_COMMANDS=true) and inspect outputs yourself.\n"
    "- Review with git_status / git_diff / git_log / git_show. Nothing is "
    "committed automatically; you decide what to report.\n"
    "- Iterate: read -> decide -> write -> verify -> review until done, "
    "then report completion. Do not hand a natural-language task to "
    "another agent in this mode.\n"
    "\n"
    "SECURITY:\n"
    "- All paths are workspace-relative; the gateway rejects absolute "
    "paths, '..' traversal, symlink escapes, and anything outside the "
    "operator-configured allowed roots.\n"
    "- process_run runs with the gateway user's OS privileges; only use "
    "it when the operator enabled commands and the user approved the "
    "intent.\n"
    "- Never claim a file changed without a read-back or git_diff proof.\n"
    "- No git operation in direct mode commits, pushes, resets, or "
    "overwrites; those are separate operator-only actions."
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

    mcp = MCPServer(
        "Agent Gateway",
        instructions=GATEWAY_INSTRUCTIONS,
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
    transport_security = build_transport_security(config.public_mcp_host)
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
        "Public MCP host : "
        f"{config.public_mcp_host or '(localhost only)'}"
    )
    print(
        "Bearer token    : "
        f"{'configured' if config.token_auth_enabled() else 'not set (localhost only)'}"
    )
    print("Direct mode     : enabled (deterministic tools, no LLM)")
    print(
        "OpenCode agent  : "
        f"{'enabled' if config.enable_opencode_agent else 'disabled'}"
    )
    print(
        "Commands        : "
        f"{'enabled' if config.enable_commands else 'disabled (AGENT_ENABLE_COMMANDS)'}"
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

    config = Config.from_env()
    run(config)


if __name__ == "__main__":
    main()