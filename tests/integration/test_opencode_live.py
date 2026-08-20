"""Integration tests against the live local OpenCode backend (OPTIONAL).

Requires ENABLE_OPENCODE_AGENT=true and OpenCode reachable at
127.0.0.1:4096. Skipped by default: the V2 direct-mode suite must not
require OpenCode or any model.
"""

from __future__ import annotations

import asyncio

import pytest

from agent_gateway.config import Config
from agent_gateway.errors import InvalidSessionError
from agent_gateway.executors.opencode.executor import OpenCodeExecutor
from conftest import needs_backend, needs_opencode_mode

pytestmark = [needs_backend, needs_opencode_mode]


@pytest.fixture
def executor() -> OpenCodeExecutor:
    return OpenCodeExecutor(Config.from_env({}))


async def test_health_live(executor: OpenCodeExecutor) -> None:
    health = await executor.health()
    assert health.available is True
    assert health.healthy is True
    assert health.version


async def test_session_lifecycle_live(executor: OpenCodeExecutor) -> None:
    session = await executor.create_session(
        __import__("pathlib").Path("C:/Users/dev/Desktop/sample-repo")
    )
    assert session.id.startswith("ses")

    await executor.send_prompt(
        session.id, "Reply with the single word OK. Do not modify files."
    )

    for _ in range(30):
        status = await executor.status(session.id)
        if status.state == "idle":
            break
        await asyncio.sleep(1)
    else:
        pytest.fail("Session did not become idle within 30s")

    messages = await executor.messages(session.id, limit=10)
    assert messages
    text = " ".join(
        part.get("text", "")
        for m in messages
        for part in m.parts
        if part.get("type") == "text"
    )
    assert "OK" in text

    diffs = await executor.diff(session.id)
    assert isinstance(diffs, list)

    info = await executor.session_info(session.id)
    assert info.id == session.id

    aborted = await executor.abort(session.id)
    assert isinstance(aborted, bool)


async def test_invalid_session_rejected(executor: OpenCodeExecutor) -> None:
    with pytest.raises(InvalidSessionError):
        await executor.status("ses_definitely_missing_000")


async def test_pending_permissions_live(executor: OpenCodeExecutor) -> None:
    pending = await executor.pending_permissions()
    assert isinstance(pending, list)


async def test_agents_and_providers_live(executor: OpenCodeExecutor) -> None:
    agents = await executor.list_agents()
    assert agents
    providers = await executor.list_providers()
    assert "providers" in providers