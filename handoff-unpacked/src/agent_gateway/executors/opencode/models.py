"""Typed models for the OpenCode HTTP API (verified against OpenAPI v1.18.18).

All models tolerate extra JSON keys so backend evolution does not crash the
gateway; fields we read are validated.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class OpenCodeModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class Health(OpenCodeModel):
    healthy: bool
    version: str


class SessionStatus(OpenCodeModel):
    type: Literal["idle", "busy", "retry"]
    attempt: int | None = None
    message: str | None = None
    next: int | None = None


class SessionDetail(OpenCodeModel):
    id: str
    title: str | None = None
    agent: str | None = None
    directory: str | None = None
    projectID: str | None = None
    version: str | None = None
    cost: float | None = None
    tokens: dict[str, Any] | None = None
    summary: dict[str, Any] | None = None
    time: dict[str, Any] | None = None


class SnapshotFileDiff(OpenCodeModel):
    file: str | None = None
    patch: str | None = None
    additions: int = 0
    deletions: int = 0
    status: str | None = None


class PermissionRequest(OpenCodeModel):
    id: str
    sessionID: str
    permission: str
    patterns: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    always: list[str] = Field(default_factory=list)
    tool: dict[str, Any] | None = None


class AgentInfo(OpenCodeModel):
    name: str
    description: str | None = None
    mode: str | None = None
    hidden: bool | None = None
    model: dict[str, Any] | None = None


class ProviderInfo(OpenCodeModel):
    id: str
    name: str
    source: str | None = None
    models: dict[str, Any] = Field(default_factory=dict)


class ProviderList(OpenCodeModel):
    all: list[ProviderInfo] = Field(default_factory=list)
    default: dict[str, str] = Field(default_factory=dict)
    connected: list[str] = Field(default_factory=list)


class Part(OpenCodeModel):
    id: str | None = None
    type: str | None = None
    text: str | None = None
    tool: str | None = None
    callID: str | None = None
    state: Any = None
    name: str | None = None
    reason: str | None = None


class MessageEntry(OpenCodeModel):
    info: dict[str, Any]
    parts: list[Part] = Field(default_factory=list)