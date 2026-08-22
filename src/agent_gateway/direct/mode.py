"""Plan/Build mode toggle for agentic workflows.

Plan mode: read-only analysis, no file modifications allowed.
Build mode: full access, can read/write/execute.
"""

from __future__ import annotations

import threading
from typing import Any


class ModeStore:
    """Tracks current mode (plan or build) per workspace."""

    def __init__(self) -> None:
        self._modes: dict[str, str] = {}
        self._lock = threading.Lock()

    def get_mode(self, workspace_id: str) -> str:
        """Get current mode for a workspace."""
        with self._lock:
            return self._modes.get(workspace_id, "build")

    def set_mode(self, workspace_id: str, mode: str) -> dict[str, Any]:
        """Set mode for a workspace."""
        if mode not in ("plan", "build"):
            return {"error": f"Invalid mode: {mode}. Use 'plan' or 'build'."}
        with self._lock:
            old_mode = self._modes.get(workspace_id, "build")
            self._modes[workspace_id] = mode
            return {
                "workspace_id": workspace_id,
                "previous_mode": old_mode,
                "current_mode": mode,
                "description": (
                    "Read-only analysis mode. You can explore code, "
                    "search, and plan, but cannot write files or run commands."
                    if mode == "plan"
                    else "Full access mode. You can read, write, edit, "
                    "and run commands."
                ),
                "allowed_in_plan": [
                    "workspace_tree", "file_read", "file_stat",
                    "file_find", "code_search", "git_status",
                    "git_diff", "git_log", "git_show",
                    "web_fetch", "web_search", "todo_write",
                    "todo_read", "lsp_info", "lsp_references",
                    "browser_open", "memory_*",
                ],
                "blocked_in_plan": [
                    "file_write", "file_replace", "file_apply_patch",
                    "process_run",
                ],
            }


# Process-global mode store
_mode_store = ModeStore()


def get_mode(workspace_id: str) -> str:
    """Get current mode for a workspace."""
    return _mode_store.get_mode(workspace_id)


def set_mode(workspace_id: str, mode: str) -> dict[str, Any]:
    """Set mode for a workspace."""
    return _mode_store.set_mode(workspace_id, mode)


def is_plan_mode(workspace_id: str) -> bool:
    """Check if a workspace is in plan mode."""
    return get_mode(workspace_id) == "plan"
