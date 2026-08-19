"""Shared pytest fixtures and helpers."""

from __future__ import annotations

import socket
from pathlib import Path

import httpx
import pytest

from agent_gateway.config import Config
from agent_gateway.security.paths import PathPolicy

REPO_ROOT = Path("C:/Users/dev/Desktop/sample-repo")


def make_config(open_url: str = "http://127.0.0.1:4096", **overrides) -> Config:
    env = {
        "OPENCODE_URL": open_url,
        "AGENT_ALLOWED_ROOTS": overrides.pop(
            "allowed_roots", ""
        ),
        "MCP_PORT": str(overrides.pop("mcp_port", 8000)),
    }
    env.update({k: str(v) for k, v in overrides.items()})
    return Config.from_env(env)


@pytest.fixture
def path_policy() -> PathPolicy:
    return PathPolicy([REPO_ROOT])


def backend_reachable(timeout: float = 2.0) -> bool:
    import asyncio

    async def _check() -> bool:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                "http://127.0.0.1:4096/global/health"
            )
            return response.status_code == 200

    try:
        return asyncio.run(_check())
    except Exception:
        return False


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


needs_backend = pytest.mark.skipif(
    not backend_reachable(),
    reason="OpenCode backend not reachable at 127.0.0.1:4096",
)

needs_repo = pytest.mark.skipif(
    not REPO_ROOT.is_dir(),
    reason=f"Sample repo not present at {REPO_ROOT}",
)