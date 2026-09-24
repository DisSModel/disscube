"""Small file helpers."""

from __future__ import annotations

import hashlib
from pathlib import Path


def sha256_file(path: str | Path, chunk_size: int = 1 << 20) -> str:
    """SHA-256 of a file's bytes, as ``"sha256:<hex>"``.

    Use it as ``SpatialSource.checksum``: when the file changes, derivations
    from that source get a new ``spec_hash`` instead of a stale cache hit.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk_size), b""):
            digest.update(block)
    return f"sha256:{digest.hexdigest()}"
