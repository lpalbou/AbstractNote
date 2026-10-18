"""Content-addressed attachment storage: ``blobs/<sha256[:2]>/<sha256>``, one file per distinct content."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Tuple

from ..ids import sha256_bytes


class BlobStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def relpath(sha256: str) -> str:
        return f"blobs/{sha256[:2]}/{sha256}"

    def path(self, sha256: str) -> Path:
        return self.root / sha256[:2] / sha256

    def put(self, data: bytes) -> Tuple[str, str, bool]:
        """Store bytes; returns ``(sha256, relative path, already_present)``."""
        sha = sha256_bytes(data)
        target = self.path(sha)
        if target.exists():
            return sha, self.relpath(sha), True
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, target)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        return sha, self.relpath(sha), False

    def read(self, sha256: str) -> bytes:
        p = self.path(sha256)
        if not p.exists():
            raise FileNotFoundError(f"The attachment bytes {sha256} are missing from this notebook.")
        return p.read_bytes()
