"""Deterministic content search (no LLM, no external binaries).

A plain line-based regex/substring search over workspace text files.
Binary files and huge files are skipped; results are capped and sorted
for deterministic output.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from ..errors import InvalidRequestError
from ..workspaces.manager import WorkspaceManager
from .hashing import is_probably_binary

_DEFAULT_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache"}
_MAX_LINE_CHARS = 500
_BINARY_PROBE = 8192


def code_search(
    manager: WorkspaceManager,
    workspace_id: str,
    pattern: str,
    path: str = ".",
    *,
    max_results: int = 200,
    max_file_bytes: int = 200_000,
    case_sensitive: bool = False,
    is_regex: bool = True,
    include_hidden: bool = False,
) -> dict:
    if not pattern or not pattern.strip():
        raise InvalidRequestError("pattern must not be empty.")
    if max_results < 1:
        raise InvalidRequestError("max_results must be >= 1.")
    if max_file_bytes < 1:
        raise InvalidRequestError("max_file_bytes must be >= 1.")

    if is_regex:
        try:
            flags = 0 if case_sensitive else re.IGNORECASE
            needle = re.compile(pattern, flags)
        except re.error as exc:
            raise InvalidRequestError(
                f"Invalid regular expression: {exc}", detail=pattern
            ) from exc
    elif not case_sensitive:
        needle = pattern.lower()
    else:
        needle = pattern

    root = manager.root(workspace_id)
    start = manager.resolve(workspace_id, path, must_exist=True)
    if not start.is_dir():
        raise InvalidRequestError("Search path must be a directory.")

    matches: list[dict] = []
    truncated = False

    def _line_hits(line: str) -> list[int]:
        if is_regex:
            return [m.start() for m in needle.finditer(line)]
        if not case_sensitive:
            positions: list[int] = []
            lower = line.lower()
            index = lower.find(needle)
            while index != -1:
                positions.append(index)
                index = lower.find(needle, index + 1)
            return positions
        positions = []
        index = line.find(needle)
        while index != -1:
            positions.append(index)
            index = line.find(needle, index + 1)
        return positions

    for directory, dirnames, filenames in os.walk(start):
        dirnames[:] = sorted(
            (d for d in dirnames if d not in _DEFAULT_SKIP_DIRS),
            key=lambda n: n.lower(),
        )
        if not include_hidden:
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            filenames = [f for f in filenames if not f.startswith(".")]
        for name in sorted(filenames, key=lambda n: n.lower()):
            if truncated:
                break
            child = Path(directory) / name
            try:
                size = child.stat().st_size
            except OSError:
                continue
            if size > max_file_bytes:
                continue
            try:
                with child.open("rb") as handle:
                    probe = handle.read(_BINARY_PROBE)
                    if is_probably_binary(probe):
                        continue
                    handle.seek(0)
                    text = handle.read().decode("utf-8", errors="replace")
            except OSError:
                continue
            rel = child.relative_to(root).as_posix()
            for line_number, line in enumerate(text.splitlines(), start=1):
                hits = _line_hits(line)
                if not hits:
                    continue
                snippet = line[:_MAX_LINE_CHARS]
                if len(line) > _MAX_LINE_CHARS:
                    snippet += "...[truncated]"
                for column in hits[:5]:
                    matches.append(
                        {
                            "file": rel,
                            "line": line_number,
                            "column": column + 1,
                            "text": snippet,
                        }
                    )
                    if len(matches) >= max_results:
                        truncated = True
                        break
                if truncated:
                    break
            if truncated:
                break

    return {
        "workspace_id": workspace_id,
        "pattern": pattern,
        "match_count": len(matches),
        "truncated": truncated,
        "matches": matches,
    }


__all__ = ["code_search"]