"""AGENTS.md loader for project-specific instructions.

Searches for AGENTS.md in the workspace root and parent directories.
Loaded automatically when workspace_open is called. The content is
included in the workspace_open response so the AI can read it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# File names to search for (in order of priority)
AGENTS_FILE_NAMES = [
    "AGENTS.md",
    "CLAUDE.md",
    "GATEWAY.md",
    ".agents/AGENTS.md",
    ".claude/CLAUDE.md",
]


def load_agents_md(workspace_path: Path) -> dict[str, Any]:
    """Search for and load AGENTS.md files from the workspace.

    Returns a dict with found files and their contents.
    Searches the workspace root and up to 3 parent directories.
    """
    found_files: list[dict[str, str]] = []

    # Search workspace root and up to 3 parents
    search_dirs = [workspace_path]
    current = workspace_path
    for _ in range(3):
        parent = current.parent
        if parent == current:
            break
        search_dirs.append(parent)
        current = parent

    for directory in search_dirs:
        for filename in AGENTS_FILE_NAMES:
            filepath = directory / filename
            if filepath.is_file():
                try:
                    content = filepath.read_text(encoding="utf-8")
                    if content.strip():
                        rel_path = str(filepath.relative_to(workspace_path)) if filepath != workspace_path else filename
                        found_files.append({
                            "path": rel_path,
                            "absolute_path": str(filepath),
                            "content": content,
                            "source": "workspace" if directory == workspace_path else "parent",
                        })
                except (OSError, UnicodeDecodeError):
                    continue

    return {
        "found": len(found_files) > 0,
        "files": found_files,
        "instructions": "\n\n---\n\n".join(
            f"# {f['path']}\n\n{f['content']}" for f in found_files
        ) if found_files else "",
        "count": len(found_files),
    }
