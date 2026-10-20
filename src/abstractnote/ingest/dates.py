"""When a note happened, and how sure that date is (docs/data-model.md "Time", docs/importers.md "Dates").

Two things live here:

- :func:`detect_file_date`: the date a file carries in its own metadata, shared by ``abstractnote import files``
  and capture: a photo's EXIF ``DateTimeOriginal`` (Pillow, when installed), an XMP creation date (images and PDFs),
  a PDF's Info ``/CreationDate``, an Office Open XML ``docProps/core.xml`` ``dcterms:created``, else (for a path)
  the file's modification time. These are fixed metadata fields read by position, never the meaning of the text.
- :func:`date_basis` / :func:`is_approximate`: how a stored note's ``occurred_at`` was found, and whether it is an
  approximation that the note's own text may correct (``note.redate``).
"""

from __future__ import annotations

import datetime as _dt
import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any, Optional, Tuple, Union

#: ``date_basis`` values whose ``occurred_at`` is an approximation of when the thing happened (importers.md):
#: a session start, a last edit, a commit, a bulk import commit, a file date, a store update, a session's last
#: record.
APPROXIMATE_BASES = frozenset({"origin_session", "front_matter_modified", "git_added", "bulk_commit", "file_mtime",
                               "store_updated", "last_record"})
#: An imported note written before date bases were recorded: how its date was found is not known.
UNRECORDED = "unrecorded"
#: A live capture: the client's clock (or the moment it was received). Never moved by the note's content.
CLIENT = "client"
#: The note's own text stated its date (``note.redate`` at processing).
CONTENT = "content"
#: A person set the date (``POST /note/notes/{id}/redate``).
USER = "user"

FileDate = Tuple[str, str]  # (occurred_at ISO UTC, date_basis)

_XMP_DATE = re.compile(rb"(?:xmp:CreateDate|photoshop:DateCreated|exif:DateTimeOriginal)"
                       rb"\s*(?:=\s*[\"']([^\"']+)[\"']|>\s*([^<\s]+)\s*<)")
_PDF_CREATION = re.compile(rb"/CreationDate\s*\(\s*(D:[^)]*)\)")
_PDF_PARTS = re.compile(r"D:(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?([Zz+\-])?(\d{2})?'?(\d{2})?'?")
_OOXML_CREATED = re.compile(rb"<dcterms:created[^>]*>\s*([^<\s]+)\s*</dcterms:created>")


def _iso(dt: _dt.datetime) -> Optional[str]:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    dt = dt.astimezone(_dt.timezone.utc)
    if dt.year < 1900 or dt.year > 2999:  # a zeroed or garbled field is no date
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_text(text: str) -> Optional[str]:
    """An ISO 8601 date or date-time (any offset, ``Z``; a naive time is UTC) as UTC, else None."""
    t = str(text or "").strip()
    if not t:
        return None
    try:
        return _iso(_dt.datetime.fromisoformat(t.replace("Z", "+00:00").replace("z", "+00:00")))
    except ValueError:
        return None


def _exif_text(raw: Any) -> Optional[str]:
    """EXIF ``YYYY:MM:DD HH:MM:SS`` (no zone: taken as UTC, as the files importer always did)."""
    try:
        day, time_ = str(raw).strip().split(" ", 1)
        return _iso_text(day.replace(":", "-") + "T" + time_.strip())
    except ValueError:
        return None


def _exif_date(data: bytes) -> Optional[str]:
    try:
        from PIL import Image  # optional
    except ImportError:
        return None
    try:
        with Image.open(io.BytesIO(data)) as img:
            exif = img.getexif()
            raw = None
            try:
                raw = exif.get_ifd(0x8769).get(36867)  # Exif IFD: DateTimeOriginal
            except Exception:  # noqa: BLE001 - no Exif IFD
                raw = None
            raw = raw or exif.get(36867) or exif.get(306)  # DateTimeOriginal, DateTime
    except Exception:  # noqa: BLE001 - not an image, or unreadable EXIF
        return None
    return _exif_text(raw) if raw else None


def _xmp_date(data: bytes) -> Optional[str]:
    m = _XMP_DATE.search(data)
    if not m:
        return None
    return _iso_text((m.group(1) or m.group(2) or b"").decode("ascii", "ignore"))


def _pdf_date(data: bytes) -> Optional[str]:
    m = _PDF_CREATION.search(data)
    if not m:
        return None
    p = _PDF_PARTS.match(m.group(1).decode("latin-1", "ignore"))
    if not p:
        return None
    year, month, day, hour, minute, second, sign, oh, om = p.groups()
    try:
        dt = _dt.datetime(int(year), int(month or 1), int(day or 1), int(hour or 0), int(minute or 0),
                          int(second or 0))
    except ValueError:
        return None
    if sign in ("+", "-"):
        delta = _dt.timedelta(hours=int(oh or 0), minutes=int(om or 0))
        dt = dt.replace(tzinfo=_dt.timezone(delta if sign == "+" else -delta))
    return _iso(dt)


def _ooxml_date(data: bytes) -> Optional[str]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            core = z.read("docProps/core.xml")
    except (zipfile.BadZipFile, KeyError, OSError, RuntimeError):
        return None
    m = _OOXML_CREATED.search(core)
    return _iso_text(m.group(1).decode("ascii", "ignore")) if m else None


_OOXML_MIMES = ("application/vnd.openxmlformats-officedocument.",)
_OOXML_EXT = (".docx", ".xlsx", ".pptx", ".docm", ".xlsm", ".pptm")


def detect_file_date(source: Union[str, Path, bytes], mime: Optional[str] = None,
                     filename: Optional[str] = None) -> Optional[FileDate]:
    """``(occurred_at, date_basis)`` from a file's own metadata, or None.

    ``source`` is a path (its bytes are read; the modification time is the last resort, basis ``file_mtime``)
    or the bytes themselves (no modification time to fall back on). Order: images ``exif`` then ``xmp``;
    PDFs ``pdf_created`` then ``xmp``; Office Open XML documents ``office_created``; then ``file_mtime``."""
    path: Optional[Path] = None
    if isinstance(source, (bytes, bytearray)):
        data = bytes(source)
    else:
        path = Path(source).expanduser()
        data = path.read_bytes()
        filename = filename or path.name
    from .extract import guess_mime

    m = guess_mime(filename, mime)
    ext = Path(filename or "").suffix.lower()
    found: Optional[FileDate] = None
    if m.startswith("image/"):
        d = _exif_date(data)
        found = (d, "exif") if d else None
        if found is None:
            d = _xmp_date(data)
            found = (d, "xmp") if d else None
    elif m == "application/pdf" or ext == ".pdf":
        d = _pdf_date(data)
        found = (d, "pdf_created") if d else None
        if found is None:
            d = _xmp_date(data)
            found = (d, "xmp") if d else None
    elif m.startswith(_OOXML_MIMES) or ext in _OOXML_EXT:
        d = _ooxml_date(data)
        found = (d, "office_created") if d else None
    if found is None and path is not None:
        mtime = _dt.datetime.fromtimestamp(path.stat().st_mtime, tz=_dt.timezone.utc)
        found = (mtime.strftime("%Y-%m-%dT%H:%M:%SZ"), "file_mtime")
    return found


# ----------------------------------------------------------------------------- a stored note's basis

def _source(note: Any) -> dict:
    raw = note["source_json"] if not isinstance(note, dict) else note.get("source_json", note.get("source"))
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}") or {}
    except (TypeError, ValueError):
        return {}


def date_basis(note: Any) -> str:
    """How the note's ``occurred_at`` was found: the latest redate's basis (``content`` or ``user``), else the
    importer's ``source.origin.date_basis``, else ``unrecorded`` for an import and ``client`` for a live note."""
    src = _source(note)
    redated = src.get("redated") if isinstance(src.get("redated"), dict) else None
    if redated and redated.get("basis"):
        return str(redated["basis"])
    origin = src.get("origin") if isinstance(src.get("origin"), dict) else {}
    if origin.get("date_basis"):
        return str(origin["date_basis"])
    return UNRECORDED if (src.get("client") == "importer" or src.get("importer")) else CLIENT


def is_approximate(basis: str) -> bool:
    """True when the date is a stand-in (a file date, a commit, a session start ...) the note's text may correct."""
    return basis in APPROXIMATE_BASES or basis == UNRECORDED


__all__ = ["APPROXIMATE_BASES", "CLIENT", "CONTENT", "UNRECORDED", "USER", "date_basis", "detect_file_date",
           "is_approximate"]
