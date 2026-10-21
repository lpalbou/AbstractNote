"""Files -> one note per file (docs/importers.md): documents, images, audio, video, logs.

The bytes are stored directly in the notebook (no upload limit applies: this runs on the machine
that holds the files) and their text is extracted by the core lane's extractors at capture.
``occurred_at`` = the date the file carries in its own metadata (:func:`abstractnote.ingest.dates.detect_file_date`,
shared with capture: a photo's EXIF date when Pillow is installed, an XMP creation date, a PDF's ``/CreationDate``,
an Office document's ``core.xml`` created date), else the file's modification time. ``source.origin`` =
``{path, sha256, date_basis}`` (``no_split`` with ``--no-split``); the source key is the content hash, so the same
file imported again (from anywhere) is a duplicate. A document of dated entries (a diary) is split into a container
and one note per entry when it is understood (W30, ``process/split.py``), unless ``--no-split``.
"""

from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
from typing import Iterator, Optional, Sequence

from ..ingest.dates import detect_file_date
from . import ImportReport, NoteDraft, in_window

IMPORTER = "files"
SKIPPED_NAMES = frozenset({".DS_Store", "Thumbs.db"})


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
           until: Optional[str] = None, no_split: bool = False) -> Iterator[NoteDraft]:
    """One draft per file. ``no_split`` (``--no-split``, W30): a document of dated entries stays one note
    (``source.origin.no_split``) instead of a container and one note per entry."""
    for path in _walk(paths):
        if path.name in SKIPPED_NAMES or any(part.startswith(".") for part in path.parts[-1:]):
            report.skip("hidden_file")
            continue
        data = path.read_bytes()
        if not data:
            report.skip("empty")
            continue
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        occurred, basis = detect_file_date(path, mime)  # a path always has a date (its modification time last)
        if not in_window(occurred, since, until):
            report.skip("outside_window")
            continue
        digest = hashlib.sha256(data).hexdigest()
        source = {"client": "importer", "importer": IMPORTER, "origin": {"path": str(path.resolve()), "sha256": digest,
                                                                         "date_basis": basis}}
        if no_split:
            source["origin"]["no_split"] = True
        # the title is the file's name, not its text: a file nothing can be read from is left out at capture
        yield NoteDraft(IMPORTER, digest, "", occurred, "file", path.name, source,
                        [{"data": data, "mime": mime, "filename": path.name}], title_is_text=False)
