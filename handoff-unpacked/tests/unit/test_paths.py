"""Unit tests for directory security (allowed roots, traversal)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_gateway.errors import (
    InvalidRequestError,
    UnauthorizedDirectoryError,
)
from agent_gateway.security.paths import PathPolicy

ROOT = Path("C:/Users/dev/Desktop/sample-repo").resolve()


def test_inside_root_allowed() -> None:
    policy = PathPolicy([ROOT])
    resolved = policy.resolve_task_directory(str(ROOT))
    assert resolved == ROOT
    nested = policy.resolve_task_directory(
        str(ROOT / "subdir"), must_exist=False
    )
    assert nested == ROOT / "subdir"


def test_outside_root_rejected() -> None:
    policy = PathPolicy([ROOT])
    with pytest.raises(UnauthorizedDirectoryError):
        policy.resolve_task_directory("C:/Users/dev/Desktop")


def test_traversal_rejected() -> None:
    policy = PathPolicy([ROOT])
    with pytest.raises(UnauthorizedDirectoryError):
        policy.resolve_task_directory(str(ROOT / ".."))


def test_empty_path_rejected() -> None:
    policy = PathPolicy([ROOT])
    with pytest.raises(InvalidRequestError):
        policy.resolve_task_directory("   ")


def test_nonexistent_directory_rejected() -> None:
    policy = PathPolicy([ROOT])
    with pytest.raises(InvalidRequestError):
        policy.resolve_task_directory(str(ROOT / "does-not-exist-123"))


def test_drive_root_rejected() -> None:
    policy = PathPolicy([ROOT])
    with pytest.raises(UnauthorizedDirectoryError):
        policy.resolve_task_directory("C:/")


def test_no_roots_fails_closed() -> None:
    policy = PathPolicy([])
    with pytest.raises(UnauthorizedDirectoryError):
        policy.resolve_task_directory(str(ROOT))


def test_multiple_roots() -> None:
    other = Path("C:/Users/dev/Desktop").resolve()
    policy = PathPolicy([ROOT, other])
    assert policy.resolve_task_directory(str(ROOT)) == ROOT
    assert policy.resolve_task_directory(str(other)) == other


def test_sibling_prefix_not_allowed() -> None:
    policy = PathPolicy([Path("C:/Users/dev/Desktop/sample")])
    with pytest.raises(UnauthorizedDirectoryError):
        policy.resolve_task_directory("C:/Users/dev/Desktop/sample-evil")


def test_case_insensitive_windows_paths() -> None:
    policy = PathPolicy([ROOT])
    resolved = policy.resolve_task_directory("C:/USERS/DEV/DESKTOP/SAMPLE-REPO")
    assert resolved == ROOT


def test_allowed_roots_listing() -> None:
    policy = PathPolicy([ROOT])
    assert policy.allowed_roots() == [str(ROOT)]