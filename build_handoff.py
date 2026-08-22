"""Build handoff.zip and DIFF.patch for repository transfer.

Usage:
    python build_handoff.py

Outputs:
    DIFF.patch      — git format-patch output as UTF-8 (not UTF-16)
    handoff.zip     — repo tree with relative paths, excluding build artifacts
"""

from __future__ import annotations

import os
import subprocess
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
OUT_DIFF = REPO_ROOT / "DIFF.patch"
OUT_ZIP = REPO_ROOT / "handoff.zip"

EXCLUDE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    "*.egg-info",
    ".mypy_cache",
    ".ruff_cache",
    ".opencode",
}
EXCLUDE_FILES = {".env", ".env.local", ".env.production"}


def _should_exclude(path: Path) -> bool:
    rel = path.relative_to(REPO_ROOT)
    parts = rel.parts
    for part in parts[:-1]:
        if part in EXCLUDE_DIRS or part.endswith(".egg-info"):
            return True
    name = parts[-1]
    if name in EXCLUDE_FILES:
        return True
    if name.endswith(".pyc"):
        return True
    return False


def build_diff() -> None:
    """Generate DIFF.patch as UTF-8 via git format-patch."""
    # Check we're in a git repo
    try:
        subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("SKIP DIFF.patch: not a git repository or git unavailable")
        return

    # Find the base commit (first commit or a known tag)
    log = subprocess.run(
        ["git", "log", "--oneline", "--reverse"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    lines = log.stdout.strip().splitlines()
    if not lines:
        print("SKIP DIFF.patch: no commits found")
        return

    base_commit = lines[0].split()[0]
    head_commit = lines[-1].split()[0]

    if base_commit == head_commit:
        print("SKIP DIFF.patch: only one commit, nothing to diff")
        return

    result = subprocess.run(
        ["git", "format-patch", f"{base_commit}..{head_commit}", "--stdout"],
        cwd=str(REPO_ROOT),
        capture_output=True,
    )
    if result.returncode != 0:
        print(f"SKIP DIFF.patch: git format-patch failed: {result.stderr.decode('utf-8', errors='replace')}")
        return

    patch_bytes = result.stdout
    # Ensure UTF-8 encoding (strip any UTF-16 BOM)
    if patch_bytes[:2] == b"\xff\xfe":
        patch_bytes = patch_bytes[2:].decode("utf-16-le").encode("utf-8")
    elif patch_bytes[:3] == b"\xef\xbb\xbf":
        pass  # already UTF-8 BOM, strip it
        patch_bytes = patch_bytes[3:]

    OUT_DIFF.write_bytes(patch_bytes)
    print(f"Wrote {OUT_DIFF.name} ({len(patch_bytes):,} bytes, UTF-8)")


def build_zip() -> None:
    """Build handoff.zip with relative paths and exclusions."""
    if OUT_ZIP.exists():
        OUT_ZIP.unlink()

    count = 0
    with zipfile.ZipFile(OUT_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(REPO_ROOT):
            root_path = Path(root)
            # Prune excluded directories in-place
            dirs[:] = [
                d
                for d in dirs
                if d not in EXCLUDE_DIRS
                and not d.endswith(".egg-info")
            ]
            for name in sorted(files):
                child = root_path / name
                if _should_exclude(child):
                    continue
                rel = child.relative_to(REPO_ROOT)
                zf.write(child, rel.as_posix())
                count += 1

    print(f"Wrote {OUT_ZIP.name} ({count} files, {OUT_ZIP.stat().st_size:,} bytes)")


def main() -> None:
    os.chdir(str(REPO_ROOT))
    build_diff()
    build_zip()


if __name__ == "__main__":
    main()
