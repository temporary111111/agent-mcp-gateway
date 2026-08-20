"""Unit tests for workspace handles and path containment."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_gateway.errors import (
    InvalidRequestError,
    InvalidWorkspaceError,
    UnauthorizedDirectoryError,
)
from agent_gateway.security.paths import PathPolicy
from agent_gateway.workspaces.manager import WorkspaceManager


def make_manager(root: Path) -> WorkspaceManager:
    return WorkspaceManager(PathPolicy([root]))


def test_open_inside_allowed_root(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    result = manager.open(str(tmp_path))
    assert result["workspace_id"].startswith("ws_")
    assert result["root"] == str(tmp_path.resolve())


def test_open_outside_allowed_root_rejected(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    outside = tmp_path.parent
    with pytest.raises(UnauthorizedDirectoryError):
        manager.open(str(outside))


def test_open_with_no_allowed_roots_fails_closed(tmp_path: Path) -> None:
    manager = WorkspaceManager(PathPolicy([]))
    with pytest.raises(UnauthorizedDirectoryError):
        manager.open(str(tmp_path))


def test_open_nonexistent_directory_rejected(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    with pytest.raises(InvalidRequestError):
        manager.open(str(tmp_path / "does-not-exist"))


def test_unknown_workspace_id_rejected(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    with pytest.raises(InvalidWorkspaceError):
        manager.resolve("ws_deadbeef", "file.txt")


def test_resolve_relative_path_ok(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.txt").write_text("x", encoding="utf-8")
    ws = manager.open(str(tmp_path))["workspace_id"]
    resolved = manager.resolve(ws, "sub/a.txt", must_exist=True)
    assert resolved == (tmp_path / "sub" / "a.txt").resolve()


def test_resolve_absolute_path_rejected(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    ws = manager.open(str(tmp_path))["workspace_id"]
    with pytest.raises(InvalidRequestError):
        manager.resolve(ws, str(tmp_path / "a.txt"))
    with pytest.raises(InvalidRequestError):
        manager.resolve(ws, "C:/Windows/win.ini")


def test_resolve_traversal_rejected(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    ws = manager.open(str(tmp_path))["workspace_id"]
    for bad in ("../secret.txt", "sub/../../secret.txt", "a/../.."):
        with pytest.raises(InvalidRequestError):
            manager.resolve(ws, bad)


def test_resolve_symlink_escape_rejected(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-secret.txt"
    outside.write_text("secret", encoding="utf-8")
    link = tmp_path / "escape"
    try:
        os.symlink(tmp_path.parent, link, target_is_directory=True)
    except OSError:
        pytest.skip("symlink/junction creation not permitted")
    manager = make_manager(tmp_path)
    ws = manager.open(str(tmp_path))["workspace_id"]
    with pytest.raises(InvalidRequestError):
        manager.resolve(ws, "escape/outside-secret.txt")


def test_resolve_null_byte_rejected(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    ws = manager.open(str(tmp_path))["workspace_id"]
    with pytest.raises(InvalidRequestError):
        manager.resolve(ws, "a\x00b")


def test_resolve_must_exist_enforced(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    ws = manager.open(str(tmp_path))["workspace_id"]
    with pytest.raises(InvalidRequestError):
        manager.resolve(ws, "missing.txt", must_exist=True)
    assert manager.resolve(ws, "missing.txt", must_exist=False) is not None


def test_close_workspace(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    ws = manager.open(str(tmp_path))["workspace_id"]
    assert manager.close(ws) is True
    with pytest.raises(InvalidWorkspaceError):
        manager.root(ws)