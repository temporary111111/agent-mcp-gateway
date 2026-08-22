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

GATEWAY_INSTRUCTIONS = """\
You are connected to a local Agent Gateway. You are the reasoning agent;
the gateway executes deterministic tools and does not invoke another LLM.

═══════════════════════════════════════════════════════════════════════
 AGENTIC LOOP — Follow this for every task
═══════════════════════════════════════════════════════════════════════

1. PLAN (before acting)
   - Read the task carefully. Break it into concrete steps.
   - Use todo_write to track your plan. Set each step as pending,
     then mark in_progress as you work, completed when done.
   - If the project has AGENTS.md, read it first — it contains
     project-specific conventions, build commands, and test patterns.

2. EXPLORE (understand the codebase)
   - workspace_open → workspace_tree → file_find → code_search
   - Read relevant files with file_read. Understand the existing
     patterns, imports, naming conventions, and architecture.
   - Do NOT write code until you understand the context.

3. EXECUTE (make changes)
   - Write with file_write (full file) or file_replace/file_apply_patch
     (surgical edits). Pass expected_sha256 from your last read.
   - Use process_run to build/test. Check exit codes and output.
   - Work in small, atomic steps. Verify each step before moving on.

4. VERIFY (prove it works)
   - After writing: file_read the changed lines to confirm.
   - Run tests/builds with process_run and inspect output.
   - Run git_diff to review all changes. Nothing is auto-committed.
   - If verification fails → go back to EXPLORE or EXECUTE.

5. ITERATE (loop until done)
   - Repeat steps 2-4 until the task is complete.
   - If stuck: re-read the task, check AGENTS.md, try a different approach.

6. REPORT (finish cleanly)
   - Summarize what was done, what files changed, test results.
   - Use git_diff to show the final state of changes.

═══════════════════════════════════════════════════════════════════════
 ERROR RECOVERY — When things go wrong
═══════════════════════════════════════════════════════════════════════

- Command fails: Read the error output. Fix the issue. Retry.
- Edit fails (stale hash): Re-read the file with file_read, get the
  new sha256, then retry the edit.
- Test fails: Read the test output, understand the failure, fix the
  code, re-run tests. Do NOT skip failing tests.
- Build fails: Read the error, fix, rebuild. Check for missing imports,
  syntax errors, or type mismatches.
- If you fail 3 times on the same step: STOP. Re-read AGENTS.md, re-
  explore the codebase, try a completely different approach.

═══════════════════════════════════════════════════════════════════════
 TOOL USAGE BEST PRACTICES
═══════════════════════════════════════════════════════════════════════

file_read:
  - Always read before writing. Use line numbers to find target code.
  - Read the full file for small files (<200 lines), or use offset/
    max_lines for larger files.

file_replace:
  - Use EXACT text from the file. Include enough context to be unique.
  - Always pass expected_sha256 from your last read.

file_write:
  - For new files: no sha256 needed.
  - For existing files: MUST include expected_sha256 from last read.

process_run:
  - Use argument arrays, not shell strings: ["npm", "run", "test"]
  - Set background=true for long-running processes (web servers).
  - Check the exit_code in the result — 0 means success.

code_search:
  - Use regex for precise search: "def my_function" not "my function"
  - Search before writing to avoid duplicating existing code.

workspace_tree:
  - Use max_depth=2 for quick overview, max_depth=4 for deep dive.

═══════════════════════════════════════════════════════════════════════
 SECURITY
═══════════════════════════════════════════════════════════════════════

- All paths are workspace-relative. The gateway rejects absolute paths,
  '..' traversal, symlink escapes, and anything outside allowed roots.
- process_run runs with OS privileges. Only use when the operator
  enabled commands.
- Never claim a file changed without a read-back or git_diff proof.
- No git operation commits, pushes, resets, or overwrites. Those are
  operator-only actions.
"""


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