"""Files -> one note per file (docs/importers.md): documents, images, audio, video, logs.

The bytes are stored directly in the notebook (no upload limit applies: this runs on the machine
that holds the files) and their text is extracted by the core lane's extractors at capture.
``occurred_at`` = the photo's EXIF date when Pillow is installed and the file has one, else the
file's modification time. ``source.origin`` = ``{path, sha256}``; the source key is the content
hash, so the same file imported again (from anywhere) is a duplicate.
"""

from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
from typing import Iterator, Optional, Sequence

from . import ImportReport, NoteDraft, in_window, iso_from_epoch, iso_utc

IMPORTER = "files"
SKIPPED_NAMES = frozenset({".DS_Store", "Thumbs.db"})


def _exif_date(path: Path) -> Optional[str]:
    try:
        from PIL import Image  # optional
    except ImportError:
        return None
    try:
        with Image.open(path) as img:
            exif = img.getexif()
            raw = exif.get(36867) or exif.get(306)  # DateTimeOriginal, DateTime
    except Exception:  # noqa: BLE001 - not an image, or unreadable EXIF
        return None
    if not raw:
        return None
    try:
        day, time_ = str(raw).strip().split(" ", 1)
        return iso_utc(day.replace(":", "-") + "T" + time_ + "+00:00")
    except ValueError:
        return None


def _walk(paths: Sequence[str | Path]) -> Iterator[Path]:
    for p in paths:
        path = Path(p).expanduser()
        if path.is_dir():
            yield from sorted(x for x in path.rglob("*") if x.is_file())
        elif path.is_file():
            yield path
        else:
            raise ValueError(f"There is no file or folder at {p}.")


def drafts(*, report: ImportReport, paths: Sequence[str | Path], since: Optional[str] = None,
           until: Optional[str] = None) -> Iterator[NoteDraft]:
    for path in _walk(paths):
        if path.name in SKIPPED_NAMES or any(part.startswith(".") for part in path.parts[-1:]):
            report.skip("hidden_file")
            continue
        data = path.read_bytes()
        if not data:
            report.skip("empty")
            continue
        occurred = _exif_date(path) or iso_from_epoch(path.stat().st_mtime)
        if not in_window(occurred, since, until):
            report.skip("outside_window")
            continue
        digest = hashlib.sha256(data).hexdigest()
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        source = {"client": "importer", "importer": IMPORTER, "origin": {"path": str(path.resolve()), "sha256": digest}}
        yield NoteDraft(IMPORTER, digest, "", occurred, "file", path.name, source,
                        [{"data": data, "mime": mime, "filename": path.name}])
