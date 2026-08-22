"""OpenCode write-task integration test (OPTIONAL mode).

Proves that in OpenCode agent mode a delegated task that actually writes
files produces a non-empty message-scoped diff, and that completion is
detected through message state (not just an empty status map).

Runs only when ENABLE_OPENCODE_AGENT=true and the backend is reachable;
it works on a COPY of the sample repo, never the original.
"""

from __future__ import annotations

import asyncio
import shutil

import pytest

from agent_gateway.config import Config
from agent_gateway.executors import build_executors
from agent_gateway.security.paths import PathPolicy
from agent_gateway.services.delegation import DelegationService
from conftest import REPO_ROOT, needs_backend, needs_opencode_mode, needs_repo

pytestmark = [pytest.mark.integration, needs_backend, needs_repo, needs_opencode_mode]


@pytest.fixture
def copy_repo(tmp_path) -> str:
    target = tmp_path / "work-repo"
    shutil.copytree(REPO_ROOT, target)
    return str(target)


async def test_delegated_write_task_produces_diff_and_completes(
    copy_repo: str,
) -> None:
    config = Config.from_env(
        {
            "OPENCODE_URL": "http://127.0.0.1:4096",
            "AGENT_ALLOWED_ROOTS": copy_repo,
        }
    )
    delegation = DelegationService(
        build_executors(config), PathPolicy(config.allowed_roots),
        opencode_enabled=True,
    )

    result = await delegation.start_task(
        "opencode",
        (
            "Create a new file notes.txt whose content is exactly "
            "'created by gateway write-task test'. Do not modify any "
            "other file."
        ),
        copy_repo,
    )
    session_id = result["session_id"]
    assert session_id.startswith("ses")

    for _ in range(120):
        status = await delegation.status("opencode", session_id)
        if status.get("completed") is True:
            break
        await asyncio.sleep(1)
    else:
        pytest.fail("Write task did not complete in time")

    # The message-scoped diff snapshot settles right after the final
    # "stop" message; poll briefly for it.
    write_diffs: list[dict] = []
    for _ in range(10):
        diffs = await delegation.diff("opencode", session_id)
        write_diffs = [d for d in diffs if d["file"].endswith("notes.txt")]
        if write_diffs:
            break
        await asyncio.sleep(1)
    assert write_diffs, f"Expected a notes.txt diff, got {diffs}"
    assert write_diffs[0]["additions"] > 0

    import pathlib

    notes = pathlib.Path(copy_repo) / "notes.txt"
    assert notes.exists()
    assert notes.read_text(encoding="utf-8").strip() == (
        "created by gateway write-task test"
    )