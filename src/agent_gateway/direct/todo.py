"""In-memory task tracking for agentic workflows."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TodoItem:
    id: int
    content: str
    status: str = "pending"  # pending, in_progress, completed


@dataclass
class TodoStore:
    _items: list[TodoItem] = field(default_factory=list)
    _next_id: int = 1
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def write(self, items: list[dict[str, str]]) -> dict[str, Any]:
        """Replace the entire todo list with new items.

        Each item dict should have 'content' and optional 'status'.
        """
        with self._lock:
            self._items.clear()
            for item in items:
                content = item.get("content", "").strip()
                if not content:
                    continue
                status = item.get("status", "pending")
                if status not in ("pending", "in_progress", "completed"):
                    status = "pending"
                self._items.append(
                    TodoItem(id=self._next_id, content=content, status=status)
                )
                self._next_id += 1
            return self._read_internal()

    def read(self) -> dict[str, Any]:
        """Read the current todo list."""
        with self._lock:
            return self._read_internal()

    def _read_internal(self) -> dict[str, Any]:
        return {
            "items": [
                {"id": item.id, "content": item.content, "status": item.status}
                for item in self._items
            ],
            "total": len(self._items),
            "pending": sum(1 for i in self._items if i.status == "pending"),
            "in_progress": sum(1 for i in self._items if i.status == "in_progress"),
            "completed": sum(1 for i in self._items if i.status == "completed"),
        }


# Process-global store
_todo_store = TodoStore()


def todo_write(items: list[dict[str, str]]) -> dict[str, Any]:
    """Replace the todo list with new items."""
    return _todo_store.write(items)


def todo_read() -> dict[str, Any]:
    """Read the current todo list."""
    return _todo_store.read()
