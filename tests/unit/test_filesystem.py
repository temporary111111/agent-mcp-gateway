"""Unit tests for deterministic filesystem tools."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_gateway.direct.filesystem import (
    file_apply_patch,
    file_find,
    file_read,
    file_replace,
    file_stat,
    file_write,
    workspace_tree,
)
from agent_gateway.direct.hashing import sha256_bytes, sha256_file
from agent_gateway.errors import HashConflictError, InvalidRequestError
from agent_gateway.security.paths import PathPolicy
from agent_gateway.workspaces.manager import WorkspaceManager


@pytest.fixture
def manager(tmp_path: Path) -> tuple[WorkspaceManager, Path]:
    return WorkspaceManager(PathPolicy([tmp_path])), tmp_path


@pytest.fixture
def ws(manager) -> str:
    return manager[0].open(str(manager[1]))["workspace_id"]


def test_workspace_tree(manager, ws: str) -> None:
    _, root = manager
    (root / "a.py").write_text("x", encoding="utf-8")
    (root / "sub").mkdir()
    (root / "sub" / "b.txt").write_text("y", encoding="utf-8")
    result = workspace_tree(manager[0], ws)
    paths = {entry["path"] for entry in result["entries"]}
    assert {"a.py", "sub", "sub/b.txt"} <= paths
    assert result["truncated"] is False


def test_workspace_tree_skips_git_and_limits(manager, ws: str) -> None:
    _, root = manager
    (root / ".git").mkdir()
    (root / ".git" / "x").write_text("", encoding="utf-8")
    for i in range(50):
        (root / f"f{i}.py").write_text("", encoding="utf-8")
    result = workspace_tree(manager[0], ws, max_entries=10)
    assert result["truncated"] is True
    assert result["entry_count"] == 10
    assert all(".git" not in e["path"] for e in result["entries"])


def test_file_read_line_numbers(manager, ws: str) -> None:
    _, root = manager
    target = root / "hello.txt"
    target.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    result = file_read(manager[0], ws, "hello.txt")
    assert result["binary"] is False
    assert result["content"].startswith("1: alpha")
    assert result["sha256"] == sha256_file(target)


def test_file_read_max_lines_truncates(manager, ws: str) -> None:
    _, root = manager
    (root / "many.txt").write_text("\n".join(f"line{i}" for i in range(100)), encoding="utf-8")
    result = file_read(manager[0], ws, "many.txt", max_lines=10)
    assert result["line_count"] == 10
    assert result["truncated"] is True


def test_file_read_max_bytes_truncates(manager, ws: str) -> None:
    _, root = manager
    (root / "big.txt").write_text("a" * 1000, encoding="utf-8")
    result = file_read(manager[0], ws, "big.txt", max_bytes=100)
    assert result["truncated"] is True
    assert len(result["content"]) < 200


def test_file_read_offset(manager, ws: str) -> None:
    _, root = manager
    (root / "t.txt").write_text("aaaa\nbbbb\ncccc\n", encoding="utf-8")
    result = file_read(manager[0], ws, "t.txt", offset_bytes=5, max_lines=10)
    assert "bbbb" in result["content"]
    assert result["start_line"] == 2


def test_file_read_binary_safe(manager, ws: str) -> None:
    _, root = manager
    target = root / "blob.bin"
    target.write_bytes(b"\x00\x01\x02binary\x00data")
    result = file_read(manager[0], ws, "blob.bin")
    assert result["binary"] is True
    assert "content" not in result
    assert result["size"] == target.stat().st_size
    assert result["sha256"] == sha256_file(target)


def test_file_read_missing_rejected(manager, ws: str) -> None:
    with pytest.raises(InvalidRequestError):
        file_read(manager[0], ws, "nope.txt")


def test_file_stat(manager, ws: str) -> None:
    _, root = manager
    (root / "f.txt").write_text("hello", encoding="utf-8")
    result = file_stat(manager[0], ws, "f.txt")
    assert result["type"] == "file"
    assert result["size"] == 5
    assert result["binary"] is False
    assert result["sha256"] == sha256_bytes(b"hello")


def test_file_stat_binary_flag(manager, ws: str) -> None:
    _, root = manager
    (root / "b.bin").write_bytes(b"\x00\x01")
    result = file_stat(manager[0], ws, "b.bin")
    assert result["binary"] is True


def test_file_find_pattern(manager, ws: str) -> None:
    _, root = manager
    (root / "main.py").write_text("", encoding="utf-8")
    (root / "test_main.py").write_text("", encoding="utf-8")
    (root / "notes.txt").write_text("", encoding="utf-8")
    result = file_find(manager[0], ws, "*.py")
    assert {m["path"] for m in result["matches"]} == {"main.py", "test_main.py"}


def test_file_find_max_results(manager, ws: str) -> None:
    _, root = manager
    for i in range(20):
        (root / f"f{i}.py").write_text("", encoding="utf-8")
    result = file_find(manager[0], ws, "*.py", max_results=5)
    assert result["truncated"] is True
    assert result["match_count"] == 5


def test_file_write_create(manager, ws: str) -> None:
    _, root = manager
    result = file_write(manager[0], ws, "new/file.txt", "hello")
    assert result["created"] is True
    assert result["before_sha256"] is None
    assert (root / "new" / "file.txt").read_text(encoding="utf-8") == "hello"


def test_file_write_overwrite_with_expected_hash(manager, ws: str) -> None:
    _, root = manager
    (root / "f.txt").write_text("one", encoding="utf-8")
    current = sha256_file(root / "f.txt")
    result = file_write(manager[0], ws, "f.txt", "two", expected_sha256=current)
    assert result["created"] is False
    assert result["before_sha256"] == current
    assert (root / "f.txt").read_text(encoding="utf-8") == "two"


def test_file_write_stale_hash_conflict(manager, ws: str) -> None:
    _, root = manager
    (root / "f.txt").write_text("one", encoding="utf-8")
    with pytest.raises(HashConflictError):
        file_write(manager[0], ws, "f.txt", "two", expected_sha256="0" * 64)
    assert (root / "f.txt").read_text(encoding="utf-8") == "one"


def test_file_write_escape_attempts(manager, ws: str) -> None:
    for bad in ("../evil.txt", "sub/../../evil.txt", "C:/Windows/evil.txt", "C:/evil.txt"):
        with pytest.raises(InvalidRequestError):
            file_write(manager[0], ws, bad, "x")


def test_file_replace_exact(manager, ws: str) -> None:
    _, root = manager
    (root / "f.txt").write_text("foo bar foo", encoding="utf-8")
    current = sha256_file(root / "f.txt")
    result = file_replace(manager[0], ws, "f.txt", "foo", "baz", expected_sha256=current)
    assert result["replaced"] == 2
    assert (root / "f.txt").read_text(encoding="utf-8") == "baz bar baz"


def test_file_replace_occurrence(manager, ws: str) -> None:
    _, root = manager
    (root / "f.txt").write_text("foo foo foo", encoding="utf-8")
    result = file_replace(manager[0], ws, "f.txt", "foo", "bar", occurrence=2)
    assert result["replaced"] == 1
    assert (root / "f.txt").read_text(encoding="utf-8") == "foo bar foo"


def test_file_replace_missing_needle(manager, ws: str) -> None:
    _, root = manager
    (root / "f.txt").write_text("hello", encoding="utf-8")
    with pytest.raises(InvalidRequestError):
        file_replace(manager[0], ws, "f.txt", "zzz", "y")


def test_file_replace_stale_hash_conflict(manager, ws: str) -> None:
    _, root = manager
    (root / "f.txt").write_text("hello", encoding="utf-8")
    with pytest.raises(HashConflictError):
        file_replace(manager[0], ws, "f.txt", "hello", "bye", expected_sha256="0" * 64)


def test_patch_modify_existing(manager, ws: str) -> None:
    _, root = manager
    (root / "app.py").write_text(
        "def add(a, b):\n    return a + b\n\nprint(add(1, 2))\n",
        encoding="utf-8",
    )
    patch = (
        "--- a/app.py\n+++ b/app.py\n"
        "@@ -1,4 +1,4 @@\n def add(a, b):\n-    return a + b\n+    return a - b\n \n print(add(1, 2))\n"
    )
    result = file_apply_patch(manager[0], ws, "app.py", patch)
    assert result["hunks"] == 1
    assert "return a - b" in (root / "app.py").read_text(encoding="utf-8")


def test_patch_create_new_file(manager, ws: str) -> None:
    _, root = manager
    patch = (
        "--- /dev/null\n+++ b/newfile.txt\n"
        "@@ -0,0 +1,3 @@\n+line one\n+line two\n+line three\n"
    )
    result = file_apply_patch(manager[0], ws, "newfile.txt", patch)
    assert result["created"] is True
    assert (root / "newfile.txt").read_text(encoding="utf-8") == (
        "line one\nline two\nline three\n"
    )


def test_patch_no_newline_marker(manager, ws: str) -> None:
    _, root = manager
    (root / "f.txt").write_text("line1\nline2", encoding="utf-8")  # no trailing NL
    patch = (
        "--- a/f.txt\n+++ b/f.txt\n"
        "@@ -1,2 +1,2 @@\n line1\n-line2\n\\ No newline at end of file\n+line2 changed\n"
    )
    result = file_apply_patch(manager[0], ws, "f.txt", patch)
    assert (root / "f.txt").read_text(encoding="utf-8") == "line1\nline2 changed"


def test_patch_multiple_hunks(manager, ws: str) -> None:
    _, root = manager
    (root / "f.txt").write_text(
        "one\ntwo\nthree\nfour\nfive\n", encoding="utf-8"
    )
    patch = (
        "--- a/f.txt\n+++ b/f.txt\n"
        "@@ -1,3 +1,3 @@\n one\n-two\n+2\n three\n"
        "@@ -4,2 +4,2 @@\n four\n-five\n+5\n"
    )
    result = file_apply_patch(manager[0], ws, "f.txt", patch)
    assert result["hunks"] == 2
    assert (root / "f.txt").read_text(encoding="utf-8") == "one\n2\nthree\nfour\n5\n"


def test_patch_nonmatching_hunk_leaves_file_untouched(manager, ws: str) -> None:
    _, root = manager
    (root / "f.txt").write_text("original content\n", encoding="utf-8")
    before = sha256_file(root / "f.txt")
    patch = (
        "--- a/f.txt\n+++ b/f.txt\n"
        "@@ -1,1 +1,1 @@\n-totally different\n+changed\n"
    )
    with pytest.raises(InvalidRequestError):
        file_apply_patch(manager[0], ws, "f.txt", patch)
    assert sha256_file(root / "f.txt") == before


def test_patch_stale_hash_conflict(manager, ws: str) -> None:
    _, root = manager
    (root / "f.txt").write_text("hello\n", encoding="utf-8")
    patch = "--- a/f.txt\n+++ b/f.txt\n@@ -1,1 +1,1 @@\n-hello\n+bye\n"
    with pytest.raises(HashConflictError):
        file_apply_patch(manager[0], ws, "f.txt", patch, expected_sha256="0" * 64)