"""Unit tests for deterministic read-only git tools."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent_gateway.direct.git import git_diff, git_log, git_show, git_status
from agent_gateway.errors import InvalidRequestError
from agent_gateway.security.paths import PathPolicy
from agent_gateway.workspaces.manager import WorkspaceManager


def _git(root: Path, *args: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "app.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8"
    )
    _git(tmp_path, "add", "app.py")
    _git(tmp_path, "commit", "-m", "initial commit")
    return tmp_path


@pytest.fixture
def ws(repo: Path) -> tuple[WorkspaceManager, str]:
    manager = WorkspaceManager(PathPolicy([repo]))
    return manager, manager.open(str(repo))["workspace_id"]


def test_git_status_clean(ws) -> None:
    manager, workspace_id = ws
    result = git_status(manager, workspace_id)
    assert result["clean"] is True
    assert result["branch"] == "main"


def test_git_status_dirty(ws) -> None:
    manager, workspace_id = ws
    (manager.root(workspace_id) / "app.py").write_text(
        "def add(a, b):\n    return a * b\n", encoding="utf-8"
    )
    result = git_status(manager, workspace_id)
    assert result["clean"] is False
    entry = next(e for e in result["entries"] if e["path"] == "app.py")
    assert entry["x"] == " "
    assert entry["y"] == "M"


def test_git_diff_reports_changes(ws) -> None:
    manager, workspace_id = ws
    root = manager.root(workspace_id)
    (root / "app.py").write_text(
        "def add(a, b):\n    return a + b  # comment\n", encoding="utf-8"
    )
    result = git_diff(manager, workspace_id)
    assert "app.py" in result["files"]
    assert "# comment" in result["diff"]
    assert "-def add(a, b):" not in result["diff"]


def test_git_diff_staged(ws) -> None:
    manager, workspace_id = ws
    root = manager.root(workspace_id)
    (root / "new.py").write_text("x = 1\n", encoding="utf-8")
    _git(root, "add", "new.py")
    result = git_diff(manager, workspace_id, staged=True)
    assert "new.py" in result["files"]


def test_git_diff_path_filter(ws) -> None:
    manager, workspace_id = ws
    root = manager.root(workspace_id)
    (root / "app.py").write_text(
        "def add(a, b):\n    return a * b\n", encoding="utf-8"
    )
    (root / "other.txt").write_text("other\n", encoding="utf-8")
    result = git_diff(manager, workspace_id, path="app.py")
    assert result["files"] == ["app.py"]


def test_git_log(ws) -> None:
    manager, workspace_id = ws
    result = git_log(manager, workspace_id)
    assert result["count"] == 1
    assert result["entries"][0]["subject"] == "initial commit"
    assert len(result["entries"][0]["hash"]) == 7


def test_git_show_head(ws) -> None:
    manager, workspace_id = ws
    result = git_show(manager, workspace_id, rev="HEAD")
    assert "initial commit" in result["output"]
    assert "app.py" in result["output"]


def test_git_show_unsafe_rev_rejected(ws) -> None:
    manager, workspace_id = ws
    for bad in ("--output=/tmp/x", "HEAD; rm -rf", "-n 5"):
        with pytest.raises(InvalidRequestError):
            git_show(manager, workspace_id, rev=bad)


def test_not_a_git_repository_rejected(tmp_path: Path) -> None:
    manager = WorkspaceManager(PathPolicy([tmp_path]))
    workspace_id = manager.open(str(tmp_path))["workspace_id"]
    with pytest.raises(InvalidRequestError):
        git_status(manager, workspace_id)


def test_git_tools_reject_escape_path(ws) -> None:
    manager, workspace_id = ws
    with pytest.raises(InvalidRequestError):
        git_diff(manager, workspace_id, path="../app.py")