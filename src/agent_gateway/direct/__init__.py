"""Direct-mode primitives: deterministic, no LLM, no backend."""

from __future__ import annotations

from .filesystem import (
    file_apply_patch,
    file_find,
    file_read,
    file_replace,
    file_stat,
    file_write,
    workspace_tree,
)
from .git import git_diff, git_log, git_show, git_status
from .hashing import sha256_file
from .process import process_run
from .search import code_search

__all__ = [
    "code_search",
    "file_apply_patch",
    "file_find",
    "file_read",
    "file_replace",
    "file_stat",
    "file_write",
    "git_diff",
    "git_log",
    "git_show",
    "git_status",
    "process_run",
    "sha256_file",
    "workspace_tree",
]