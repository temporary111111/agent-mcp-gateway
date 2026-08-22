"""Direct-mode workspace handles.

``workspace_open`` binds a canonical directory to an opaque workspace ID.
Every subsequent direct tool takes a relative path inside that workspace;
the manager re-checks containment (including symlink resolution) at the
point of every use. Handles are in-memory: a gateway restart invalidates
them (V2 does not require persistence).
"""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

from ..errors import InvalidRequestError, InvalidWorkspaceError
from ..security.paths import PathPolicy

_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_WORKSPACE_ID_PREFIX = "ws_"


class WorkspaceManager:
    def __init__(self, path_policy: PathPolicy) -> None:
        self._path_policy = path_policy
        self._workspaces: dict[str, Path] = {}

    def open(self, directory: str) -> dict:
        """Validate *directory* and bind it to a fresh opaque ID."""
        resolved = self._path_policy.resolve_task_directory(directory)
        workspace_id = f"{_WORKSPACE_ID_PREFIX}{uuid.uuid4().hex}"
        self._workspaces[workspace_id] = resolved
        return {
            "workspace_id": workspace_id,
            "root": str(resolved),
        }

    def root(self, workspace_id: str) -> Path:
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
        return self._workspaces.pop(workspace_id, None) is not None

    def count(self) -> int:
        return len(self._workspaces)


__all__ = ["WorkspaceManager"]