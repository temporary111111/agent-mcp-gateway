"""Direct-mode MCP tools: deterministic primitives with no LLM dependency.

GPT-5.6 Sol (the client model) owns the reasoning loop; these tools only
perform deterministic filesystem, search, process, and git operations
inside authorized workspaces.

NOTE: no `from __future__ import annotations` here on purpose. Tool
functions are defined inside build_direct_tools where `config` is a
closure parameter; string annotations are later re-evaluated against
module globals by the MCP SDK (inspect.signature eval_str=True) and
would fail with NameError. Eager annotations resolve at definition time
in the correct scope.
"""

from typing import Annotated

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from .. import direct as _direct
from ..config import Config
from ..logging import get_logger
from ..workspaces.manager import WorkspaceManager
from .helpers import tool_handler

logger = get_logger("tools.direct")

READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    open_world_hint=False,
)

MUTATING = ToolAnnotations(
    read_only_hint=False,
    open_world_hint=False,
)

WORKSPACE_ID = Annotated[
    str,
    Field(
        pattern=r"^ws_",
        description="Opaque workspace ID returned by workspace_open.",
    ),
]

RELPATH = Annotated[
    str,
    Field(
        description=(
            "Workspace-relative path (forward or back slashes). Must not "
            "be absolute and must not contain '..'."
        ),
    ),
]

DIRPATH = Annotated[
    str,
    Field(
        description=(
            "Workspace-relative directory path ('.' for the workspace "
            "root)."
        ),
    ),
]


def register_direct_tools(
    mcp: MCPServer,
    manager: WorkspaceManager,
    config: Config,
) -> None:
    @mcp.tool(
        title="Open a local workspace",
        annotations=MUTATING,
    )
    @tool_handler
    async def workspace_open(
        directory: Annotated[
            str,
            Field(
                description=(
                    "Absolute path of the directory to work in. Must be "
                    "inside an operator-allowed root (AGENT_ALLOWED_ROOTS)."
                ),
            ),
        ] = ...,
    ) -> dict:
        """Validate `directory` and bind it to an opaque workspace ID. All
        other direct tools take this ID plus workspace-relative paths.
        Handles expire on gateway restart; reopen with workspace_open."""
        return manager.open(directory)

    @mcp.tool(
        title="Workspace directory tree",
        annotations=READ_ONLY,
    )
    @tool_handler
    async def workspace_tree(
        workspace_id: WORKSPACE_ID = ...,
        path: DIRPATH = ".",
        max_depth: Annotated[
            int,
            Field(ge=1, le=20, description="Maximum recursion depth."),
        ] = 4,
        max_entries: Annotated[
            int,
            Field(
                ge=1,
                le=100000,
                description="Maximum entries to return (truncated flag set).",
            ),
        ] = 1000,
        include_hidden: Annotated[
            bool,
            Field(description="Include dot-files/dot-directories."),
        ] = False,
    ) -> dict:
        """List the directory tree inside the workspace (sorted, capped).
        Returns entries with relative paths, type, and size."""
        return _direct.workspace_tree(
            manager,
            workspace_id,
            path,
            max_depth=max_depth,
            max_entries=max_entries,
            include_hidden=include_hidden,
        )

    @mcp.tool(
        title="Read a file",
        annotations=READ_ONLY,
    )
    @tool_handler
    async def file_read(
        workspace_id: WORKSPACE_ID = ...,
        path: RELPATH = ...,
        offset_bytes: Annotated[
            int,
            Field(ge=0, description="Byte offset to start reading at."),
        ] = 0,
        max_bytes: Annotated[
            int,
            Field(
                ge=1,
                le=config.max_read_bytes,
                description="Maximum bytes to read.",
            ),
        ] = 0,
        max_lines: Annotated[
            int | None,
            Field(ge=1, description="Maximum lines to return."),
        ] = None,
        line_numbers: Annotated[
            bool,
            Field(description="Prefix content lines with line numbers."),
        ] = True,
    ) -> dict:
        """Read a text file with line numbers; binary files return
        metadata plus a small hex preview instead of content."""
        return _direct.file_read(
            manager,
            workspace_id,
            path,
            offset_bytes=offset_bytes,
            max_bytes=max_bytes or config.max_read_bytes,
            max_lines=max_lines,
            line_numbers=line_numbers,
        )

    @mcp.tool(
        title="File metadata",
        annotations=READ_ONLY,
    )
    @tool_handler
    async def file_stat(
        workspace_id: WORKSPACE_ID = ...,
        path: RELPATH = ...,
    ) -> dict:
        """Return size, mtime, type, sha256, and binary flag for a file."""
        return _direct.file_stat(manager, workspace_id, path)

    @mcp.tool(
        title="Find files by name pattern",
        annotations=READ_ONLY,
    )
    @tool_handler
    async def file_find(
        workspace_id: WORKSPACE_ID = ...,
        pattern: Annotated[
            str,
            Field(
                min_length=1,
                description="Glob pattern, e.g. '*.py' or 'tests/**/*.py'.",
            ),
        ] = ...,
        path: DIRPATH = ".",
        max_results: Annotated[
            int,
            Field(ge=1, le=100000, description="Maximum matches to return."),
        ] = 200,
        include_hidden: Annotated[
            bool,
            Field(description="Include dot-files."),
        ] = False,
    ) -> dict:
        """Find files by name glob inside the workspace (sorted, capped)."""
        return _direct.file_find(
            manager,
            workspace_id,
            pattern,
            path,
            max_results=max_results,
            include_hidden=include_hidden,
        )

    @mcp.tool(
        title="Search file contents",
        annotations=READ_ONLY,
    )
    @tool_handler
    async def code_search(
        workspace_id: WORKSPACE_ID = ...,
        pattern: Annotated[
            str,
            Field(
                min_length=1,
                description="Regular expression or literal text to find.",
            ),
        ] = ...,
        path: DIRPATH = ".",
        max_results: Annotated[
            int,
            Field(ge=1, le=100000, description="Maximum matches to return."),
        ] = 200,
        case_sensitive: Annotated[
            bool,
            Field(description="Case-sensitive matching."),
        ] = False,
        is_regex: Annotated[
            bool,
            Field(description="Treat pattern as a regular expression."),
        ] = True,
        include_hidden: Annotated[
            bool,
            Field(description="Include dot-files."),
        ] = False,
    ) -> dict:
        """Line-based content search over text files. Binary files are
        skipped; results include file, line number, column, and snippet."""
        return _direct.code_search(
            manager,
            workspace_id,
            pattern,
            path,
            max_results=max_results,
            max_file_bytes=config.max_read_bytes,
            case_sensitive=case_sensitive,
            is_regex=is_regex,
            include_hidden=include_hidden,
        )

    @mcp.tool(
        title="Write a file",
        annotations=MUTATING,
    )
    @tool_handler
    async def file_write(
        workspace_id: WORKSPACE_ID = ...,
        path: RELPATH = ...,
        content: Annotated[
            str,
            Field(description="Full new content of the file (UTF-8)."),
        ] = ...,
        expected_sha256: Annotated[
            str | None,
            Field(
                description=(
                    "SHA-256 of the content as last read (from file_read/"
                    "file_stat). Required before overwriting an existing "
                    "file to avoid stale writes."
                ),
            ),
        ] = None,
    ) -> dict:
        """Create or overwrite a file atomically. For existing files pass
        expected_sha256 from a prior read; a mismatch is rejected."""
        return _direct.file_write(
            manager,
            workspace_id,
            path,
            content,
            expected_sha256=expected_sha256,
        )

    @mcp.tool(
        title="Replace text in a file",
        annotations=MUTATING,
    )
    @tool_handler
    async def file_replace(
        workspace_id: WORKSPACE_ID = ...,
        path: RELPATH = ...,
        old_string: Annotated[
            str,
            Field(min_length=1, description="Exact text to replace."),
        ] = ...,
        new_string: Annotated[
            str,
            Field(description="Replacement text."),
        ] = ...,
        occurrence: Annotated[
            int,
            Field(
                ge=0,
                description=(
                    "0 replaces all occurrences; positive n replaces only "
                    "the n-th (1-based)."
                ),
            ),
        ] = 0,
        expected_sha256: Annotated[
            str | None,
            Field(
                description="SHA-256 from a prior read; mismatch is rejected.",
            ),
        ] = None,
    ) -> dict:
        """Replace exact text in an existing file (atomic, hashed)."""
        return _direct.file_replace(
            manager,
            workspace_id,
            path,
            old_string,
            new_string,
            occurrence=occurrence,
            expected_sha256=expected_sha256,
        )

    @mcp.tool(
        title="Apply a unified diff",
        annotations=MUTATING,
    )
    @tool_handler
    async def file_apply_patch(
        workspace_id: WORKSPACE_ID = ...,
        path: RELPATH = ...,
        patch: Annotated[
            str,
            Field(
                description=(
                    "git-style unified diff. May create a new file "
                    "(--- /dev/null) or modify an existing one. All hunks "
                    "must match current content or nothing is written."
                ),
            ),
        ] = ...,
        expected_sha256: Annotated[
            str | None,
            Field(
                description="SHA-256 from a prior read; mismatch is rejected.",
            ),
        ] = None,
    ) -> dict:
        """Apply a unified diff to a workspace file. Returns before/after
        hashes. Never commits; review with git_diff."""
        return _direct.file_apply_patch(
            manager,
            workspace_id,
            path,
            patch,
            expected_sha256=expected_sha256,
        )

    @mcp.tool(
        title="Run a process",
        annotations=MUTATING,
    )
    @tool_handler
    async def process_run(
        workspace_id: WORKSPACE_ID = ...,
        executable: Annotated[
            str,
            Field(
                description=(
                    "Executable: a bare name resolved via PATH (python, "
                    "git, npm), an absolute path, or a workspace-relative "
                    "path."
                ),
            ),
        ] = ...,
        args: Annotated[
            list[str],
            Field(
                description="Argument array (no shell string interpretation).",
            ),
        ] = ...,
        cwd_relative: DIRPATH = ".",
        timeout_seconds: Annotated[
            int,
            Field(
                ge=1,
                le=config.process_timeout_max,
                description="Hard deadline; the process tree is killed.",
            ),
        ] = 30,
    ) -> dict:
        """Launch a process inside the workspace with argument-array
        semantics, hard timeout, and output caps. Disabled unless
        AGENT_ENABLE_COMMANDS=true. A launched process runs with the
        gateway user's OS privileges."""
        return _direct.process_run(
            config,
            manager,
            workspace_id,
            executable,
            args,
            cwd_relative=cwd_relative,
            timeout_seconds=timeout_seconds,
        )

    @mcp.tool(
        title="Git status",
        annotations=READ_ONLY,
    )
    @tool_handler
    async def git_status(
        workspace_id: WORKSPACE_ID = ...,
        path: Annotated[
            str | None,
            Field(description="Optional workspace-relative path filter."),
        ] = None,
    ) -> dict:
        """Read-only working-tree status of the workspace git repository."""
        return _direct.git_status(manager, workspace_id, path)

    @mcp.tool(
        title="Git diff",
        annotations=READ_ONLY,
    )
    @tool_handler
    async def git_diff(
        workspace_id: WORKSPACE_ID = ...,
        path: Annotated[
            str | None,
            Field(description="Optional workspace-relative path filter."),
        ] = None,
        staged: Annotated[
            bool,
            Field(description="Show staged changes instead of unstaged."),
        ] = False,
        unified: Annotated[
            int,
            Field(ge=1, le=10, description="Context lines per hunk."),
        ] = 3,
    ) -> dict:
        """Read-only diff of working-tree changes against the index."""
        return _direct.git_diff(
            manager, workspace_id, path, staged=staged, unified=unified
        )

    @mcp.tool(
        title="Git log",
        annotations=READ_ONLY,
    )
    @tool_handler
    async def git_log(
        workspace_id: WORKSPACE_ID = ...,
        max_count: Annotated[
            int,
            Field(ge=1, le=200, description="Maximum commits to return."),
        ] = 20,
        path: Annotated[
            str | None,
            Field(description="Optional workspace-relative path filter."),
        ] = None,
    ) -> dict:
        """Read-only commit history (hash, author, date, subject)."""
        return _direct.git_log(manager, workspace_id, max_count=max_count, path=path)

    @mcp.tool(
        title="Git show",
        annotations=READ_ONLY,
    )
    @tool_handler
    async def git_show(
        workspace_id: WORKSPACE_ID = ...,
        rev: Annotated[
            str,
            Field(
                description=(
                    "Revision to inspect: HEAD, a branch name, a short or "
                    "full hash. Option-like values are rejected."
                ),
            ),
        ] = "HEAD",
        path: Annotated[
            str | None,
            Field(description="Optional workspace-relative path filter."),
        ] = None,
    ) -> dict:
        """Read-only display of a commit (metadata + diff)."""
        return _direct.git_show(manager, workspace_id, rev=rev, path=path)