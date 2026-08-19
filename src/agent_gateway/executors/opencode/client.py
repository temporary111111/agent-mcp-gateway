"""HTTP client for the OpenCode headless server.

Centralizes base URL, optional Basic auth, timeouts, and error mapping.
Credentials are never logged. All responses are parsed into typed models
(or validated dicts) so callers never touch raw httpx.
"""

from __future__ import annotations

from typing import Any

import httpx
import pydantic

from ...config import Config
from ...errors import MalformedResponseError
from ...logging import get_logger
from . import errors as opencode_errors
from .models import (
    AgentInfo,
    Health,
    MessageEntry,
    PermissionRequest,
    ProviderList,
    SessionDetail,
    SessionStatus,
    SnapshotFileDiff,
)

logger = get_logger("opencode.client")

_SESSION_ID_PATTERN = "^ses"


class OpenCodeClient:
    def __init__(
        self,
        config: Config,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        timeout = httpx.Timeout(
            connect=config.opencode_connect_timeout,
            read=config.opencode_read_timeout,
            write=config.opencode_write_timeout,
            pool=config.opencode_pool_timeout,
        )
        auth: httpx.Auth | None = None
        if config.auth_enabled():
            auth = httpx.BasicAuth(
                config.opencode_username or "opencode",
                config.opencode_password,
            )
        self._config = config
        self._auth = auth
        self._client = http_client or httpx.AsyncClient(
            base_url=config.opencode_url,
            timeout=timeout,
            headers={"Accept": "application/json"},
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        context: str,
        expected_status: tuple[int, ...] = (200,),
    ) -> httpx.Response:
        try:
            response = await self._client.request(
                method,
                path,
                params=params,
                json=json_body,
                auth=self._auth,
            )
        except Exception as exc:  # httpx transport errors
            raise opencode_errors.map_transport_error(exc, context) from exc

        if response.status_code not in expected_status:
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise opencode_errors.map_status_error(exc, context) from exc
        return response

    @staticmethod
    def _parse(
        model: type[pydantic.BaseModel],
        payload: Any,
        context: str,
    ) -> pydantic.BaseModel:
        try:
            return model.model_validate(payload)
        except pydantic.ValidationError as exc:
            raise opencode_errors.map_validation_error(exc, context) from exc

    @staticmethod
    def _require_list(payload: Any, context: str) -> list[Any]:
        if isinstance(payload, list):
            return payload
        raise MalformedResponseError(
            f"OpenCode backend returned a non-list payload: {context}."
        )

    @staticmethod
    def _require_dict(payload: Any, context: str) -> dict[Any, Any]:
        if isinstance(payload, dict):
            return payload
        raise MalformedResponseError(
            f"OpenCode backend returned a non-object payload: {context}."
        )

    async def health(self) -> Health:
        response = await self._request("GET", "/global/health", context="health")
        return self._parse(Health, response.json(), "health")

    async def create_session(
        self,
        directory: str,
        *,
        title: str | None = None,
        agent: str | None = None,
    ) -> SessionDetail:
        body: dict[str, Any] = {}
        if title:
            body["title"] = title
        if agent:
            body["agent"] = agent
        response = await self._request(
            "POST",
            "/session",
            params={"directory": directory},
            json_body=body,
            context="create_session",
        )
        return self._parse(SessionDetail, response.json(), "create_session")

    async def send_prompt(
        self,
        session_id: str,
        task: str,
        *,
        agent: str | None = None,
        directory: str | None = None,
    ) -> None:
        body: dict[str, Any] = {
            "parts": [{"type": "text", "text": task}],
        }
        if agent:
            body["agent"] = agent
        params: dict[str, Any] = {}
        if directory:
            params["directory"] = directory
        await self._request(
            "POST",
            f"/session/{session_id}/prompt_async",
            params=params or None,
            json_body=body,
            context=f"send_prompt({session_id})",
            expected_status=(204,),
        )

    async def session_status_map(
        self,
        directory: str | None = None,
    ) -> dict[str, SessionStatus]:
        params = {"directory": directory} if directory else None
        response = await self._request(
            "GET",
            "/session/status",
            params=params,
            context="session_status",
        )
        raw = response.json()
        parsed: dict[str, SessionStatus] = {}
        for session_id, payload in self._require_dict(
            raw, "session/status"
        ).items():
            parsed[session_id] = self._parse(
                SessionStatus, payload, f"session/status[{session_id}]"
            )
        return parsed

    async def session_detail(self, session_id: str) -> SessionDetail:
        response = await self._request(
            "GET",
            f"/session/{session_id}",
            context=f"session_detail({session_id})",
        )
        return self._parse(SessionDetail, response.json(), "session_detail")

    async def messages(
        self,
        session_id: str,
        *,
        limit: int = 50,
        before: str | None = None,
    ) -> list[MessageEntry]:
        params: dict[str, Any] = {"limit": limit}
        if before:
            params["before"] = before
        response = await self._request(
            "GET",
            f"/session/{session_id}/message",
            params=params,
            context=f"messages({session_id})",
        )
        raw = response.json()
        return [
            self._parse(MessageEntry, item, "messages")
            for item in self._require_list(raw, "messages")
        ]

    async def diff(
        self,
        session_id: str,
        *,
        message_id: str | None = None,
    ) -> list[SnapshotFileDiff]:
        params: dict[str, Any] = {}
        if message_id:
            params["messageID"] = message_id
        response = await self._request(
            "GET",
            f"/session/{session_id}/diff",
            params=params or None,
            context=f"diff({session_id})",
        )
        raw = response.json()
        return [
            self._parse(SnapshotFileDiff, item, "diff")
            for item in self._require_list(raw, "diff")
        ]

    async def abort(self, session_id: str) -> bool:
        response = await self._request(
            "POST",
            f"/session/{session_id}/abort",
            context=f"abort({session_id})",
        )
        return bool(response.json())

    async def pending_permissions(
        self,
        directory: str | None = None,
    ) -> list[PermissionRequest]:
        params = {"directory": directory} if directory else None
        response = await self._request(
            "GET",
            "/permission",
            params=params,
            context="pending_permissions",
        )
        raw = response.json()
        return [
            self._parse(PermissionRequest, item, "permission")
            for item in self._require_list(raw, "permission")
        ]

    async def reply_permission(
        self,
        request_id: str,
        reply: str,
        *,
        message: str | None = None,
    ) -> bool:
        body: dict[str, Any] = {"reply": reply}
        if message:
            body["message"] = message
        response = await self._request(
            "POST",
            f"/permission/{request_id}/reply",
            json_body=body,
            context=f"reply_permission({request_id})",
        )
        return bool(response.json())

    async def agents(self) -> list[AgentInfo]:
        response = await self._request("GET", "/agent", context="agents")
        raw = response.json()
        return [
            self._parse(AgentInfo, item, "agents")
            for item in self._require_list(raw, "agents")
        ]

    async def providers(self) -> ProviderList:
        response = await self._request("GET", "/provider", context="providers")
        return self._parse(ProviderList, response.json(), "providers")


__all__ = ["OpenCodeClient", "_SESSION_ID_PATTERN"]