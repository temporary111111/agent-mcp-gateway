"""Persistent session memory for agentic workflows.

Saves and loads context across restarts. Memory is stored in
~/.agent-gateway/memory/<workspace_id>.json and loaded automatically
when the workspace is opened.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import CONFIG_DIR


MEMORY_DIR = CONFIG_DIR / "memory"


@dataclass
class MemoryEntry:
    key: str
    value: str
    timestamp: float
    tags: list[str] = field(default_factory=list)


@dataclass
class SessionMemory:
    _entries: dict[str, MemoryEntry] = field(default_factory=dict)
    _workspace_id: str = ""
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def init(self, workspace_id: str) -> None:
        """Initialize memory for a workspace, loading from disk if available."""
        with self._lock:
            self._workspace_id = workspace_id
            self._load_from_disk()

    def _load_from_disk(self) -> None:
        """Load memory entries from disk."""
        if not self._workspace_id:
            return
        path = MEMORY_DIR / f"{self._workspace_id}.json"
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for item in data.get("entries", []):
                entry = MemoryEntry(
                    key=item["key"],
                    value=item["value"],
                    timestamp=item.get("timestamp", time.time()),
                    tags=item.get("tags", []),
                )
                self._entries[entry.key] = entry
        except (json.JSONDecodeError, OSError):
            pass

    def _save_to_disk(self) -> None:
        """Save memory entries to disk."""
        if not self._workspace_id:
            return
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        path = MEMORY_DIR / f"{self._workspace_id}.json"
        data = {
            "workspace_id": self._workspace_id,
            "entries": [
                {
                    "key": e.key,
                    "value": e.value,
                    "timestamp": e.timestamp,
                    "tags": e.tags,
                }
                for e in self._entries.values()
            ],
        }
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def save(self, key: str, value: str, tags: list[str] | None = None) -> dict[str, Any]:
        """Save a memory entry. Overwrites if key exists."""
        with self._lock:
            self._entries[key] = MemoryEntry(
                key=key,
                value=value,
                timestamp=time.time(),
                tags=tags or [],
            )
            self._save_to_disk()
            return {"status": "saved", "key": key, "total": len(self._entries)}

    def recall(self, key: str) -> dict[str, Any]:
        """Recall a specific memory entry."""
        with self._lock:
            entry = self._entries.get(key)
            if not entry:
                return {"found": False, "key": key}
            return {
                "found": True,
                "key": entry.key,
                "value": entry.value,
                "tags": entry.tags,
                "saved_at": entry.timestamp,
            }

    def search(self, query: str, tags: list[str] | None = None) -> dict[str, Any]:
        """Search memory entries by query and optional tags."""
        with self._lock:
            results = []
            query_lower = query.lower()
            for entry in self._entries.values():
                # Tag filter
                if tags and not any(t in entry.tags for t in tags):
                    continue
                # Text match in key or value
                if query_lower in entry.key.lower() or query_lower in entry.value.lower():
                    results.append({
                        "key": entry.key,
                        "value": entry.value,
                        "tags": entry.tags,
                        "saved_at": entry.timestamp,
                    })
            return {"results": results, "count": len(results)}

    def delete(self, key: str) -> dict[str, Any]:
        """Delete a memory entry."""
        with self._lock:
            if key in self._entries:
                del self._entries[key]
                self._save_to_disk()
                return {"status": "deleted", "key": key, "remaining": len(self._entries)}
            return {"status": "not_found", "key": key}

    def list_all(self) -> dict[str, Any]:
        """List all memory entries."""
        with self._lock:
            entries = []
            for entry in sorted(self._entries.values(), key=lambda e: e.timestamp, reverse=True):
                entries.append({
                    "key": entry.key,
                    "value": entry.value[:200],  # Truncate long values
                    "tags": entry.tags,
                    "saved_at": entry.timestamp,
                })
            return {"entries": entries, "total": len(entries)}


# Process-global memory store
_session_memory = SessionMemory()


def init_memory(workspace_id: str) -> None:
    """Initialize memory for a workspace."""
    _session_memory.init(workspace_id)


def memory_save(key: str, value: str, tags: list[str] | None = None) -> dict[str, Any]:
    """Save a memory entry."""
    return _session_memory.save(key, value, tags)


def memory_recall(key: str) -> dict[str, Any]:
    """Recall a specific memory entry."""
    return _session_memory.recall(key)


def memory_search(query: str, tags: list[str] | None = None) -> dict[str, Any]:
    """Search memory entries."""
    return _session_memory.search(query, tags)


def memory_delete(key: str) -> dict[str, Any]:
    """Delete a memory entry."""
    return _session_memory.delete(key)


def memory_list() -> dict[str, Any]:
    """List all memory entries."""
    return _session_memory.list_all()
