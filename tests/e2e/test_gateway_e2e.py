"""End-to-end tests.

Two layers:

1. service_e2e - full DelegationService flow against the real OpenCode
   backend on the real sample repository, verifying the repository is NOT
   modified by a read-only task.

2. mcp_e2e - the complete stack: the gateway MCP server running as a real
   Streamable HTTP process on localhost, driven through the MCP client
   library (the same protocol ChatGPT uses).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from agent_gateway.config import Config, parse_allowed_roots
from agent_gateway.executors import build_executors
from agent_gateway.security.paths import PathPolicy
from agent_gateway.services.delegation import DelegationService
from conftest import REPO_ROOT, free_port, needs_backend, needs_repo

pytestmark = [needs_backend, needs_repo]


def snapshot_repo(root: Path) -> dict:
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            files[str(path.relative_to(root))] = (
                path.read_bytes(),
                path.stat().st_size,
                path.stat().st_mtime_ns,
            )
    return files


async def wait_for_port(port: int, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            async with httpx.AsyncClient(timeout=1.0) as client:
                response = await client.get(
                    f"http://127.0.0.1:{port}/mcp", headers={"Accept": "application/json"}
                )
                if response.status_code < 500:
                    return True
        except Exception:
            pass
        await asyncio.sleep(0.3)
    return False


@pytest.mark.e2e
async def test_readonly_delegated_task_does_not_modify_repo() -> None:
    config = Config.from_env(
        {
            "OPENCODE_URL": "http://127.0.0.1:4096",
            "AGENT_ALLOWED_ROOTS": str(REPO_ROOT),
        }
    )
    path_policy = PathPolicy(config.allowed_roots)
    delegation = DelegationService(build_executors(config), path_policy)

    before = snapshot_repo(REPO_ROOT)

    result = await delegation.start_task(
        "opencode",
        "Inspect the repository and summarize its structure. "
        "Do not modify any files.",
        str(REPO_ROOT),
    )
    session_id = result["session_id"]
    assert session_id.startswith("ses")

    state = None
    for _ in range(60):
        status = await delegation.status("opencode", session_id)
        state = status["state"]
        if state == "idle":
            break
        await asyncio.sleep(1)
    else:
        pytest.fail(f"Task did not finish; last state {state!r}")

    messages = await delegation.messages("opencode", session_id, limit=20)
    assert messages
    text = " ".join(
        part.get("text", "")
        for m in messages
        for part in m["parts"]
        if part.get("type") == "text"
    )
    assert "app.py" in text or "README" in text or "repo" in text.lower()

    diffs = await delegation.diff("opencode", session_id)
    assert isinstance(diffs, list)
    assert all(d["additions"] == 0 and d["deletions"] == 0 for d in diffs)

    await asyncio.sleep(1.0)
    after = snapshot_repo(REPO_ROOT)
    assert before == after, "Read-only delegated task modified the repository"


@pytest.mark.e2e
async def test_full_mcp_server_protocol() -> None:
    port = free_port()
    env = dict(os.environ)
    env.update(
        {
            "MCP_PORT": str(port),
            "MCP_HOST": "127.0.0.1",
            "OPENCODE_URL": "http://127.0.0.1:4096",
            "AGENT_ALLOWED_ROOTS": str(REPO_ROOT),
            "LOG_LEVEL": "WARNING",
        }
    )
    server = subprocess.Popen(
        [sys.executable, "-m", "agent_gateway.server"],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert await wait_for_port(port), "Gateway server did not start in time"
        await run_mcp_client_flow(port)
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)


async def run_mcp_client_flow(port: int) -> None:
    import json

    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with streamable_http_client(f"http://127.0.0.1:{port}/mcp") as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()

            tools_result = await session.list_tools()
            tool_names = {tool.name for tool in tools_result.tools}
            expected = {
                "gateway_health",
                "agent_executors",
                "agent_start_task",
                "agent_continue",
                "agent_status",
                "agent_session",
                "agent_messages",
                "agent_diff",
                "agent_abort",
                "agent_pending_permissions",
                "agent_reply_permission",
                "opencode_health",
                "opencode_agents",
                "opencode_providers",
            }
            assert expected.issubset(tool_names), (
                f"Missing tools: {expected - tool_names}"
            )

            health = await session.call_tool("gateway_health", {})
            assert not health.is_error
            assert "opencode" in health.content[0].text
            assert "healthy" in health.content[0].text

            start = await session.call_tool(
                "agent_start_task",
                {
                    "executor": "opencode",
                    "task": (
                        "List the files in this repository and say the word "
                        "FINISHED. Do not modify anything."
                    ),
                    "directory": str(REPO_ROOT),
                },
            )
            assert not start.is_error
            start_payload = json.loads(start.content[0].text)
            session_id = start_payload["session_id"]
            assert session_id.startswith("ses_")

            state = None
            for _ in range(60):
                status = await session.call_tool(
                    "agent_status",
                    {"executor": "opencode", "session_id": session_id},
                )
                assert not status.is_error
                status_payload = json.loads(status.content[0].text)
                state = status_payload["state"]
                if state == "idle":
                    break
                await asyncio.sleep(1)
            else:
                pytest.fail(f"MCP task did not finish; last state {state!r}")

            messages = await session.call_tool(
                "agent_messages",
                {"executor": "opencode", "session_id": session_id, "limit": 20},
            )
            assert not messages.is_error
            messages_text = messages.content[0].text
            assert "FINISHED" in messages_text

            diff = await session.call_tool(
                "agent_diff",
                {"executor": "opencode", "session_id": session_id},
            )
            assert not diff.is_error

            bad_status = await session.call_tool(
                "agent_status",
                {"executor": "opencode", "session_id": "ses_nonexistent"},
            )
            assert bad_status.is_error

            bad_dir = await session.call_tool(
                "agent_start_task",
                {
                    "executor": "opencode",
                    "task": "touch nothing",
                    "directory": "C:/Windows",
                },
            )
            assert bad_dir.is_error
