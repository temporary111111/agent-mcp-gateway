"""Directory authorization.

Remote (potentially untrusted) callers may only delegate tasks into
directories that the operator explicitly listed in AGENT_ALLOWED_ROOTS.
Every task directory is canonicalized and verified to remain inside one
of those roots; anything else is rejected before any backend call.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..errors import InvalidRequestError, UnauthorizedDirectoryError


def _normalize(path: Path) -> str:
    return os.path.normcase(str(path))


class PathPolicy:
    def __init__(self, allowed_roots: list[Path] | tuple[Path, ...]) -> None:
        self._roots = tuple(self._canonicalize(root) for root in allowed_roots)

    @staticmethod
    def _canonicalize(raw: str | Path) -> Path:
        return Path(os.path.abspath(os.path.expanduser(str(raw)))).resolve()

    def allowed_roots(self) -> list[str]:
        return [str(root) for root in self._roots]

    def _is_within(self, candidate: Path, root: Path) -> bool:
        return PathPolicy.is_within(candidate, root)

    @staticmethod
    def is_within(candidate: Path, root: Path) -> bool:
        candidate_norm = _normalize(candidate)
        root_norm = _normalize(root)
        if candidate_norm == root_norm:
            return True
        prefix = root_norm if root_norm.endswith(os.sep) else root_norm + os.sep
        return candidate_norm.startswith(prefix)

    def resolve_task_directory(
        self,
        raw: str,
        *,
        must_exist: bool = True,
    ) -> Path:
        """Validate and canonicalize a task directory.

        Rejects: empty paths, non-existent directories (when required),
        filesystem roots, and anything outside the configured roots.
        """
        if not raw or not raw.strip():
            raise InvalidRequestError("Task directory must not be empty.")

        candidate = self._canonicalize(raw)

        if candidate.parent == candidate:
            raise UnauthorizedDirectoryError(
                "A filesystem root is not an allowed task directory.",
                detail=str(candidate),
            )

        if not self._roots:
            raise UnauthorizedDirectoryError(
                "No task directories are allowed: AGENT_ALLOWED_ROOTS is "
                "not configured."
            )

        inside = any(
            self._is_within(candidate, root) for root in self._roots
        )
        if not inside:
            raise UnauthorizedDirectoryError(
                "Task directory is outside the allowed roots.",
                detail=str(candidate),
            )

        if must_exist and not candidate.is_dir():
            raise InvalidRequestError(
                "Task directory does not exist.",
                detail=str(candidate),
            )

        return candidate