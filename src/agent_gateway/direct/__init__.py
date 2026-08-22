"""Direct-mode primitives: deterministic, no LLM, no backend."""

from __future__ import annotations

from .agents_md import load_agents_md
from .browser import browser_open
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
from .lsp import lsp_info, lsp_references
from .mode import get_mode, is_plan_mode, set_mode
from .process import process_run
from .search import code_search
from .todo import todo_read, todo_write
from .web import web_fetch, web_search

__all__ = [
    "browser_open",
    "code_search",
    "file_apply_patch",
    "file_find",
    "file_read",
    "file_replace",
    "file_stat",
    "file_write",
    "get_mode",
    "git_diff",
    "git_log",
    "git_show",
    "git_status",
    "is_plan_mode",
    "load_agents_md",
    "lsp_info",
    "lsp_references",
    "process_run",
    "set_mode",
    "sha256_file",
    "todo_read",
    "todo_write",
    "web_fetch",
    "web_search",
    "workspace_tree",
]
