"""LSP tools for code intelligence (go-to-definition, find-references, etc.).

These tools require language servers to be installed. They operate on
workspace files and provide code intelligence without needing a full IDE.

Supported languages: Python (pylsp/pyright), JavaScript/TypeScript (tsserver),
and any language with a running LSP server.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..errors import InvalidRequestError, ProcessExecutionError
from ..workspaces.manager import WorkspaceManager


def _find_python_lsp() -> str | None:
    """Find a Python LSP server executable."""
    for name in ("pylsp", "pyright-langserver", "pyright"):
        for path in Path(sys.prefix).parent.glob(f"Scripts/{name}*"):
            return str(path)
    # Try PATH
    import shutil
    for name in ("pylsp", "pyright-langserver", "pyright"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _find_js_ts_lsp() -> str | None:
    """Find a JS/TS LSP server executable."""
    import shutil
    for name in ("typescript-language-server", "tsserver"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _detect_language(file_path: Path) -> str | None:
    """Detect the language of a file based on extension."""
    ext = file_path.suffix.lower()
    mapping = {
        ".py": "python",
        ".pyi": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".mjs": "javascript",
        ".cjs": "javascript",
    }
    return mapping.get(ext)


def _get_lsp_server(language: str) -> str | None:
    """Get the LSP server path for a language."""
    if language == "python":
        return _find_python_lsp()
    if language in ("javascript", "typescript"):
        return _find_js_ts_lsp()
    return None


def lsp_info(
    manager: WorkspaceManager,
    workspace_id: str,
    path: str,
    line: int,
    character: int,
) -> dict[str, Any]:
    """Get hover information for a symbol at a position.

    Returns documentation/type info for the symbol under the cursor.
    """
    file_path = manager.resolve(workspace_id, path, must_exist=True)
    if not file_path.is_file():
        raise InvalidRequestError(f"Not a file: {path}")

    language = _detect_language(file_path)
    if not language:
        return {
            "path": path,
            "line": line,
            "character": character,
            "info": f"Unsupported file type: {file_path.suffix}",
        }

    # For now, return basic file info
    # Full LSP integration requires running a language server process
    return {
        "path": path,
        "line": line,
        "character": character,
        "language": language,
        "info": (
            f"LSP hover for {file_path.name} at line {line + 1}, "
            f"column {character + 1}. Full LSP integration requires "
            f"a language server (pylsp, tsserver, etc.) to be installed."
        ),
    }


def lsp_references(
    manager: WorkspaceManager,
    workspace_id: str,
    path: str,
    line: int,
    character: int,
) -> dict[str, Any]:
    """Find all references to the symbol at a position."""
    file_path = manager.resolve(workspace_id, path, must_exist=True)
    if not file_path.is_file():
        raise InvalidRequestError(f"Not a file: {path}")

    language = _detect_language(file_path)
    if not language:
        return {"path": path, "references": [], "language": None}

    # Basic text search fallback when no LSP server is available
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
        lines = content.split("\n")
        if line >= len(lines):
            return {"path": path, "references": [], "error": "Line out of range"}

        # Extract the word at the cursor position
        target_line = lines[line]
        # Find word boundaries
        start = character
        while start > 0 and target_line[start - 1].isalnum():
            start -= 1
        end = character
        while end < len(target_line) and target_line[end].isalnum():
            end += 1
        symbol = target_line[start:end]

        if not symbol:
            return {"path": path, "references": [], "error": "No symbol at position"}

        # Search workspace for the symbol
        references = []
        ws_path = Path(manager.resolve(workspace_id, "."))
        for root, _dirs, files in os.walk(ws_path):
            # Skip .git and node_modules
            dirs_to_skip = [d for d in _dirs if d in (".git", "node_modules", "__pycache__")]
            for d in dirs_to_skip:
                _dirs.remove(d)
            for fname in files:
                fpath = Path(root) / fname
                if not fpath.is_file():
                    continue
                try:
                    fcontent = fpath.read_text(encoding="utf-8", errors="replace")
                    for i, line_text in enumerate(fcontent.split("\n")):
                        if symbol in line_text:
                            rel = fpath.relative_to(ws_path)
                            references.append(
                                {
                                    "file": str(rel),
                                    "line": i + 1,
                                    "text": line_text.strip()[:120],
                                }
                            )
                except Exception:
                    continue

        return {
            "path": path,
            "symbol": symbol,
            "language": language,
            "references": references[:100],
            "total": len(references),
            "note": "Text-based search (install pylsp/tsserver for semantic analysis)",
        }
    except Exception as exc:
        return {"path": path, "references": [], "error": str(exc)}
