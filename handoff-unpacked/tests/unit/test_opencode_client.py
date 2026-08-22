"""Unit tests for the OpenCode HTTP client using a mock transport."""

from __future__ import annotations

import httpx
import pytest

from agent_gateway.config import Config
from agent_gateway.errors import (
    BackendHTTPError,
    ExecutorUnavailableError,
    InvalidSessionError,
    MalformedResponseError,
    PermissionGatewayError,
)
from agent_gateway.executors.opencode.client import OpenCodeClient


def make_client(
    handler, *, password: str = "", base_url: str = "http://127.0.0.1:4096"
) -> OpenCodeClient:
    config = Config.from_env(
        {
            "OPENCODE_URL": base_url,
            "OPENCODE_PASSWORD": password,
        }
    )
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        transport=transport, base_url=base_url
    )
    return OpenCodeClient(config, http_client=http_client)


async def test_health_ok() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"healthy": True, "version": "1.18.18"})

    client = make_client(handler)
    health = await client.health()
    assert health.healthy is True
    assert health.version == "1.18.18"
    await client.close()


async def test_health_backend_down() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = make_client(handler)
    with pytest.raises(ExecutorUnavailableError):
        await client.health()
    await client.close()


async def test_http_500_mapped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    client = make_client(handler)
    with pytest.raises(BackendHTTPError) as exc:
        await client.health()
    assert "HTTP 500" in str(exc.value)
    await client.close()


async def test_404_mapped_to_invalid_session() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"name": "NotFoundError", "data": {}})

    client = make_client(handler)
    with pytest.raises(InvalidSessionError):
        await client.session_detail("ses_missing")
    await client.close()


async def test_401_mapped_to_permission_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    client = make_client(handler)
    with pytest.raises(PermissionGatewayError):
        await client.health()
    await client.close()


async def test_timeout_mapped() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    client = make_client(handler)
    with pytest.raises(Exception) as exc:
        await client.health()
    from agent_gateway.errors import GatewayTimeoutError

    assert isinstance(exc.value, GatewayTimeoutError)
    await client.close()


async def test_malformed_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"healthy": "not-a-bool"})

    client = make_client(handler)
    with pytest.raises(MalformedResponseError):
        await client.health()
    await client.close()


async def test_basic_auth_header_sent_when_password_set() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"healthy": True, "version": "1"})

    client = make_client(handler, password="hunter2")
    await client.health()
    await client.close()
    assert seen
    auth_header = seen[0].headers.get("Authorization", "")
    assert auth_header.startswith("Basic ")


async def test_no_auth_header_when_password_empty() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"healthy": True, "version": "1"})

    client = make_client(handler, password="")
    await client.health()
    await client.close()
    assert "Authorization" not in seen[0].headers


async def test_send_prompt_204_and_body() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["json"] = request.content
        return httpx.Response(204)

    client = make_client(handler)
    await client.send_prompt("ses_abc", "do the thing")
    await client.close()
    assert "ses_abc" in captured["url"]
    assert "prompt_async" in captured["url"]
    assert b'"text"' in captured["json"]


async def test_create_session_posts_directory() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(
            200,
            json={"id": "ses_new", "title": "T", "directory": "C:/x"},
        )

    client = make_client(handler)
    session = await client.create_session("C:/x")
    await client.close()
    assert "directory=C%3A%2Fx" in captured["url"].replace("%3A", "%3A")
    assert session.id == "ses_new"


async def test_pending_permissions_parsed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "id": "per_1",
                    "sessionID": "ses_1",
                    "permission": "bash",
                    "patterns": ["*"],
                    "metadata": {},
                    "always": [],
                }
            ],
        )

    client = make_client(handler)
    requests = await client.pending_permissions()
    await client.close()
    assert requests[0].id == "per_1"
    assert requests[0].permission == "bash"


async def test_reply_permission_posts_reply() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = request.content
        return httpx.Response(200, json=True)

    client = make_client(handler)
    result = await client.reply_permission("per_1", "reject")
    await client.close()
    assert result is True
    assert b'"reject"' in captured["json"]


async def test_agents_list_parsed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "name": "build",
                    "mode": "primary",
                    "hidden": False,
                    "permission": [],
                    "options": {},
                }
            ],
        )

    client = make_client(handler)
    agents = await client.agents()
    await client.close()
    assert agents[0].name == "build"