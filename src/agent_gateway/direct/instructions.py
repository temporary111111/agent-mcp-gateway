"""Dynamic instruction generator for the agent gateway.

Generates context-aware instructions based on:
- Detected project type (Python, Node.js, Go, etc.)
- AGENTS.md conventions
- Available tools
- User preferences
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


# Project type detection patterns
PROJECT_MARKERS = {
    "python": {
        "files": ["pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "Pipfile"],
        "test_cmd": "pytest",
        "build_cmd": "pip install -e .",
        "lint_cmd": "ruff check .",
        "patterns": [
            "Use snake_case for functions and variables",
            "Use UPPER_SNAKE_CASE for constants",
            "Type hints are encouraged",
            "Use pathlib.Path for file paths",
        ],
    },
    "node": {
        "files": ["package.json", "tsconfig.json", "yarn.lock", "pnpm-lock.yaml"],
        "test_cmd": "npm test",
        "build_cmd": "npm run build",
        "lint_cmd": "npm run lint",
        "patterns": [
            "Use camelCase for variables and functions",
            "Use PascalCase for classes and components",
            "Prefer const over let",
            "Use async/await over callbacks",
        ],
    },
    "go": {
        "files": ["go.mod", "go.sum"],
        "test_cmd": "go test ./...",
        "build_cmd": "go build ./...",
        "lint_cmd": "golangci-lint run",
        "patterns": [
            "Use camelCase for unexported, PascalCase for exported",
            "Error handling: check every error",
            "Keep functions short and focused",
            "Use go fmt for formatting",
        ],
    },
    "rust": {
        "files": ["Cargo.toml", "Cargo.lock"],
        "test_cmd": "cargo test",
        "build_cmd": "cargo build",
        "lint_cmd": "cargo clippy",
        "patterns": [
            "Use snake_case for functions and variables",
            "Use PascalCase for types and enums",
            "Handle Results, don't unwrap",
            "Use cargo fmt for formatting",
        ],
    },
    "java": {
        "files": ["pom.xml", "build.gradle", "build.gradle.kts"],
        "test_cmd": "mvn test",
        "build_cmd": "mvn compile",
        "lint_cmd": "mvn checkstyle:check",
        "patterns": [
            "Use camelCase for methods and variables",
            "Use PascalCase for classes",
            "One public class per file",
            "Use interfaces over abstract classes",
        ],
    },
}


def detect_project_type(workspace_path: Path) -> str | None:
    """Detect the primary project type from workspace files."""
    for project_type, config in PROJECT_MARKERS.items():
        for marker in config["files"]:
            if (workspace_path / marker).exists():
                return project_type
    return None


def get_project_config(project_type: str | None) -> dict[str, Any] | None:
    """Get configuration for a detected project type."""
    if project_type and project_type in PROJECT_MARKERS:
        return PROJECT_MARKERS[project_type]
    return None


def generate_dynamic_instructions(
    workspace_path: Path | None = None,
    agents_md_content: str = "",
    tools_available: list[str] | None = None,
) -> str:
    """Generate context-aware instructions based on project detection."""

    # Base instructions (always included)
    base = """\
You are connected to a local Agent Gateway. You are the reasoning agent;
the gateway executes deterministic tools and does not invoke another LLM.

═══════════════════════════════════════════════════════════════════════
 AGENTIC LOOP — Follow this for every task
═══════════════════════════════════════════════════════════════════════

1. PLAN (before acting)
   - Read the task carefully. Break it into concrete steps.
   - Use todo_write to track your plan.
   - If the project has AGENTS.md, read it first.

2. EXPLORE (understand the codebase)
   - workspace_open → workspace_tree → file_find → code_search
   - Read relevant files. Understand patterns, imports, conventions.
   - Do NOT write code until you understand the context.

3. EXECUTE (make changes)
   - Write with file_write or file_replace/file_apply_patch.
   - Pass expected_sha256 from your last read.
   - Work in small, atomic steps.

4. VERIFY (prove it works — MANDATORY)
   - Run tests/builds with process_run and WAIT for output.
   - You MUST see "passed" or "0 failed" in the output.
   - NEVER claim "tests pass" without running them and seeing output.

5. ITERATE (loop until done)
   - Repeat steps 2-4 until complete.

6. REPORT (finish cleanly)
   - Show ACTUAL test output (e.g. "7 passed, 0 failed").
   - Summarize what was done and what files changed.
"""

    # Detect project type and add specific instructions
    project_section = ""
    if workspace_path:
        project_type = detect_project_type(workspace_path)
        config = get_project_config(project_type)

        if config:
            patterns = "\n".join(f"   - {p}" for p in config["patterns"])
            project_section = f"""
═══════════════════════════════════════════════════════════════════════
 PROJECT TYPE: {project_type.upper()}
═══════════════════════════════════════════════════════════════════════

Detected project type: {project_type}
Test command: {config['test_cmd']}
Build command: {config['build_cmd']}
Lint command: {config['lint_cmd']}

Conventions for this project:
{patterns}

When making changes:
- Use the detected test command to verify changes
- Follow the project's naming conventions
- Check for existing patterns before adding new code
- Run lint before reporting completion
"""
        else:
            project_section = f"""
═══════════════════════════════════════════════════════════════════════
 PROJECT TYPE: UNKNOWN
═══════════════════════════════════════════════════════════════════════

No standard project type detected. Before making changes:
1. Read existing files to understand the tech stack
2. Check for test commands in package.json, Makefile, or similar
3. Follow existing code conventions
4. If unsure, ask the user for the test/build command
"""

    # Add AGENTS.md conventions if available
    agents_section = ""
    if agents_md_content:
        agents_section = f"""
═══════════════════════════════════════════════════════════════════════
 PROJECT CONVENTIONS (from AGENTS.md)
═══════════════════════════════════════════════════════════════════════

{agents_md_content}

Follow these conventions strictly when making changes.
"""

    # Add strict rules
    rules = """
═══════════════════════════════════════════════════════════════════════
 STRICT RULES — Violations = FAILED build
═══════════════════════════════════════════════════════════════════════

1. NEVER claim "tests pass" without running process_run and seeing output.
2. NEVER say "done" without showing the actual pytest/test output.
3. NEVER skip verification to "save time" — verification IS the work.
4. ALWAYS show the exact output: "X passed, Y failed" in your report.
5. If you cannot run tests, say so honestly — do NOT claim success.

═══════════════════════════════════════════════════════════════════════
 SECURITY
═══════════════════════════════════════════════════════════════════════

- All paths are workspace-relative. The gateway rejects absolute paths,
  '..' traversal, symlink escapes, and anything outside allowed roots.
- process_run runs with OS privileges. Only use when allowed.
- Never claim a file changed without read-back or git_diff proof.
- No git operation commits, pushes, or overwrites.
"""

    return base + project_section + agents_section + rules
