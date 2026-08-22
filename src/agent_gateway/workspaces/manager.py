"""Direct-mode workspace handles.

``workspace_open`` binds a canonical directory to an opaque workspace ID.
Every subsequent direct tool takes a relative path inside that workspace;
the manager re-checks containment (including symlink resolution) at the
point of every use. Handles are persisted to disk so they survive MCP
session disconnections — when ChatGPT reconnects, existing workspace
bindings are restored automatically.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path

from ..config import CONFIG_DIR
from ..errors import InvalidRequestError, InvalidWorkspaceError
from ..security.paths import PathPolicy

_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_WORKSPACE_ID_PREFIX = "ws_"
_WORKSPACE_INDEX = CONFIG_DIR / "workspaces.json"


class WorkspaceManager:
    def __init__(self, path_policy: PathPolicy) -> None:
        self._path_policy = path_policy
        self._workspaces: dict[str, Path] = {}
        self._load_from_disk()

    def _load_from_disk(self) -> None:
        """Load persisted workspace mappings from disk."""
        if not _WORKSPACE_INDEX.is_file():
            return
        try:
            data = json.loads(_WORKSPACE_INDEX.read_text(encoding="utf-8"))
            for ws_id, dir_path in data.items():
                path = Path(dir_path)
                if path.exists():
                    self._workspaces[ws_id] = path
        except (json.JSONDecodeError, OSError):
            pass

    def _save_to_disk(self) -> None:
        """Persist workspace mappings to disk."""
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = {ws_id: str(p) for ws_id, p in self._workspaces.items()}
        _WORKSPACE_INDEX.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def open(self, directory: str) -> dict:
        """Validate *directory* and bind it to a workspace ID.

        If the directory is already bound, return the existing workspace ID
        instead of creating a new one. This allows reconnection to work
        seamlessly.
        """
        resolved = self._path_policy.resolve_task_directory(directory)

        # Check if directory is already bound
        for ws_id, path in self._workspaces.items():
            if path == resolved:
                return {
                    "workspace_id": ws_id,
                    "root": str(resolved),
                    "reconnected": True,
                }

        # Create new binding
        workspace_id = f"{_WORKSPACE_ID_PREFIX}{uuid.uuid4().hex}"
        self._workspaces[workspace_id] = resolved
        self._save_to_disk()
        return {
            "workspace_id": workspace_id,
            "root": str(resolved),
            "reconnected": False,
        }

    def root(self, workspace_id: str) -> Path:
        root = self._workspaces.get(workspace_id)
        if root is None:
            # Try reloading from disk
            self._load_from_disk()
            root = self._workspaces.get(workspace_id)
        if root is None:
            raise InvalidWorkspaceError(
                "Unknown or expired workspace ID. Call workspace_open again; "
                "handles are invalidated on gateway restart.",
                detail=workspace_id,
            )
        return root

    def resolve(
        self,
        workspace_id: str,
        relative_path: str,
        *,
        must_exist: bool = False,
    ) -> Path:
        """Resolve a workspace-relative path with strict containment.

        Rejects: unknown workspace IDs, absolute paths, drive-qualified
        paths, traversal (``..``) segments, and null bytes. Symlinks in
        the path are resolved and the final target must remain inside the
        workspace root.
        """
        root = self.root(workspace_id)

        if not isinstance(relative_path, str) or not relative_path.strip():
            raise InvalidRequestError(
                "Path must be a non-empty workspace-relative path.",
                detail=repr(relative_path),
            )
        relative_path = relative_path.strip()
        if os.path.isabs(relative_path) or _DRIVE_RE.match(relative_path):
            raise InvalidRequestError(
                "Path must be relative to the workspace, not absolute.",
                detail=relative_path,
            )
        if "\x00" in relative_path:
            raise InvalidRequestError("Path contains a null byte.")
        parts = re.split(r"[\\/]+", relative_path)
        if any(part == ".." for part in parts):
            raise InvalidRequestError(
                "Path traversal is not allowed.",
                detail=relative_path,
            )

        candidate = root.joinpath(relative_path)
        resolved = candidate.resolve(strict=False)
        if not PathPolicy.is_within(resolved, root):
            raise InvalidRequestError(
                "Path escapes the workspace (traversal or symlink).",
                detail=relative_path,
            )

        if must_exist and not resolved.exists():
            raise InvalidRequestError(
                "Path does not exist in the workspace.",
                detail=relative_path,
            )
        return resolved

    def close(self, workspace_id: str) -> bool:
        removed = self._workspaces.pop(workspace_id, None) is not None
        if removed:
            self._save_to_disk()
        return removed

    def count(self) -> int:
        return len(self._workspaces)


__all__ = ["WorkspaceManager"]