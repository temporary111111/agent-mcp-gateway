"""Deterministic filesystem primitives for direct mode.

No LLM, no model, no backend: every operation is a plain filesystem
operation restricted to an authorized workspace. Every call resolves the
path through the WorkspaceManager, which re-checks containment at the
point of use.
"""

from __future__ import annotations

import fnmatch
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ..errors import HashConflictError, InvalidRequestError
from ..security.paths import PathPolicy
from ..workspaces.manager import WorkspaceManager
from .edit_match import find_match, replace_text
from .hashing import is_probably_binary, read_bytes_capped, sha256_bytes, sha256_file

_DEFAULT_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache"}

_HEX_PREVIEW_BYTES = 96


def _is_hidden(name: str) -> bool:
    return name.startswith(".")


def _rel_of(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _atomic_write_bytes(target: Path, data: bytes) -> None:
    """Write via a temp file in the same directory + os.replace."""
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".agw-", dir=str(parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _existing_text(path: Path) -> tuple[str, bytes]:
    """Read an existing file as strict UTF-8 text; binary -> error."""
    data = path.read_bytes()
    try:
        return data.decode("utf-8"), data
    except UnicodeDecodeError as exc:
        raise InvalidRequestError(
            "File is not valid UTF-8 text; refusing a text operation on it.",
            detail=str(path),
        ) from exc


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def workspace_tree(
    manager: WorkspaceManager,
    workspace_id: str,
    path: str = ".",
    *,
    max_depth: int = 4,
    max_entries: int = 1000,
    skip_dirs: tuple[str, ...] | None = None,
    include_hidden: bool = False,
) -> dict:
    if max_depth < 1:
        raise InvalidRequestError("max_depth must be >= 1.")
    if max_entries < 1:
        raise InvalidRequestError("max_entries must be >= 1.")
    root = manager.root(workspace_id)
    start = manager.resolve(workspace_id, path, must_exist=True)
    if not start.is_dir():
        raise InvalidRequestError("Tree path must be a directory.")

    skip = set(_DEFAULT_SKIP_DIRS)
    if skip_dirs:
        skip.update(skip_dirs)

    entries: list[dict] = []
    truncated = False

    def _walk(directory: Path, depth: int) -> None:
        nonlocal truncated
        if truncated:
            return
        try:
            names = sorted(
                os.listdir(directory),
                key=lambda n: n.lower(),
            )
        except OSError as exc:
            raise InvalidRequestError(
                f"Cannot list directory: {exc}", detail=str(directory)
            ) from exc
        for name in names:
            if truncated:
                return
            if not include_hidden and _is_hidden(name):
                continue
            child = directory / name
            try:
                resolved_child = child.resolve()
            except OSError:
                continue
            if not PathPolicy.is_within(resolved_child, root):
                continue
            try:
                stat = resolved_child.stat()
            except OSError:
                continue
            is_dir = resolved_child.is_dir()
            if is_dir and name in skip:
                continue
            entries.append(
                {
                    "path": _rel_of(root, resolved_child),
                    "type": "dir" if is_dir else "file",
                    "size": stat.st_size if not is_dir else None,
                }
            )
            if len(entries) >= max_entries:
                truncated = True
                return
            if is_dir and depth < max_depth:
                _walk(resolved_child, depth + 1)

    _walk(start, 1)
    return {
        "workspace_id": workspace_id,
        "root": str(start),
        "entry_count": len(entries),
        "truncated": truncated,
        "entries": entries,
    }


def file_read(
    manager: WorkspaceManager,
    workspace_id: str,
    path: str,
    *,
    offset_bytes: int = 0,
    max_bytes: int = 200_000,
    max_lines: int | None = None,
    line_numbers: bool = True,
) -> dict:
    if offset_bytes < 0:
        raise InvalidRequestError("offset_bytes must be >= 0.")
    if max_bytes < 1:
        raise InvalidRequestError("max_bytes must be >= 1.")
    root = manager.root(workspace_id)
    target = manager.resolve(workspace_id, path, must_exist=True)
    if target.is_dir():
        raise InvalidRequestError("Cannot read a directory with file_read.")

    with target.open("rb") as handle:
        handle.seek(offset_bytes)
        data = handle.read(max_bytes)
        truncated = len(data) == max_bytes and handle.read(1) != b""

    size = target.stat().st_size
    digest = sha256_file(target)
    rel = _rel_of(root, target)

    if is_probably_binary(data):
        preview = data[:_HEX_PREVIEW_BYTES].hex()
        return {
            "workspace_id": workspace_id,
            "path": rel,
            "binary": True,
            "size": size,
            "sha256": digest,
            "preview_hex": preview,
            "preview_bytes": len(data[:_HEX_PREVIEW_BYTES]),
            "truncated": truncated,
            "note": "Binary file; content is not included.",
        }

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        preview = data[:_HEX_PREVIEW_BYTES].hex()
        return {
            "workspace_id": workspace_id,
            "path": rel,
            "binary": True,
            "size": size,
            "sha256": digest,
            "preview_hex": preview,
            "preview_bytes": len(data[:_HEX_PREVIEW_BYTES]),
            "truncated": truncated,
            "note": "Non-UTF-8 text; content is not included.",
        }

    lines = text.splitlines()
    start_line = 1
    if offset_bytes > 0:
        # Count newlines in the raw bytes up to offset_bytes.
        # On Windows, files may use \r\n; if offset lands on \n after
        # \r, it still counts as a newline boundary.
        with target.open("rb") as fh:
            fh.seek(0)
            prefix = fh.read(offset_bytes)
        start_line = 1 + prefix.count(b"\n")
    if max_lines is not None and len(lines) > max_lines:
        lines = lines[:max_lines]
        truncated = True
    if line_numbers:
        width = len(str(start_line + len(lines)))
        content = "\n".join(
            f"{start_line + i:>{width}}: {line}" for i, line in enumerate(lines)
        )
    else:
        content = "\n".join(lines)
    return {
        "workspace_id": workspace_id,
        "path": rel,
        "binary": False,
        "size": size,
        "sha256": digest,
        "encoding": "utf-8",
        "start_line": start_line,
        "line_count": len(lines),
        "truncated": truncated,
        "content": content,
    }


def file_stat(
    manager: WorkspaceManager,
    workspace_id: str,
    path: str,
) -> dict:
    root = manager.root(workspace_id)
    target = manager.resolve(workspace_id, path, must_exist=True)
    stat = target.stat()
    is_dir = target.is_dir()
    digest = None
    binary = None
    if not is_dir:
        with target.open("rb") as handle:
            digest = sha256_file(target)
            binary = is_probably_binary(handle.read(8192))
    return {
        "workspace_id": workspace_id,
        "path": _rel_of(root, target),
        "type": "dir" if is_dir else "file",
        "size": stat.st_size,
        "mtime": datetime.fromtimestamp(
            stat.st_mtime, tz=timezone.utc
        ).isoformat(),
        "sha256": digest,
        "binary": binary,
    }


def file_find(
    manager: WorkspaceManager,
    workspace_id: str,
    pattern: str,
    path: str = ".",
    *,
    max_results: int = 200,
    skip_dirs: tuple[str, ...] | None = None,
    include_hidden: bool = False,
) -> dict:
    if not pattern or not pattern.strip():
        raise InvalidRequestError("pattern must not be empty.")
    if max_results < 1:
        raise InvalidRequestError("max_results must be >= 1.")
    root = manager.root(workspace_id)
    start = manager.resolve(workspace_id, path, must_exist=True)
    if not start.is_dir():
        raise InvalidRequestError("find path must be a directory.")

    skip = set(_DEFAULT_SKIP_DIRS)
    if skip_dirs:
        skip.update(skip_dirs)

    matches: list[dict] = []
    truncated = False
    for directory, dirnames, filenames in os.walk(start):
        resolved_dir = Path(directory).resolve()
        if not PathPolicy.is_within(resolved_dir, root):
            dirnames.clear()
            continue
        dirnames[:] = sorted(
            (d for d in dirnames if d not in skip),
            key=lambda n: n.lower(),
        )
        if not include_hidden:
            dirnames[:] = [d for d in dirnames if not _is_hidden(d)]
            filenames = [f for f in filenames if not _is_hidden(f)]
        for name in sorted(filenames, key=lambda n: n.lower()):
            child = Path(directory) / name
            resolved_child = child.resolve()
            if not PathPolicy.is_within(resolved_child, root):
                continue
            rel = _rel_of(root, resolved_child)
            if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(name, pattern):
                matches.append(
                    {
                        "path": rel,
                        "size": resolved_child.stat().st_size,
                    }
                )
                if len(matches) >= max_results:
                    truncated = True
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


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def _verify_expected(target: Path, expected_sha256: str | None, *, require_hash: bool = False) -> str | None:
    """Verify the caller's expected sha256 against the current file.

    Returns the current hash. Raises if the file exists but no hash was
    provided (when *require_hash* is True) or if the hash mismatches.
    """
    if not target.exists():
        return None
    current = sha256_file(target)
    if expected_sha256 is None:
        if require_hash:
            raise HashConflictError(
                "expected_sha256 is required when overwriting an existing "
                "file. Read the file first (file_read/file_stat) and pass "
                "the sha256 to avoid stale writes.",
                detail={"path": str(target), "actual": current},
            )
        return current
    if current != expected_sha256:
        raise HashConflictError(
            "File changed since it was read: the provided expected_sha256 "
            "does not match the current content. Re-read the file and retry.",
            detail={
                "expected": expected_sha256,
                "actual": current,
            },
        )
    return current


def file_write(
    manager: WorkspaceManager,
    workspace_id: str,
    path: str,
    content: str,
    *,
    expected_sha256: str | None = None,
) -> dict:
    root = manager.root(workspace_id)
    target = manager.resolve(workspace_id, path, must_exist=False)
    before = _verify_expected(target, expected_sha256, require_hash=True)
    data = content.encode("utf-8")
    _atomic_write_bytes(target, data)
    return {
        "workspace_id": workspace_id,
        "path": _rel_of(root, target),
        "created": before is None,
        "before_sha256": before,
        "after_sha256": sha256_bytes(data),
        "size": len(data),
        "note": (
            "No commit was made; inspect git_diff to review before deciding "
            "to commit."
        ),
    }


def file_replace(
    manager: WorkspaceManager,
    workspace_id: str,
    path: str,
    old_string: str,
    new_string: str,
    *,
    occurrence: int = 0,
    expected_sha256: str | None = None,
) -> dict:
    """Replace ``old_string`` in a text file using 9-layer fuzzy matching.

    Tries progressively looser matching strategies to handle imprecise
    LLM output:
    1. Exact match
    2. Line-trimmed match
    3. Whitespace-normalized match
    4. Indentation-flexible match
    5. Escape-normalized match
    6. Trimmed-boundary match
    7. Block-anchor match (first+last lines as anchors)
    8. Context-aware match (first+last lines + 50% middle)
    9. Multi-occurrence exact match

    occurrence == 0 replaces all occurrences; positive n replaces only
    the n-th occurrence (1-based).
    """
    root = manager.root(workspace_id)
    target = manager.resolve(workspace_id, path, must_exist=True)
    before = _verify_expected(target, expected_sha256, require_hash=True)
    if old_string == "":
        raise InvalidRequestError("old_string must not be empty.")
    text, _ = _existing_text(target)

    if occurrence == 0:
        # Replace all occurrences using 9-layer matching
        new_text, count, match_type = replace_text(text, old_string, new_string, replace_all=True)
        if count == 0:
            # Try find_match for better error message
            found = find_match(text, old_string)
            if found:
                raise InvalidRequestError(
                    "old_string was found but could not be uniquely matched. "
                    "Try providing more context or use file_apply_patch.",
                    detail={
                        "path": _rel_of(root, target),
                        "needle": old_string[:120],
                        "found_preview": found[:120],
                    },
                )
            raise InvalidRequestError(
                "old_string was not found in the file.",
                detail={"path": _rel_of(root, target), "needle": old_string[:120]},
            )
        replaced = count
    else:
        # Single occurrence replacement using 9-layer matching
        # First try exact match for occurrence-based replacement
        count = text.count(old_string)
        if count > 0:
            # Exact match found — use it directly
            if occurrence > count:
                raise InvalidRequestError(
                    f"Occurrence {occurrence} requested but only {count} found.",
                    detail=str(target),
                )
            position = -1
            for _ in range(occurrence):
                position = text.find(old_string, position + 1)
            new_text = text[:position] + new_string + text[position + len(old_string):]
            replaced = 1
        else:
            # No exact match — try 9-layer fuzzy matching
            found = find_match(text, old_string)
            if not found:
                raise InvalidRequestError(
                    "old_string was not found in the file.",
                    detail={"path": _rel_of(root, target), "needle": old_string[:120]},
                )
            # For fuzzy match, replace the first occurrence of the matched text
            idx = text.find(found)
            new_text = text[:idx] + new_string + text[idx + len(found):]
            replaced = 1

    data = new_text.encode("utf-8")
    _atomic_write_bytes(target, data)
    return {
        "workspace_id": workspace_id,
        "path": _rel_of(root, target),
        "replaced": replaced,
        "before_sha256": before,
        "after_sha256": sha256_bytes(data),
        "size": len(data),
    }


# ---------------------------------------------------------------------------
# Unified-diff application (deterministic, no external tools)
# ---------------------------------------------------------------------------


class _Hunk:
    __slots__ = ("old_start", "new_start", "old_lines", "new_lines",
                 "old_no_newline", "new_no_newline")

    def __init__(self) -> None:
        self.old_start = 0
        self.new_start = 0
        self.old_lines: list[str] = []
        self.new_lines: list[str] = []
        self.old_no_newline = False
        self.new_no_newline = False


def _parse_unified_patch(patch: str) -> list[_Hunk]:
    header_re = re.compile(
        r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@"
    )
    hunks: list[_Hunk] = []
    current: _Hunk | None = None
    for raw_line in patch.splitlines():
        line = raw_line
        if line.startswith("@@ "):
            match = header_re.match(line)
            if not match:
                raise InvalidRequestError(
                    "Malformed hunk header.", detail=line[:120]
                )
            current = _Hunk()
            current.old_start = int(match.group(1))
            current.new_start = int(match.group(3))
            hunks.append(current)
            continue
        if current is None:
            continue
        if line == "\\ No newline at end of file":
            if current.old_lines and not current.new_lines:
                current.old_no_newline = True
            elif current.new_lines:
                current.new_no_newline = True
            continue
        if line.startswith(" "):
            text = line[1:]
            current.old_lines.append(text)
            current.new_lines.append(text)
        elif line.startswith("-"):
            current.old_lines.append(line[1:])
        elif line.startswith("+"):
            current.new_lines.append(line[1:])
        else:
            raise InvalidRequestError(
                "Malformed patch line.", detail=line[:120]
            )
    if not hunks:
        raise InvalidRequestError(
            "Patch contains no hunks; nothing to apply."
        )
    return hunks


def file_apply_patch(
    manager: WorkspaceManager,
    workspace_id: str,
    path: str,
    patch: str,
    *,
    expected_sha256: str | None = None,
) -> dict:
    """Apply a unified diff (git-style) to a workspace file.

    Supports create-new-file patches (old side ``/dev/null``) and
    ``\\ No newline at end of file`` markers. All hunks are validated
    against the current content before anything is written; a failed
    hunk leaves the file untouched.
    """
    root = manager.root(workspace_id)
    target = manager.resolve(workspace_id, path, must_exist=False)
    before = _verify_expected(target, expected_sha256, require_hash=True)

    if not target.exists():
        old_lines: list[str] = []
        old_trailing = False
        crlf = False
        create = True
    else:
        old_text, _ = _existing_text(target)
        raw_lines = old_text.split("\n")
        crlf = any(line.endswith("\r") for line in raw_lines)
        old_lines = [
            line[:-1] if line.endswith("\r") else line for line in raw_lines
        ]
        old_trailing = old_text.endswith("\n")
        if old_lines and old_lines[-1] == "":
            old_lines.pop()
        create = False

    hunks = _parse_unified_patch(patch)

    new_lines: list[str] = []
    new_trailing = old_trailing
    delta = 0
    cursor = 0

    for hunk in hunks:
        if create:
            if hunk.old_lines:
                raise InvalidRequestError(
                    "Patch removes content but the file does not exist.",
                    detail=str(target),
                )
            new_lines.extend(hunk.new_lines)
            new_trailing = not hunk.new_no_newline
            continue

        index = hunk.old_start - 1 + delta
        length = len(hunk.old_lines)
        if index < cursor:
            index = cursor
        match_index = None
        search_window = 60
        for offset in range(search_window + 1):
            candidate = index + offset
            if candidate + length <= len(old_lines):
                if old_lines[candidate:candidate + length] == hunk.old_lines:
                    match_index = candidate
                    break
            if offset > 0:
                candidate_back = index - offset
                if candidate_back >= 0 and (
                    candidate_back + length <= len(old_lines)
                ):
                    if (
                        old_lines[candidate_back:candidate_back + length]
                        == hunk.old_lines
                    ):
                        match_index = candidate_back
                        break
        if match_index is None:
            raise InvalidRequestError(
                "Patch hunk does not match the current file content; "
                "file was left unchanged.",
                detail={
                    "path": _rel_of(root, target),
                    "hunk_start": hunk.old_start,
                    "needle": hunk.old_lines[0][:120] if hunk.old_lines else "",
                },
            )
        new_lines.extend(old_lines[cursor:match_index])
        new_lines.extend(hunk.new_lines)
        cursor = match_index + length
        delta += len(hunk.new_lines) - len(hunk.old_lines)
        if hunk.new_no_newline:
            new_trailing = False
        else:
            new_trailing = True

    if not create:
        new_lines.extend(old_lines[cursor:])

    newline = "\r\n" if crlf else "\n"
    new_text = newline.join(new_lines)
    if new_trailing:
        new_text += newline
    data = new_text.encode("utf-8")
    _atomic_write_bytes(target, data)
    return {
        "workspace_id": workspace_id,
        "path": _rel_of(root, target),
        "created": create,
        "hunks": len(hunks),
        "newline": newline,
        "before_sha256": before,
        "after_sha256": sha256_bytes(data),
        "size": len(data),
    }


__all__ = [
    "file_apply_patch",
    "file_find",
    "file_read",
    "file_replace",
    "file_stat",
    "file_write",
    "workspace_tree",
]