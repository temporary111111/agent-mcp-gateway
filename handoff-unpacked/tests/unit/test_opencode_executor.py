"""Unit tests for the OpenCode executor (via a mock client)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_gateway.config import Config
from agent_gateway.errors import (
    InvalidRequestError,
    InvalidSessionError,
    SessionBusyError,
)
from agent_gateway.executors.opencode.executor import (
    OpenCodeExecutor,
    validate_message_id,
    validate_permission_reply,
    validate_permission_id,
    validate_session_id,
)
from agent_gateway.executors.opencode.models import (
    Health,
    MessageEntry,
    PermissionRequest,
    SessionDetail,
    SessionStatus,
    SnapshotFileDiff,
)

DIR = Path("C:/Users/dev/Desktop/sample-repo")


class FakeClient:
    def __init__(self) -> None:
        self.health_payload = Health(healthy=True, version="1.18.18")
        self.session_payload = SessionDetail(
            id="ses_abc", title="T", directory=str(DIR), agent="build"
        )
        self.status_map: dict[str, SessionStatus] = {}
        self.messages_payload: list[MessageEntry] = []
        self.diff_payload: list[SnapshotFileDiff] = []
        self.permissions_payload: list[PermissionRequest] = []
        self.reply_result = True
        self.abort_result = True
        self.calls: list[str] = []

    async def health(self) -> Health:
        self.calls.append("health")
        return self.health_payload

    async def create_session(self, directory, *, title=None, agent=None):
        self.calls.append("create_session")
        return self.session_payload

    async def send_prompt(self, session_id, task, *, agent=None, directory=None):
        self.calls.append("send_prompt")

    async def session_status_map(self, directory=None):
        self.calls.append("session_status_map")
        return self.status_map

    async def session_detail(self, session_id):
        self.calls.append("session_detail")
        return self.session_payload

    async def messages(self, session_id, *, limit=50, before=None):
        self.calls.append("messages")
        return self.messages_payload

    async def diff(self, session_id, *, message_id=None):
        self.calls.append("diff")
        return self.diff_payload

    async def abort(self, session_id):
        self.calls.append("abort")
        return self.abort_result

    async def pending_permissions(self, directory=None):
        self.calls.append("pending_permissions")
        return self.permissions_payload

    async def reply_permission(self, request_id, reply, *, message=None):
        self.calls.append("reply_permission")
        return self.reply_result

    async def agents(self):
        return []

    async def providers(self):
        from agent_gateway.executors.opencode.models import ProviderList

        return ProviderList(all=[], default={}, connected=[])


@pytest.fixture
def executor() -> OpenCodeExecutor:
    config = Config.from_env({})
    return OpenCodeExecutor(config, client=FakeClient())


async def test_health(executor: OpenCodeExecutor) -> None:
    health = await executor.health()
    assert health.available is True
    assert health.healthy is True
    assert health.version == "1.18.18"


async def test_create_session(executor: OpenCodeExecutor) -> None:
    session = await executor.create_session(DIR)
    assert session.id == "ses_abc"
    assert session.directory == DIR.as_posix()


async def test_send_prompt_empty_task_rejected(executor: OpenCodeExecutor) -> None:
    with pytest.raises(InvalidRequestError):
        await executor.send_prompt("ses_abc", "   ")


async def test_status_busy_and_idle(executor: OpenCodeExecutor) -> None:
    executor._client.status_map = {"ses_abc": SessionStatus(type="busy")}
    status = await executor.status("ses_abc")
    assert status.state == "busy"

    executor._client.status_map = {}
    status = await executor.status("ses_abc")
    assert status.state == "idle"


async def test_invalid_session_id_rejected(executor: OpenCodeExecutor) -> None:
    with pytest.raises(InvalidSessionError):
        await executor.status("nope")


def test_validate_ids() -> None:
    assert validate_session_id("ses_x") == "ses_x"
    assert validate_message_id("msg_x") == "msg_x"
    assert validate_permission_id("per_x") == "per_x"
    with pytest.raises(InvalidSessionError):
        validate_session_id("bad")
    with pytest.raises(InvalidRequestError):
        validate_message_id("bad")
    with pytest.raises(InvalidRequestError):
        validate_permission_id("bad")


def test_validate_permission_reply() -> None:
    assert validate_permission_reply("once") == "once"
    assert validate_permission_reply("always") == "always"
    assert validate_permission_reply("reject") == "reject"
    with pytest.raises(InvalidRequestError):
        validate_permission_reply("maybe")


async def test_messages_rendered(executor: OpenCodeExecutor) -> None:
    executor._client.messages_payload = [
        MessageEntry(
            info={
                "id": "msg_1",
                "role": "assistant",
                "agent": "build",
                "modelID": "m",
                "finish": "stop",
                "time": {"created": 123},
            },
            parts=[
                {"type": "text", "text": "hello"},
                {"type": "tool", "tool": "bash", "callID": "c1"},
                {"type": "step-start"},
            ],
        )
    ]
    messages = await executor.messages("ses_abc")
    assert messages[0].role == "assistant"
    assert messages[0].parts[0] == {"type": "text", "text": "hello"}
    assert messages[0].parts[1]["tool"] == "bash"


def _assistant_entry(finish: str | None) -> MessageEntry:
    return MessageEntry(
        info={"id": "msg_x", "role": "assistant", "finish": finish},
        parts=[],
    )


async def test_is_completed_requires_stop_finish(
    executor: OpenCodeExecutor,
) -> None:
    client = executor._client
    client.status_map = {"ses_abc": SessionStatus(type="busy")}
    assert await executor.is_completed("ses_abc") is False

    client.status_map = {"ses_abc": SessionStatus(type="idle")}
    client.messages_payload = [_assistant_entry("tool-calls"), _assistant_entry("stop")]
    assert await executor.is_completed("ses_abc") is True

    client.messages_payload = [_assistant_entry("tool-calls")]
    assert await executor.is_completed("ses_abc") is False

    client.messages_payload = [_assistant_entry("tool-calls"), _assistant_entry("stop")]
    assert await executor.is_completed("ses_abc") is True

    client.messages_payload = []
    assert await executor.is_completed("ses_abc") is True


async def test_diff_mapped(executor: OpenCodeExecutor) -> None:
    executor._client.diff_payload = [
        SnapshotFileDiff(
            file="a.py", status="modified", additions=2, deletions=1, patch="@@"
        )
    ]
    diffs = await executor.diff("ses_abc")
    assert diffs[0].file == "a.py"
    assert diffs[0].additions == 2


async def test_permissions_and_reply(executor: OpenCodeExecutor) -> None:
    executor._client.permissions_payload = [
        PermissionRequest(
            id="per_1",
            sessionID="ses_abc",
            permission="bash",
            patterns=["*"],
            metadata={},
            always=[],
        )
    ]
    pending = await executor.pending_permissions()
    assert pending[0].id == "per_1"
    assert pending[0].permission == "bash"

    assert await executor.reply_permission("per_1", "always") is True
    with pytest.raises(InvalidRequestError):
        await executor.reply_permission("per_1", "yolo")


async def test_abort(executor: OpenCodeExecutor) -> None:
    assert await executor.abort("ses_abc") is True


async def test_busy_mapping_on_prompt_failure(executor: OpenCodeExecutor) -> None:
    from agent_gateway.errors import BackendHTTPError

    async def failing_send_prompt(*args, **kwargs):
        raise BackendHTTPError("busy", status_code=400)

    executor._client.send_prompt = failing_send_prompt  # type: ignore[method-assign]
    executor._client.status_map = {"ses_abc": SessionStatus(type="busy")}
    with pytest.raises(SessionBusyError):
        await executor.send_prompt("ses_abc", "more")