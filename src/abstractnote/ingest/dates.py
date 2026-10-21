"""When a note happened, and how sure that date is (docs/data-model.md "Time", docs/importers.md "Dates").

Two things live here:

- :func:`detect_file_date`: the date a file carries in its own metadata, shared by ``abstractnote import files``
  and capture: a photo's EXIF ``DateTimeOriginal`` (Pillow, when installed), an XMP creation date (images and PDFs),
  a PDF's Info ``/CreationDate``, an Office Open XML ``docProps/core.xml`` ``dcterms:created``, else (for a path)
  the file's modification time. These are fixed metadata fields read by position, never the meaning of the text.
- :func:`date_basis` / :func:`is_approximate`: how a stored note's ``occurred_at`` was found, and whether it is an
  approximation that the note's own text may correct (``note.redate``).
- :func:`capture_date`: the date and basis of a live capture (ADR 0008 §7): the caller's time for a note typed now
  (``client``, never moved by its text), else the attachment's own metadata date, else the file date the client
  sent (``last_modified``), else the moment of the upload, the paste or the capture (stand-ins the text may correct).
- :func:`document_date`: the stated document date of an extraction as a time and a basis (``content``, or
  ``stated_month`` / ``stated_year`` when the text gives only a month or a year).
- :data:`PRECEDENCE` / :func:`stated_date_wins`: which evidence of a note's date beats which (W30, one table): a
  person's date, then the moment the words were written, then a date the document states about itself, then the
  file's metadata, then the receiving side's stand-ins (the client's upload, paste or capture time).
- :func:`redate_note`: a person's re-date. A note not understood yet just moves; an understood note moves, keeps its
  statements where its interpretation placed them (the note index and AbstractMemory stay in agreement, no belief
  overlaps another) and is marked, with the notes that depend on it, for re-understanding (ADR 0008 §4).
"""

from __future__ import annotations

import datetime as _dt
import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any, Optional, Tuple, Union

#: Stand-in dates of a live capture (ADR 0008 §7): the moment a file was uploaded, a text pasted, or a note captured
#: without any date (an API call that sent none). The note's own text may correct them (``note.redate``, basis
#: ``content``); the extraction header reads "this date is approximate, from the upload" (the basis, underscores as
#: spaces), which is the only case in which the model gives a ``document_date``.
UPLOAD, PASTED, CAPTURE_TIME = "upload", "pasted_text", "capture_time"
#: A stated document date that names only a month or a year (``2019-03``, ``2019``): placed on its first day and
#: still approximate.
STATED_MONTH, STATED_YEAR = "stated_month", "stated_year"
#: ``date_basis`` values whose ``occurred_at`` is an approximation of when the thing happened (importers.md):
#: a session start, a last edit, a commit, a bulk import commit, a file date, a store update, a session's last
#: record; a live capture's upload, paste or capture moment; a stated month or year.
APPROXIMATE_BASES = frozenset({"origin_session", "front_matter_modified", "git_added", "bulk_commit", "file_mtime",
                               "store_updated", "last_record", UPLOAD, PASTED, CAPTURE_TIME, STATED_MONTH,
                               STATED_YEAR})
#: An imported note written before date bases were recorded: how its date was found is not known.
UNRECORDED = "unrecorded"
#: A note a person typed now: the client's clock. Never moved by the note's content (a typed "the notes of 14 March
#: 2019 were useful" stays dated today).
CLIENT = "client"
#: The note's own text stated its date (``note.redate`` at processing).
CONTENT = "content"
#: A person set the date (``POST /note/notes/{id}/redate``).
USER = "user"
#: A dated part of a document (W30 "moments"): the date its heading or first line states; the date of a part note
#: (``part_of`` a container) and of the container itself (its earliest part).
STATED = "stated"

#: The precedence of date evidence (W30; docs/data-model.md "Document dates"), strongest first. A date the document
#: states about itself (the model's ``document_date``, a dated part, or a dated header line an importer read) beats
#: the file's own metadata, which beats the receiving side's stand-ins. Two kinds of evidence are never moved by what
#: a text states: a person's date (only another re-date changes it) and the moment the words were written (a note
#: typed now, a transcript record, a hub message: the dates such a text gives are about other things).
PRECEDENCE: Tuple[Tuple[int, str, Tuple[str, ...]], ...] = (
    (5, "a person set it", (USER,)),
    (4, "the moment it was written", (CLIENT, "record", "first_record", "message")),
    (3, "the document states it", (CONTENT, STATED, "front_matter", "created_line", "date_line", STATED_MONTH,
                                   STATED_YEAR)),
    (2, "the file's own metadata", ("exif", "xmp", "pdf_created", "office_created", "file_mtime")),
    (1, "a stand-in of the receiving side", (UPLOAD, PASTED, CAPTURE_TIME, "git_added", "bulk_commit",
                                             "origin_session", "front_matter_modified", "store_updated",
                                             "last_record", UNRECORDED)),
)
RANK: dict = {basis: rank for rank, _, bases in PRECEDENCE for basis in bases}
#: The rank of a date the document states about itself.
STATED_RANK = 3
#: The rank of the moment the words were written: never moved by a stated date.
WRITTEN_RANK = 4
FILE_METADATA_BASES = frozenset(b for rank, _, bases in PRECEDENCE if rank == 2 for b in bases)
#: The receiving side's moments (the client's time standing in for an upload, a paste, a capture).
RECEIVED_BASES = frozenset({UPLOAD, PASTED, CAPTURE_TIME})


def rank(basis: Optional[str]) -> int:
    """The precedence rank of a date basis (:data:`PRECEDENCE`); an unknown basis ranks as a stand-in (1)."""
    return int(RANK.get(str(basis or UNRECORDED), 1))


def stated_date_wins(current: Optional[str]) -> bool:
    """True when a date the document states about itself (rank 3, exact) may replace the note's current date:
    the current one is the file's metadata or a stand-in (rank below 3), or a stated month or year (a full stated
    date refines it). Never a person's date nor the moment the words were written."""
    r = rank(current)
    return r < STATED_RANK or (r == STATED_RANK and is_approximate(str(current)))

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


# ----------------------------------------------------------------------------- a live capture's date

def _attachment_mtime(value: Any) -> Optional[str]:
    """The ``last_modified`` a client sent with an attachment (ISO 8601, or epoch milliseconds as the web's
    ``File.lastModified`` gives), as UTC; anything else is refused with a sentence."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("A file's last_modified must be a date or epoch milliseconds.")
    if isinstance(value, (int, float)):
        try:
            return _iso(_dt.datetime.fromtimestamp(float(value) / 1000.0, tz=_dt.timezone.utc))
        except (OverflowError, OSError, ValueError):
            raise ValueError("A file's last_modified is not a date.") from None
    found = _iso_text(str(value))
    if found is None:
        raise ValueError(f"A file's last_modified {value!r} is not an ISO 8601 date.")
    return found


#: Clients whose attachments are made at the moment of the capture (the watch's recordings), never uploaded
#: documents: their time stays the client's (``client``).
RECORDING_CLIENTS = frozenset({"watch"})


def capture_date(occurred_at: Optional[str], attachments: Any, *, pasted: bool, now: str,
                 client: Optional[str] = None) -> Tuple[str, str]:
    """``(occurred_at, date_basis)`` of a live capture (ADR 0008 §7, W30 :data:`PRECEDENCE`, docs/importers.md "Dates").

    ``occurred_at`` is the caller's (already normalised) time or None. A note with no attachment and no pasted text
    keeps the caller's time as ``client``. An upload is dated by its file first, whether the caller sent a time or
    not (the file's metadata beats the client's time): the first attachment's own metadata date (``exif``, ``xmp``,
    ``pdf_created``, ``office_created``), else the first ``last_modified`` a client sent with a file
    (``file_mtime``), else the caller's time or the moment of the capture as ``upload``. A paste is the moment of
    the paste (``pasted_text``); a capture with nothing else the moment of the capture (``capture_time``). A watch's
    attachment is a recording made now (:data:`RECORDING_CLIENTS`): with the watch's time it stays ``client``. The
    document's own stated date may still replace any of these stand-ins when it is understood (``note.redate``)."""
    atts = list(attachments or [])
    if occurred_at and (not atts or client in RECORDING_CLIENTS):
        return occurred_at, (PASTED if pasted and not atts else CLIENT)
    for a in atts:
        found = detect_file_date(a["data"], a.get("mime"), a.get("filename"))
        if found:
            return found
    for a in atts:
        mtime = _attachment_mtime(a.get("last_modified"))
        if mtime:
            return mtime, "file_mtime"
    if atts:
        return occurred_at or now, UPLOAD
    return now, (PASTED if pasted else CAPTURE_TIME)


_FROM_FILE = {"exif": "the photo", "xmp": "the file", "pdf_created": "the document", "office_created": "the document",
              "file_mtime": "the file's last change"}


def long_day(ts: str) -> str:
    """``2019-03-14T...`` as "14 March 2019" (a date in a capture answer)."""
    d = _dt.datetime.strptime(str(ts)[:10], "%Y-%m-%d")
    return f"{d.day} {d.strftime('%B')} {d.year}"


def _lost(at: Optional[str], basis: Optional[str], won: str) -> str:
    """The part of a date sentence that says what lost (W30): "; the file itself is from 20 March 2026" for the
    file's metadata, "; it was sent on 21 October 2026" for an upload's client time ("pasted on" for a paste, "saved
    on" for a capture without a date), "; its earlier date was ..." otherwise (an importer's stand-in). Empty when nothing lost or the losing date is the same day as the winner."""
    if not at or str(at)[:10] == str(won)[:10]:
        return ""
    try:
        day = long_day(at)
    except ValueError:
        return ""
    if basis in FILE_METADATA_BASES:
        return f"; the file itself is from {day}"
    if basis == PASTED:
        return f"; it was pasted on {day}"
    if basis == CAPTURE_TIME:
        return f"; it was saved on {day}"
    if basis in RECEIVED_BASES or basis == CLIENT:
        return f"; it was sent on {day}"
    return f"; its earlier date was {day}"


def date_sentence(occurred_at: str, basis: str, *, lost_at: Optional[str] = None,
                  lost_basis: Optional[str] = None) -> Optional[str]:
    """What the capture answer says about the note's date when it did not come from the person's clock:
    "Dated 2 July 2019 from the document." (a file's own metadata date or its last change), with what lost when the
    caller sent its own time ("Dated 2 July 2019 from the document; it was sent on 21 October 2026."). The
    stand-ins (``upload``, ``pasted_text``, ``capture_time``) say nothing at capture: the text may still state the
    date, and the answer of the understanding says it then (:func:`redate_sentence`)."""
    where = _FROM_FILE.get(basis)
    if not where:
        return None
    return f"Dated {long_day(occurred_at)} from {where}{_lost(lost_at, lost_basis, occurred_at)}."


# ----------------------------------------------------------------------------- a stated document date

_ISO_MONTH = re.compile(r"^(\d{4})-(\d{2})$")
_ISO_YEAR = re.compile(r"^(\d{4})$")


def document_date(iso: str) -> Tuple[str, str]:
    """``(occurred_at, basis)`` of an extraction's ``document_date.iso``: a full date or time is exact
    (``content``); ``YYYY-MM`` is its month's first day (``stated_month``) and ``YYYY`` its year's (``stated_year``),
    both approximate. These are ISO forms read by position, never words. Raises ValueError otherwise."""
    from .. import ids

    raw = str(iso or "").strip()
    m = _ISO_MONTH.match(raw)
    if m and 1 <= int(m.group(2)) <= 12:
        return f"{m.group(1)}-{m.group(2)}-01T00:00:00Z", STATED_MONTH
    m = _ISO_YEAR.match(raw)
    if m:
        return f"{m.group(1)}-01-01T00:00:00Z", STATED_YEAR
    return ids.normalize_time(raw, field="document date"), CONTENT


def redate_sentence(ops: Any) -> Optional[str]:
    """The sentence an understanding adds to its answer when the note's text dated it (a ``note.redate`` op with
    basis ``content``, ``stated``, ``stated_month`` or ``stated_year``): which won and what lost (W30, the op's
    ``from`` and ``from_basis``): "Dated 5 March 2025 from the document; the file itself is from 20 March 2026."."""
    for op in ops or []:
        if not isinstance(op, dict) or op.get("op") != "note.redate":
            continue
        p = op.get("payload") or {}
        basis, to = p.get("basis"), str(p.get("to") or "")
        if not to:
            continue
        lost = _lost(p.get("from"), p.get("from_basis"), to) if p.get("from_basis") else ""
        if basis in (CONTENT, STATED):
            return f"Dated {long_day(to)} from the document{lost}."
        if basis == STATED_MONTH:
            d = _dt.datetime.strptime(to[:10], "%Y-%m-%d")
            return f"Dated {d.strftime('%B')} {d.year} from the document{lost}."
        if basis == STATED_YEAR:
            return f"Dated {to[:4]} from the document{lost}."
    return None


# ----------------------------------------------------------------------------- a person's re-date

class RedateRefused(ValueError):
    """A re-date that cannot be done safely in this build (the reason is one sentence for the person)."""


#: Why a re-date of an understood note waits for re-understanding (the seam to the re-understanding lane).
NEEDS_REUNDERSTANDING = ("This note was already understood, and its date can only change once the notebook can "
                         "understand it again.")


def understood(nb: Any, note_id: str) -> bool:
    """True when the note has an applied processing revision (its statements are in both stores)."""
    return nb.store.q1("SELECT 1 FROM revisions WHERE note_id=? AND action IS NULL AND applied_index=1"
                       " AND abandoned=0 LIMIT 1", (note_id,)) is not None


def redate_note(nb: Any, note_id: str, occurred_at: Any, account: Optional[str], reason: str = "", *,
                client: str = "web") -> dict:
    """A person sets when a note happened (``POST /note/notes/{id}/redate``, basis ``user``; ADR 0008 §4, §7).

    - A note not understood yet: one ``note.redate`` user revision (``Notebook._redate_op``); it is understood
      later at its new date.
    - An understood note: its statements stay where its interpretation placed them, in the note index AND in
      AbstractMemory (``note.redate`` with ``keep_statements``: no belief is moved in one store only, none overlaps
      another), and the note is marked for re-understanding together with the notes that depend on it, forward from
      the earlier of the old and the new date: the ``note.redate`` revision's own projection writes those marks in
      the same transaction (``store.notes`` ``MARKING_ACTIONS``, ADR 0008 §4); the re-understanding re-places the
      statements at the new date in both stores. When the notebook cannot understand a note again (the installed
      AbstractMemory has no closure kind ``reinterpreted``) the re-date is refused (:data:`NEEDS_REUNDERSTANDING`)
      and nothing is written: re-placing windows in the note index alone would leave the two stores disagreeing
      (AbstractMemory cannot reopen a closed window).
    """
    from .. import ids

    row = nb.store.note_row(note_id)
    if row is None:
        raise ValueError("That note is not in this notebook.")
    if not understood(nb, note_id):
        return nb._redate_op(note_id, occurred_at, account, reason, client=client)  # noqa: SLF001
    if occurred_at in (None, ""):
        raise ValueError("Give the date the note was written.")
    new = ids.normalize_time(occurred_at, field="occurred_at")
    old = row["occurred_at"]
    if new == old:
        raise ValueError("The note already has that date.")
    from ..store.kg import supports_reinterpreted

    if not supports_reinterpreted():
        raise RedateRefused(NEEDS_REUNDERSTANDING)
    out = nb._redate_op(note_id, new, account, reason, client=client, keep_statements=True)  # noqa: SLF001
    marked = out.get("reinterpret_marked") or {}
    if note_id not in (marked.get("notes") or []) and note_id not in {m["note_id"] for m in nb.store.pending_marks()}:
        raise RuntimeError("The re-dated note was not marked to be understood again; its statements would stay at"
                           " the old date (ADR 0008 §4).")
    out["reinterpret"] = marked
    return out


__all__ = ["APPROXIMATE_BASES", "CAPTURE_TIME", "CLIENT", "CONTENT", "FILE_METADATA_BASES", "NEEDS_REUNDERSTANDING",
           "PASTED", "PRECEDENCE", "RANK", "RECEIVED_BASES", "RedateRefused", "STATED", "STATED_MONTH", "STATED_RANK",
           "STATED_YEAR", "UNRECORDED", "UPLOAD", "USER", "WRITTEN_RANK", "capture_date", "date_basis",
           "date_sentence", "detect_file_date", "document_date", "is_approximate", "long_day", "rank", "redate_note",
           "redate_sentence", "stated_date_wins", "understood"]
