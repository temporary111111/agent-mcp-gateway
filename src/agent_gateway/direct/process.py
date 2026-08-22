"""Deterministic process execution for direct mode.

Explicitly opt-in: without ``AGENT_ENABLE_COMMANDS=true`` every
``process_run`` call is rejected before anything starts. Executable +
argument-array semantics (no shell string). Hard timeout and output caps;
on timeout the process tree is killed.

Includes doom loop detection: warns when the same command is executed
3+ times with identical arguments in sequence.

Security note (documented, not hidden): a launched process runs with the
gateway user's OS privileges. AGENT_ALLOWED_ROOTS constrains where a
task can point the gateway, but it cannot sandbox what a launched
program itself can access.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from ..config import Config
from ..errors import CommandDisabledError, InvalidRequestError, ProcessExecutionError
from ..workspaces.manager import WorkspaceManager

_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
_POLL_INTERVAL = 0.05
_DOOM_LOOP_THRESHOLD = 3


class _DoomLoopTracker:
    """Track recent command executions to detect doom loops."""

    def __init__(self) -> None:
        self._recent: list[tuple[str, tuple[str, ...]]] = []
        self._lock = threading.Lock()

    def record(self, command: str, args: tuple[str, ...]) -> bool:
        """Record a command execution. Returns True if doom loop detected."""
        with self._lock:
            key = (command, args)
            self._recent.append(key)
            # Keep only last 10 entries
            if len(self._recent) > 10:
                self._recent = self._recent[-10:]

            # Check for doom loop: last N entries are identical
            if len(self._recent) >= _DOOM_LOOP_THRESHOLD:
                last_n = self._recent[-_DOOM_LOOP_THRESHOLD:]
                if all(entry == last_n[0] for entry in last_n):
                    return True
            return False

    def clear(self) -> None:
        """Clear the tracking history."""
        with self._lock:
            self._recent.clear()


# Process-global doom loop tracker
_doom_tracker = _DoomLoopTracker()


class _OutputCapture:
    def __init__(self, cap: int) -> None:
        self._cap = cap
        self._buffer = bytearray()
        self._lock = threading.Lock()
        self.truncated = False

    def write(self, chunk: bytes) -> None:
        with self._lock:
            remaining = self._cap - len(self._buffer)
            if remaining > 0:
                self._buffer.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    self.truncated = True
            else:
                self.truncated = True

    def text(self) -> str:
        with self._lock:
            return bytes(self._buffer).decode("utf-8", errors="replace")


def _reader(stream, capture: _OutputCapture) -> None:
    try:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                break
            capture.write(chunk)
    except Exception:
        pass


def _resolve_executable(
    manager: WorkspaceManager, workspace_id: str, executable: str
) -> str:
    if not executable or not executable.strip():
        raise InvalidRequestError("executable must not be empty.")
    executable = executable.strip()
    if "\x00" in executable or any(
        c in executable for c in ("\n", "\r", '"', "'")
    ):
        raise InvalidRequestError("Invalid executable name.")
    if os.path.isabs(executable):
        if not os.path.isfile(executable):
            raise ProcessExecutionError(
                "Executable does not exist.", detail=executable
            )
        return executable
    if os.sep in executable or "/" in executable:
        resolved = manager.resolve(
            workspace_id, executable, must_exist=True
        )
        return str(resolved)
    return executable


def process_run(
    config: Config,
    manager: WorkspaceManager,
    workspace_id: str,
    executable: str,
    args: list[str],
    *,
    cwd_relative: str = ".",
    timeout_seconds: int = 30,
    background: bool = False,
) -> dict:
    if not config.enable_commands:
        raise CommandDisabledError(
            "process_run is disabled: set AGENT_ENABLE_COMMANDS=true to "
            "allow the gateway to launch processes. A launched process "
            "runs with the gateway user's OS privileges; allowlists do "
            "not sandbox it."
        )
    if not isinstance(args, list) or not all(
        isinstance(a, str) for a in args
    ):
        raise InvalidRequestError(
            "args must be a list of strings (argument-array semantics)."
        )
    if timeout_seconds < 1 or timeout_seconds > config.process_timeout_max:
        raise InvalidRequestError(
            f"timeout_seconds must be between 1 and "
            f"{config.process_timeout_max} (AGENT_PROCESS_TIMEOUT_MAX)."
        )

    command = _resolve_executable(manager, workspace_id, executable)
    cwd = manager.resolve(workspace_id, cwd_relative, must_exist=True)
    if not cwd.is_dir():
        raise InvalidRequestError(
            "cwd_relative must point to a directory inside the workspace."
        )

    # Doom loop detection
    cmd_tuple = (command, tuple(args))
    doom_loop_detected = _doom_tracker.record(command, tuple(args))

    # Background mode: detach the process and return immediately
    if background:
        try:
            if sys.platform == "win32":
                process = subprocess.Popen(
                    [command, *args],
                    cwd=str(cwd),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    creationflags=(
                        _CREATE_NO_WINDOW
                        | subprocess.CREATE_NEW_PROCESS_GROUP
                    ),
                )
            else:
                process = subprocess.Popen(
                    [command, *args],
                    cwd=str(cwd),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                )
        except OSError as exc:
            raise ProcessExecutionError(
                f"Failed to start background process: {exc}",
                detail={"executable": executable, "args": args},
            ) from exc

        return {
            "workspace_id": workspace_id,
            "command": [executable, *args],
            "cwd": str(cwd),
            "pid": process.pid,
            "background": True,
            "message": (
                f"Process started in background (PID {process.pid}). "
                f"It will continue running after this call returns."
            ),
        }

    # Foreground mode: capture output and wait for completion
    capture_out = _OutputCapture(config.max_process_output_bytes)
    capture_err = _OutputCapture(config.max_process_output_bytes)

    started = time.monotonic()
    try:
        process = subprocess.Popen(
            [command, *args],
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            creationflags=_CREATE_NO_WINDOW,
        )
    except OSError as exc:
        raise ProcessExecutionError(
            f"Failed to start process: {exc}",
            detail={"executable": executable, "args": args},
        ) from exc

    threads = [
        threading.Thread(target=_reader, args=(process.stdout, capture_out), daemon=True),
        threading.Thread(target=_reader, args=(process.stderr, capture_err), daemon=True),
    ]
    for thread in threads:
        thread.start()

    timed_out = False
    deadline = time.monotonic() + timeout_seconds
    while process.poll() is None:
        if time.monotonic() >= deadline:
            timed_out = True
            _kill_tree(process)
            break
        time.sleep(_POLL_INTERVAL)

    exit_code = process.wait()
    for thread in threads:
        thread.join(timeout=5)

    duration = round(time.monotonic() - started, 3)
    result = {
        "workspace_id": workspace_id,
        "command": [executable, *args],
        "cwd": str(cwd),
        "exit_code": exit_code,
        "stdout": capture_out.text(),
        "stderr": capture_err.text(),
        "duration_seconds": duration,
        "timed_out": timed_out,
        "truncated_stdout": capture_out.truncated,
        "truncated_stderr": capture_err.truncated,
    }

    if doom_loop_detected:
        result["doom_loop_warning"] = (
            f"WARNING: The same command has been executed {_DOOM_LOOP_THRESHOLD} "
            f"times in a row: {executable} {' '.join(args)[:100]}. "
            f"This may indicate a loop. Consider trying a different approach."
        )
        _doom_tracker.clear()

    return result


def _kill_tree(process: subprocess.Popen) -> None:
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
            )
            return
        except Exception:
            pass
    try:
        process.kill()
    except OSError:
        pass


__all__ = ["process_run"]