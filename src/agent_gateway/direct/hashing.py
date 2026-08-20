"""Deterministic content hashing and binary detection."""

from __future__ import annotations

import hashlib
from pathlib import Path

_BINARY_PROBE_BYTES = 8192


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_probably_binary(data: bytes) -> bool:
    """Cheap NUL-byte probe over the first chunk of a file."""
    return b"\x00" in data[:_BINARY_PROBE_BYTES]


def read_bytes_capped(path: Path, max_bytes: int) -> tuple[bytes, bool]:
    """Read up to *max_bytes*; return (data, truncated)."""
    with path.open("rb") as handle:
        data = handle.read(max_bytes)
        truncated = len(data) == max_bytes and handle.read(1) != b""
    return data, truncated


__all__ = [
    "is_probably_binary",
    "read_bytes_capped",
    "sha256_bytes",
    "sha256_file",
]