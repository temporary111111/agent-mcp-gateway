"""Deterministic read-only git inspection tools.

These run the ``git`` binary as a fixed-argument subprocess inside an
authorized workspace (no shell). Only read-only subcommands are exposed:
status, diff, log, show. Nothing here can commit, push, reset, or
overwrite worktree state.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from ..errors import InvalidRequestError, ProcessExecutionError
from ..workspaces.manager import WorkspaceManager

_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
_GIT_TIMEOUT = 60
_MAX_OUTPUT = 400_000
_DIFF_HEADER_RE = re.compile(r"^diff --git a/(.*?) b/(.*)$")
_SAFE_REV_RE = re.compile(r"^[A-Za-z0-9._/^~@-]+$")


class _GitResult:
    __slots__ = ("exit_code", "stdout", "stderr", "truncated")

    def __init__(self, exit_code: int, stdout: str, stderr: str, truncated: bool):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.truncated = truncated


def _run_git(root: Path, args: list[str]) -> _GitResult:
    truncated = False
    try:
        process = subprocess.Popen(
            ["git", "-C", str(root), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            creationflags=_CREATE_NO_WINDOW,
        )
    except OSError as exc:
        raise ProcessExecutionError(
            "Failed to start git; is git installed and on PATH?",
            detail=str(exc),
        ) from exc
    try:
        stdout, stderr = process.communicate(timeout=_GIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        _kill(process)
        raise ProcessExecutionError(
            f"git did not finish within {_GIT_TIMEOUT}s.", detail=str(root)
        ) from None

    out_text = stdout.decode("utf-8", errors="replace")
    err_text = stderr.decode("utf-8", errors="replace")
    if len(stdout) > _MAX_OUTPUT:
        out_text = out_text[:_MAX_OUTPUT]
        truncated = True
    if len(stderr) > _MAX_OUTPUT:
        err_text = err_text[:_MAX_OUTPUT]
        truncated = True
    return _GitResult(process.returncode, out_text, err_text, truncated)


def _kill(process: subprocess.Popen) -> None:
    try:
        process.kill()
    except OSError:
        pass


def _require_git_repo(manager: WorkspaceManager, workspace_id: str) -> Path:
    root = manager.root(workspace_id)
    probe = _run_git(root, ["rev-parse", "--is-inside-work-tree"])
    if probe.exit_code != 0 or probe.stdout.strip() != "true":
        raise InvalidRequestError(
            "Workspace is not a git repository (or git is unavailable).",
            detail=str(root),
        )
    return root


def _validate_rev(rev: str) -> str:
    rev = rev.strip()
    if not rev:
        raise InvalidRequestError("rev must not be empty.")
    if rev.startswith("-") or not _SAFE_REV_RE.match(rev):
        raise InvalidRequestError(
            "rev contains unsafe characters or option-like syntax.",
            detail=rev,
        )
    return rev


def _parse_branch_line(line: str) -> tuple[str | None, int | None, int | None]:
    rest = line[3:]
    branch = rest
    ahead: int | None = None
    behind: int | None = None
    if "[" in rest:
        branch = rest.split("[", 1)[0]
        meta = rest.split("[", 1)[1].rstrip("]")
        for part in meta.split(","):
            part = part.strip()
            if part.startswith("ahead"):
                ahead = int(part.split()[-1])
            elif part.startswith("behind"):
                behind = int(part.split()[-1])
    return branch.strip(), ahead, behind


def git_status(
    manager: WorkspaceManager,
    workspace_id: str,
    path: str | None = None,
) -> dict:
    root = _require_git_repo(manager, workspace_id)
    args = ["status", "--porcelain=v1", "-b", "--untracked-files=all"]
    if path:
        resolved = manager.resolve(workspace_id, path, must_exist=False)
        args.extend(["--", str(resolved.relative_to(root).as_posix())])
    result = _run_git(root, args)
    if result.exit_code != 0:
        raise ProcessExecutionError(
            f"git status failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    branch: str | None = None
    ahead: int | None = None
    behind: int | None = None
    entries: list[dict] = []
    for line in result.stdout.splitlines():
        if line.startswith("## "):
            branch, ahead, behind = _parse_branch_line(line)
            continue
        if len(line) < 4:
            continue
        entries.append(
            {
                "x": line[0],
                "y": line[1],
                "path": line[3:],
            }
        )
    return {
        "workspace_id": workspace_id,
        "branch": branch,
        "ahead": ahead,
        "behind": behind,
        "clean": not entries,
        "entries": entries,
    }


def git_diff(
    manager: WorkspaceManager,
    workspace_id: str,
    path: str | None = None,
    *,
    staged: bool = False,
    unified: int = 3,
) -> dict:
    root = _require_git_repo(manager, workspace_id)
    if unified < 1 or unified > 10:
        raise InvalidRequestError("unified must be between 1 and 10.")
    args = ["diff", "--no-ext-diff", f"--unified={unified}"]
    if staged:
        args.append("--staged")
    if path:
        resolved = manager.resolve(workspace_id, path, must_exist=False)
        args.extend(["--", str(resolved.relative_to(root).as_posix())])
    result = _run_git(root, args)
    if result.exit_code != 0:
        raise ProcessExecutionError(
            f"git diff failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    diff = result.stdout
    files: list[str] = []
    for line in diff.splitlines():
        match = _DIFF_HEADER_RE.match(line)
        if match:
            files.append(match.group(2) if match.group(2) != "/dev/null" else match.group(1))
    return {
        "workspace_id": workspace_id,
        "staged": staged,
        "files": sorted(set(files)),
        "diff": diff,
        "truncated": result.truncated,
    }


def git_log(
    manager: WorkspaceManager,
    workspace_id: str,
    *,
    max_count: int = 20,
    path: str | None = None,
) -> dict:
    root = _require_git_repo(manager, workspace_id)
    if max_count < 1 or max_count > 200:
        raise InvalidRequestError("max_count must be between 1 and 200.")
    args = [
        "log",
        f"-n {max_count}",
        "--format=%h%x09%an%x09%ad%x09%s",
        "--date=short",
    ]
    if path:
        resolved = manager.resolve(workspace_id, path, must_exist=False)
        args.extend(["--", str(resolved.relative_to(root).as_posix())])
    result = _run_git(root, args)
    if result.exit_code != 0:
        raise ProcessExecutionError(
            f"git log failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    entries: list[dict] = []
    for line in result.stdout.splitlines():
        parts = line.split("\t", 3)
        if len(parts) == 4:
            entries.append(
                {
                    "hash": parts[0],
                    "author": parts[1],
                    "date": parts[2],
                    "subject": parts[3],
                }
            )
    return {
        "workspace_id": workspace_id,
        "count": len(entries),
        "entries": entries,
    }


def git_show(
    manager: WorkspaceManager,
    workspace_id: str,
    rev: str = "HEAD",
    path: str | None = None,
) -> dict:
    root = _require_git_repo(manager, workspace_id)
    safe_rev = _validate_rev(rev)
    args = ["show", "--no-ext-diff", "--format=fuller", safe_rev]
    if path:
        resolved = manager.resolve(workspace_id, path, must_exist=False)
        args.extend(["--", str(resolved.relative_to(root).as_posix())])
    result = _run_git(root, args)
    if result.exit_code != 0:
        raise ProcessExecutionError(
            f"git show failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    return {
        "workspace_id": workspace_id,
        "rev": safe_rev,
        "output": result.stdout,
        "truncated": result.truncated,
    }


__all__ = ["git_diff", "git_log", "git_show", "git_status"]