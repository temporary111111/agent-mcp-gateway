"""Unit tests for deterministic process execution."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from agent_gateway.config import Config
from agent_gateway.direct.process import process_run
from agent_gateway.errors import (
    CommandDisabledError,
    InvalidRequestError,
    ProcessExecutionError,
)
from agent_gateway.security.paths import PathPolicy
from agent_gateway.workspaces.manager import WorkspaceManager

PYTHON = sys.executable


@pytest.fixture
def enabled_config() -> Config:
    return Config.from_env(
        {
            "AGENT_ENABLE_COMMANDS": "true",
            "AGENT_ALLOWED_ROOTS": "C:/x",
        }
    )


@pytest.fixture
def manager(tmp_path: Path) -> tuple[WorkspaceManager, Path]:
    return WorkspaceManager(PathPolicy([tmp_path])), tmp_path


@pytest.fixture
def ws(manager) -> str:
    return manager[0].open(str(manager[1]))["workspace_id"]


def test_process_disabled_by_default(manager, ws: str) -> None:
    config = Config.from_env({"AGENT_ALLOWED_ROOTS": "C:/x"})
    assert config.enable_commands is False
    with pytest.raises(CommandDisabledError):
        process_run(config, manager[0], ws, PYTHON, ["-c", "print(1)"])


def test_process_run_echo(manager, ws: str, enabled_config: Config) -> None:
    result = process_run(
        enabled_config,
        manager[0],
        ws,
        PYTHON,
        ["-c", "print('hello world')"],
    )
    assert result["exit_code"] == 0
    assert "hello world" in result["stdout"]
    assert result["timed_out"] is False
    assert result["truncated_stdout"] is False
    assert result["duration_seconds"] >= 0


def test_process_run_exit_code_and_stderr(
    manager, ws: str, enabled_config: Config
) -> None:
    result = process_run(
        enabled_config,
        manager[0],
        ws,
        PYTHON,
        ["-c", "import sys; print('boom', file=sys.stderr); sys.exit(3)"],
    )
    assert result["exit_code"] == 3
    assert "boom" in result["stderr"]


def test_process_run_timeout_kills(manager, ws: str, enabled_config: Config) -> None:
    start = __import__("time").monotonic()
    result = process_run(
        enabled_config,
        manager[0],
        ws,
        PYTHON,
        ["-c", "import time; time.sleep(60)"],
        timeout_seconds=2,
    )
    elapsed = __import__("time").monotonic() - start
    assert result["timed_out"] is True
    assert elapsed < 30
    assert result["exit_code"] != 0


def test_process_output_truncation(manager, ws: str, enabled_config: Config) -> None:
    config = Config.from_env(
        {
            "AGENT_ENABLE_COMMANDS": "true",
            "AGENT_MAX_PROCESS_OUTPUT_BYTES": "1000",
            "AGENT_ALLOWED_ROOTS": "C:/x",
        }
    )
    result = process_run(
        config,
        manager[0],
        ws,
        PYTHON,
        ["-c", "print('x' * 5000)"],
    )
    assert result["truncated_stdout"] is True
    assert len(result["stdout"]) <= 1100


def test_process_run_cwd_inside_workspace(manager, ws: str, enabled_config: Config) -> None:
    _, root = manager
    (root / "workdir").mkdir()
    (root / "workdir" / "marker.txt").write_text("present", encoding="utf-8")
    result = process_run(
        enabled_config,
        manager[0],
        ws,
        PYTHON,
        ["-c", "import os; print(os.path.exists('marker.txt'))"],
        cwd_relative="workdir",
    )
    assert "True" in result["stdout"]


def test_process_run_bad_cwd_rejected(manager, ws: str, enabled_config: Config) -> None:
    with pytest.raises(InvalidRequestError):
        process_run(
            enabled_config,
            manager[0],
            ws,
            PYTHON,
            ["-c", "pass"],
            cwd_relative="nope",
        )


def test_process_run_args_must_be_strings(
    manager, ws: str, enabled_config: Config
) -> None:
    with pytest.raises(InvalidRequestError):
        process_run(enabled_config, manager[0], ws, PYTHON, ["-c", 42])


def test_process_run_timeout_bounds(manager, ws: str, enabled_config: Config) -> None:
    with pytest.raises(InvalidRequestError):
        process_run(enabled_config, manager[0], ws, PYTHON, ["-c", "pass"], timeout_seconds=0)
    with pytest.raises(InvalidRequestError):
        process_run(
            enabled_config, manager[0], ws, PYTHON, ["-c", "pass"], timeout_seconds=999999
        )


def test_process_run_missing_executable(manager, ws: str, enabled_config: Config) -> None:
    with pytest.raises(ProcessExecutionError):
        process_run(enabled_config, manager[0], ws, "definitely-not-a-real-exe-xyz", [])


def test_process_run_escape_cwd_rejected(manager, ws: str, enabled_config: Config) -> None:
    with pytest.raises(InvalidRequestError):
        process_run(
            enabled_config,
            manager[0],
            ws,
            PYTHON,
            ["-c", "pass"],
            cwd_relative="../outside",
        )