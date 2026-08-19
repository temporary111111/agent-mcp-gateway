"""Unit tests for the DelegationService orchestration layer."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_gateway.errors import (
    ExecutorUnavailableError,
    InvalidRequestError,
    UnauthorizedDirectoryError,
)
from agent_gateway.executors.base import (
    Executor,
    ExecutorHealth,
    MessageInfo,
    SessionInfo,
    SessionStatusInfo,
)
from agent_gateway.security.paths import PathPolicy
from agent_gateway.services.delegation import DelegationService

ROOT = Path("C:/Users/dev/Desktop/sample-repo")


class FakeExecutor(Executor):
    name = "fake"

    def __init__(self) -> None:
        self.created: list[SessionInfo] = []
        self.sent: list[tuple[str, str]] = []
        self.status_override: SessionStatusInfo | None = None

    async def health(self) -> ExecutorHealth:
        return ExecutorHealth(available=True, healthy=True, version="0.1")

    async def create_session(self, directory, *, title=None, agent=None):
        info = SessionInfo(id="ses_fake", directory=str(directory), title=title)
        self.created.append(info)
        return info

    async def send_prompt(self, session_id, task, *, agent=None, directory=None):
        self.sent.append((session_id, task))

    async def status(self, session_id):
        return self.status_override or SessionStatusInfo(state="idle")

    async def session_info(self, session_id):
        return SessionInfo(id=session_id, directory=str(ROOT))

    async def messages(self, session_id, *, limit=50, before=None):
        return [MessageInfo(id="msg_1", role="assistant", parts=[{"type": "text", "text": "ok"}])]

    async def diff(self, session_id, *, message_id=None):
        return []

    async def abort(self, session_id):
        return True

    async def pending_permissions(self):
        return []

    async def reply_permission(self, request_id, reply, *, message=None):
        return True


@pytest.fixture
def service() -> DelegationService:
    executors = {"fake": FakeExecutor()}
    return DelegationService(executors, PathPolicy([ROOT]))


async def test_unknown_executor_rejected(service: DelegationService) -> None:
    with pytest.raises(ExecutorUnavailableError):
        await service.start_task("nope", "task", str(ROOT))


async def test_unauthorized_directory_rejected(service: DelegationService) -> None:
    with pytest.raises(UnauthorizedDirectoryError):
        await service.start_task("fake", "task", "C:/Users/dev/Desktop")


async def test_start_task_happy_path(service: DelegationService) -> None:
    result = await service.start_task("fake", "do stuff", str(ROOT))
    assert result["session_id"] == "ses_fake"
    assert result["status"] == "running"
    executor = service.executor("fake")
    assert executor.created
    assert executor.sent == [("ses_fake", "do stuff")]
    assert service.lookup("ses_fake") is not None


async def test_start_task_empty_task_rejected(service: DelegationService) -> None:
    with pytest.raises(InvalidRequestError):
        await service.start_task("fake", "  ", str(ROOT))


async def test_continue_uses_remembered_directory(
    service: DelegationService,
) -> None:
    await service.start_task("fake", "first", str(ROOT))
    result = await service.continue_task("fake", "ses_fake", "second")
    assert result["session_id"] == "ses_fake"
    assert result["directory"] == str(ROOT)


async def test_status_includes_pending_permissions(
    service: DelegationService,
) -> None:
    from agent_gateway.executors.base import PermissionRequestInfo

    class PermExecutor(FakeExecutor):
        async def pending_permissions(self):
            return [
                PermissionRequestInfo(
                    id="per_1",
                    session_id="ses_fake",
                    permission="bash",
                    patterns=["*"],
                )
            ]

    service._executors["fake"] = PermExecutor()
    status = await service.status("fake", "ses_fake")
    assert status["state"] == "idle"
    assert status["pending_permissions"] == [
        {"id": "per_1", "permission": "bash", "patterns": ["*"]}
    ]


async def test_permission_reply_validation(service: DelegationService) -> None:
    with pytest.raises(InvalidRequestError):
        await service.reply_permission("fake", "per_1", "maybe")
    result = await service.reply_permission("fake", "per_1", "reject")
    assert result["processed"] is True


async def test_messages_and_diff(service: DelegationService) -> None:
    messages = await service.messages("fake", "ses_fake")
    assert messages[0]["role"] == "assistant"
    diffs = await service.diff("fake", "ses_fake")
    assert diffs == []


async def test_gateway_health(service: DelegationService) -> None:
    health = await service.gateway_health()
    assert health["status"] == "ok"
    assert health["executors"][0]["executor"] == "fake"
    assert str(ROOT) in health["allowed_roots"]


async def test_list_executors(service: DelegationService) -> None:
    listing = await service.list_executors()
    assert listing == [{"executor": "fake", "capabilities": []}]