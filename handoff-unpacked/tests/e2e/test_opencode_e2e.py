"""OpenCode agent-mode end-to-end tests (OPTIONAL, disabled by default).

These tests require ENABLE_OPENCODE_AGENT=true plus a live OpenCode
server and the sample repository. They are never part of the direct-mode
suite: direct mode requires no OpenCode and no model.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

from agent_gateway.config import Config
from agent_gateway.executors import build_executors
from agent_gateway.security.paths import PathPolicy
from agent_gateway.services.delegation import DelegationService
from conftest import REPO_ROOT, free_port, needs_backend, needs_opencode_mode, needs_repo

pytestmark = [pytest.mark.e2e, needs_backend, needs_repo, needs_opencode_mode]


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
    delegation = DelegationService(
        build_executors(config), path_policy, opencode_enabled=True
    )

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
    for _ in range(300):
        status = await delegation.status("opencode", session_id)
        state = status["state"]
        if status.get("completed") is True:
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


def drain(stream, sink: list[str]) -> None:
    """Reader thread: keeps the child's stdout/stderr pipes drained.

    The gateway writes diagnostics (uvicorn banner, httpx request logs,
    SDK session-manager notes) to stdout/stderr. If nobody consumes the
    pipes, the Windows pipe buffer (~4 KiB) fills up and the child's
    event loop blocks on the next write, freezing the whole server.
    """
    for line in iter(stream.readline, b""):
        sink.append(line.decode(errors="replace").rstrip())


def spawn_gateway(port: int, env: dict) -> tuple[subprocess.Popen, list[str], list[str]]:
    server = subprocess.Popen(
        [sys.executable, "-m", "agent_gateway.server"],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    out: list[str] = []
    err: list[str] = []
    threading.Thread(target=drain, args=(server.stdout, out), daemon=True).start()
    threading.Thread(target=drain, args=(server.stderr, err), daemon=True).start()
    return server, out, err


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
            "ENABLE_OPENCODE_AGENT": "true",
            "LOG_LEVEL": "WARNING",
        }
    )
    server, out, err = spawn_gateway(port, env)
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


class RawMcp:
    """Minimal JSON-RPC-over-HTTP client for a stateless json_response server.

    The gateway's MCP endpoint runs with stateless_http=True and
    json_response=True: every request is a standalone POST that returns the
    JSON-RPC result inline (no SSE session, no notification stream). Driving
    the test with raw POSTs exercises the exact wire contract a compliant
    client uses, without the SDK client's idle SSE read that would otherwise
    time out while the delegated agent works for minutes.
    """

    def __init__(self, url: str, timeout: float = 60.0) -> None:
        import httpx2

        self._client = httpx2.AsyncClient(timeout=timeout)
        self._url = url
        self._id = 0

    async def call(self, method: str, params: dict) -> dict:
        self._id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._id,
            "method": method,
            "params": params,
        }
        response = await self._client.post(self._url, json=payload)
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()


def _result(body: dict) -> dict:
    return body.get("result", {})


def _failed(body: dict) -> bool:
    return "error" in body or bool(body.get("result", {}).get("isError"))


def _text(result: dict) -> str:
    for entry in result.get("content", []):
        if entry.get("type") == "text":
            return entry.get("text", "")
    return ""


async def run_mcp_client_flow(port: int) -> None:
    import json

    mcp = RawMcp(f"http://127.0.0.1:{port}/mcp")
    try:
        init = await mcp.call(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "e2e-raw", "version": "0"},
            },
        )
        assert "capabilities" in _result(init)

        listed = await mcp.call("tools/list", {})
        tool_names = {tool["name"] for tool in _result(listed).get("tools", [])}
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
            "workspace_open",
            "file_read",
            "process_run",
            "git_diff",
        }
        assert expected.issubset(tool_names), f"Missing tools: {expected - tool_names}"

        health = await mcp.call(
            "tools/call", {"name": "gateway_health", "arguments": {}}
        )
        assert not _failed(health)
        assert "opencode_agent_enabled" in _text(_result(health))

        start = await mcp.call(
            "tools/call",
            {
                "name": "agent_start_task",
                "arguments": {
                    "executor": "opencode",
                    "task": (
                        "List the files in this repository and say the word "
                        "FINISHED. Do not modify anything."
                    ),
                    "directory": str(REPO_ROOT),
                },
            },
        )
        assert not _failed(start), _result(start)
        start_payload = json.loads(_text(_result(start)))
        session_id = start_payload["session_id"]
        assert session_id.startswith("ses_")

        state = None
        for _ in range(300):
            status = await mcp.call(
                "tools/call",
                {
                    "name": "agent_status",
                    "arguments": {"executor": "opencode", "session_id": session_id},
                },
            )
            assert not _failed(status), _result(status)
            status_payload = json.loads(_text(_result(status)))
            state = status_payload["state"]
            if status_payload.get("completed") is True:
                break
            await asyncio.sleep(1)
        else:
            pytest.fail(f"MCP task did not finish; last state {state!r}")

        messages = await mcp.call(
            "tools/call",
            {
                "name": "agent_messages",
                "arguments": {
                    "executor": "opencode",
                    "session_id": session_id,
                    "limit": 20,
                },
            },
        )
        assert not _failed(messages), _result(messages)
        assert "FINISHED" in _text(_result(messages))

        diff = await mcp.call(
            "tools/call",
            {
                "name": "agent_diff",
                "arguments": {"executor": "opencode", "session_id": session_id},
            },
        )
        assert not _failed(diff), _result(diff)

        bad_status = await mcp.call(
            "tools/call",
            {
                "name": "agent_status",
                "arguments": {"executor": "opencode", "session_id": "ses_nonexistent"},
            },
        )
        assert _failed(bad_status)

        bad_dir = await mcp.call(
            "tools/call",
            {
                "name": "agent_start_task",
                "arguments": {
                    "executor": "opencode",
                    "task": "touch nothing",
                    "directory": "C:/Windows",
                },
            },
        )
        assert _failed(bad_dir)
    finally:
        await mcp.close()