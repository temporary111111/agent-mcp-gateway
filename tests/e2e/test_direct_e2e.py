"""V2 direct-mode end-to-end test.

Proves the exact GPT-facing loop with NO OpenCode server and NO
model/provider:

    workspace_open -> file_read -> file_write/file_apply_patch
    -> process_run (verification) -> git_diff -> exact patch verified

The gateway runs as a real Streamable HTTP process; the MCP client
library drives it exactly as ChatGPT would.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

pytestmark = [pytest.mark.e2e]

TOKEN = "e2e-test-token-1234567890"


def _git(root: Path, *args: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr


@pytest.fixture
def work_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "work"
    repo.mkdir()
    (repo / "app.py").write_text(
        "def add(a, b):\n    return a + b\n\n"
        "def main():\n    print(add(2, 3))\n",
        encoding="utf-8",
    )
    (repo / "README.md").write_text("# Work Repo\n", encoding="utf-8")
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "e2e@example.com")
    _git(repo, "config", "user.name", "E2E")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial commit")
    return repo


async def wait_for_port(port: int, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            async with httpx.AsyncClient(timeout=1.0) as client:
                response = await client.get(
                    f"http://127.0.0.1:{port}/mcp",
                    headers={
                        "Accept": "application/json",
                        "Authorization": f"Bearer {TOKEN}",
                    },
                )
                if response.status_code < 500:
                    return True
        except Exception:
            pass
        await asyncio.sleep(0.3)
    return False


def _free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _drain(stream, sink: list[str]) -> None:
    for line in iter(stream.readline, b""):
        sink.append(line.decode(errors="replace").rstrip())


def _spawn_server(port: int, repo: Path) -> subprocess.Popen:
    env = dict(os.environ)
    env.update(
        {
            "MCP_PORT": str(port),
            "MCP_HOST": "127.0.0.1",
            "AGENT_ALLOWED_ROOTS": str(repo),
            "AGENT_ENABLE_COMMANDS": "true",
            "AGENT_GATEWAY_TOKEN": TOKEN,
            "LOG_LEVEL": "WARNING",
        }
    )
    env.pop("ENABLE_OPENCODE_AGENT", None)
    server = subprocess.Popen(
        [sys.executable, "-m", "agent_gateway.server"],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    threading.Thread(
        target=_drain, args=(server.stdout, []), daemon=True
    ).start()
    threading.Thread(
        target=_drain, args=(server.stderr, []), daemon=True
    ).start()
    return server


@pytest.mark.e2e
async def test_direct_mode_full_loop_no_opencode(
    work_repo: Path,
) -> None:
    port = _free_port()
    server = _spawn_server(port, work_repo)
    try:
        assert await wait_for_port(port), "Gateway server did not start"
        await _run_direct_loop(port, work_repo)
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)


async def _run_direct_loop(port: int, repo: Path) -> None:
    import httpx2

    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    url = f"http://127.0.0.1:{port}/mcp"

    async def call(session: ClientSession, name: str, args: dict):
        result = await session.call_tool(name, args)
        assert not result.is_error, f"{name} failed: {result.content}"
        return json.loads(result.content[0].text)

    # 1. Unauthenticated access is rejected with 401.
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(
            url, headers={"Accept": "application/json"}
        )
        assert response.status_code == 401

    # 2. Authenticated MCP session.
    auth_client = httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {TOKEN}"}, timeout=300.0
    )
    async with streamable_http_client(url, http_client=auth_client) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()

            tools_result = await session.list_tools()
            tool_names = {tool.name for tool in tools_result.tools}
            direct_expected = {
                "gateway_health",
                "agent_executors",
                "workspace_open",
                "workspace_tree",
                "file_read",
                "file_stat",
                "file_find",
                "code_search",
                "file_write",
                "file_replace",
                "file_apply_patch",
                "process_run",
                "git_status",
                "git_diff",
                "git_log",
                "git_show",
            }
            assert direct_expected.issubset(tool_names), (
                f"Missing direct tools: {direct_expected - tool_names}"
            )
            opencode_only = {
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
            assert not (opencode_only & tool_names), (
                "OpenCode agent tools must NOT be registered in direct mode"
            )

            # 3. Health shows direct mode, no executors.
            health = await call(session, "gateway_health", {})
            assert health["mode"] == "direct"
            assert health["opencode_agent_enabled"] is False
            assert health["executors"] == []

            # 4. workspace_open
            opened = await call(
                session, "workspace_open", {"directory": str(repo)}
            )
            ws = opened["workspace_id"]
            assert ws.startswith("ws_")

            # 5. file_read + hash for the patch precondition
            read_result = await call(
                session, "file_read", {"workspace_id": ws, "path": "app.py"}
            )
            assert read_result["binary"] is False
            assert "def add(a, b):" in read_result["content"]
            sha_before = read_result["sha256"]

            # 6. file_apply_patch: exact deterministic edit
            patch = (
                "--- a/app.py\n+++ b/app.py\n"
                "@@ -1,4 +1,4 @@\n def add(a, b):\n"
                "-    return a + b\n+    return a + b  # fixed\n"
                " \n def main():\n"
            )
            patched = await call(
                session,
                "file_apply_patch",
                {
                    "workspace_id": ws,
                    "path": "app.py",
                    "patch": patch,
                    "expected_sha256": sha_before,
                },
            )
            assert patched["before_sha256"] == sha_before
            assert patched["hunks"] == 1

            # 7. file_write creates a new file
            written = await call(
                session,
                "file_write",
                {
                    "workspace_id": ws,
                    "path": "verify_me.txt",
                    "content": "patched-by-gateway-e2e\n",
                },
            )
            assert written["created"] is True

            # 8. process_run: deterministic verification of the patch
            verify = (
                "import pathlib;"
                "t = pathlib.Path('app.py').read_text(encoding='utf-8');"
                "assert '# fixed' in t;"
                "assert pathlib.Path('verify_me.txt').exists();"
                "print('VERIFIED')"
            )
            proc = await call(
                session,
                "process_run",
                {
                    "workspace_id": ws,
                    "executable": sys.executable,
                    "args": ["-c", verify],
                    "timeout_seconds": 30,
                },
            )
            assert proc["exit_code"] == 0, proc
            assert "VERIFIED" in proc["stdout"]
            assert proc["timed_out"] is False

            # 9. git_diff: exact patch visible, untracked file listed
            diff_result = await call(
                session, "git_diff", {"workspace_id": ws}
            )
            assert "app.py" in diff_result["files"]
            assert "# fixed" in diff_result["diff"]
            assert "-    return a + b" in diff_result["diff"]

            status_result = await call(
                session, "git_status", {"workspace_id": ws}
            )
            assert status_result["clean"] is False
            untracked = [
                e for e in status_result["entries"] if e["path"] == "verify_me.txt"
            ]
            assert untracked and untracked[0]["x"] == "?"

            # 10. Unauthorized directory is rejected.
            bad_open = await session.call_tool(
                "workspace_open", {"directory": "C:/Windows"}
            )
            assert bad_open.is_error

            # 11. Traversal is rejected.
            bad_read = await session.call_tool(
                "file_read", {"workspace_id": ws, "path": "../README.md"}
            )
            assert bad_read.is_error


def _spawn_server_commands_disabled(port: int, repo: Path) -> subprocess.Popen:
    env = dict(os.environ)
    env.update(
        {
            "MCP_PORT": str(port),
            "MCP_HOST": "127.0.0.1",
            "AGENT_ALLOWED_ROOTS": str(repo),
            "AGENT_GATEWAY_TOKEN": TOKEN,
            "LOG_LEVEL": "WARNING",
        }
    )
    env.pop("ENABLE_OPENCODE_AGENT", None)
    env.pop("AGENT_ENABLE_COMMANDS", None)
    server = subprocess.Popen(
        [sys.executable, "-m", "agent_gateway.server"],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    threading.Thread(
        target=_drain, args=(server.stdout, []), daemon=True
    ).start()
    threading.Thread(
        target=_drain, args=(server.stderr, []), daemon=True
    ).start()
    return server


@pytest.mark.e2e
async def test_commands_disabled_process_run_absent(
    work_repo: Path,
) -> None:
    port = _free_port()
    server = _spawn_server_commands_disabled(port, work_repo)
    try:
        assert await wait_for_port(port), "Gateway server did not start"

        import httpx2

        from mcp.client.session import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        url = f"http://127.0.0.1:{port}/mcp"
        auth_client = httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30.0
        )
        async with streamable_http_client(url, http_client=auth_client) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()

                tools_result = await session.list_tools()
                tool_names = {tool.name for tool in tools_result.tools}

                assert "process_run" not in tool_names, (
                    "process_run must NOT be registered when "
                    "AGENT_ENABLE_COMMANDS is not set"
                )

                health_result = await session.call_tool("gateway_health", {})
                health = json.loads(health_result.content[0].text)
                assert health["commands_enabled"] is False
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)