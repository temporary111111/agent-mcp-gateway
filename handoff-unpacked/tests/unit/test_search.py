"""Unit tests for deterministic content search."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_gateway.direct.search import code_search
from agent_gateway.errors import InvalidRequestError
from agent_gateway.security.paths import PathPolicy
from agent_gateway.workspaces.manager import WorkspaceManager


@pytest.fixture
def manager(tmp_path: Path) -> tuple[WorkspaceManager, Path]:
    return WorkspaceManager(PathPolicy([tmp_path])), tmp_path


@pytest.fixture
def ws(manager) -> str:
    return manager[0].open(str(manager[1]))["workspace_id"]


def test_code_search_finds_matches(manager, ws: str) -> None:
    _, root = manager
    (root / "a.py").write_text("def hello():\n    return 1\n", encoding="utf-8")
    (root / "b.py").write_text("x = hello()\n", encoding="utf-8")
    result = code_search(manager[0], ws, "hello")
    assert result["match_count"] == 2
    files = {m["file"] for m in result["matches"]}
    assert files == {"a.py", "b.py"}


def test_code_search_regex_and_case(manager, ws: str) -> None:
    _, root = manager
    (root / "a.py").write_text("FooBar\nfoobar\n", encoding="utf-8")
    insensitive = code_search(manager[0], ws, "foobar")
    assert insensitive["match_count"] == 2
    sensitive = code_search(manager[0], ws, "foobar", case_sensitive=True)
    assert sensitive["match_count"] == 1


def test_code_search_literal_mode(manager, ws: str) -> None:
    _, root = manager
    (root / "a.txt").write_text("price $5.00 here\n", encoding="utf-8")
    result = code_search(manager[0], ws, "$5.00", is_regex=False)
    assert result["match_count"] == 1
    with pytest.raises(InvalidRequestError):
        code_search(manager[0], ws, "(", is_regex=True)


def test_code_search_skips_binary_and_git(manager, ws: str) -> None:
    _, root = manager
    (root / ".git").mkdir()
    (root / ".git" / "x").write_text("hello\n", encoding="utf-8")
    (root / "bin.dat").write_bytes(b"\x00\x01hello\x00")
    (root / "ok.py").write_text("hello\n", encoding="utf-8")
    result = code_search(manager[0], ws, "hello")
    files = {m["file"] for m in result["matches"]}
    assert files == {"ok.py"}


def test_code_search_truncates_results(manager, ws: str) -> None:
    _, root = manager
    (root / "a.py").write_text("\n".join("hit" for _ in range(50)), encoding="utf-8")
    result = code_search(manager[0], ws, "hit", max_results=10)
    assert result["truncated"] is True
    assert result["match_count"] == 10


def test_code_search_skips_large_files(manager, ws: str) -> None:
    _, root = manager
    (root / "big.txt").write_text("needle\n" * 5000, encoding="utf-8")
    result = code_search(manager[0], ws, "needle", max_file_bytes=1000)
    assert result["match_count"] == 0


def test_code_search_includes_line_and_column(manager, ws: str) -> None:
    _, root = manager
    (root / "a.py").write_text("abc hello xyz\n", encoding="utf-8")
    result = code_search(manager[0], ws, "hello")
    first = result["matches"][0]
    assert first["line"] == 1
    assert first["column"] == 5
    assert first["text"] == "abc hello xyz"