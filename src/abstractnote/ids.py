"""Deterministic identifiers and time normalisation (docs/data-model.md).

Every id is derived from its documented inputs with sha256, so replays,
retries and rebuilds produce the same ids. Prefixes: ``nt_`` notes, ``at_``
attachments, ``cl_`` claims, ``rv_`` revisions, ``tk_`` tasks, ``ka_`` the
non-claim AbstractMemory assertions (names, aliases, merges). Entity ids follow
AbstractFramework ADR 0025: ``ex:<kind>-<slug>``.

All stored times are ISO 8601 UTC with second precision and a ``Z`` suffix
(``2026-10-19T09:12:00Z``) so that string comparison is time comparison, in
the note index and in AbstractMemory alike.
"""

from __future__ import annotations

import base64
import hashlib
import re
import unicodedata
from datetime import date, datetime, timezone
from typing import Any, Iterable, Optional

_SEP = "\x1f"

ENTITY_KINDS = ("person", "org", "project", "concept", "place", "event", "artifact", "software", "thing")


def digest(*parts: Any) -> str:
    """sha256 hex of the parts joined by an ASCII unit separator."""
    h = hashlib.sha256(_SEP.join("" if p is None else str(p) for p in parts).encode("utf-8"))
    return h.hexdigest()


def _b32(*parts: Any, length: int = 26) -> str:
    raw = hashlib.sha256(_SEP.join("" if p is None else str(p) for p in parts).encode("utf-8")).digest()
    return base64.b32encode(raw).decode("ascii").rstrip("=").lower()[:length]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- time

def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_time(value: Any, *, field: str = "time") -> str:
    """Normalise an ISO 8601 value (or datetime/date) to ``YYYY-MM-DDTHH:MM:SSZ`` UTC.

    A date alone means midnight UTC; a naive datetime is taken as UTC.
    Raises ValueError with a plain sentence on anything else.
    """
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    elif isinstance(value, str) and value.strip():
        raw = value.strip()
        if raw.endswith(("Z", "z")):
            raw = raw[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            try:
                d = date.fromisoformat(raw)
            except ValueError:
                raise ValueError(f"The {field} {value!r} is not an ISO 8601 date or time.") from None
            dt = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    else:
        raise ValueError(f"The {field} must be an ISO 8601 date or time.")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def optional_time(value: Any, *, field: str = "time") -> Optional[str]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return normalize_time(value, field=field)


def parse_time(value: str) -> datetime:
    return datetime.strptime(normalize_time(value), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def to_minute(value: str) -> str:
    """``occurred_at`` truncated to the minute (the capture fallback key)."""
    return normalize_time(value)[:16]


# --------------------------------------------------------------------------- notes

def note_id_for_import(importer: str, source_key: str) -> str:
    return "nt_" + _b32("import", importer, source_key)


def note_id_for_key(idempotency_key: str) -> str:
    return "nt_" + _b32("key", idempotency_key)


def note_id_fallback(text_norm: str, attachment_hashes: Iterable[str], occurred_at: str, client: str) -> str:
    return "nt_" + _b32("capture", text_norm, ",".join(sorted(attachment_hashes)), to_minute(occurred_at), client)


def note_id_for_insight(kind: str, title: str, body: str, evidence_note_ids: Iterable[str]) -> str:
    return "nt_" + _b32("insight", kind, title, body, ",".join(sorted(evidence_note_ids)))


def attachment_id(note_id: str, sha256: str) -> str:
    return "at_" + _b32(note_id, sha256)


def claim_id(note_id: str, subject: str, predicate: str, obj: str) -> str:
    return "cl_" + _b32(note_id, subject, predicate, obj)


def revision_id_for_note(note_id: str, attempt: int) -> str:
    return "rv_" + _b32("note", note_id, int(attempt))


def revision_id_for_action(action: str, target: str, occurred_at: str) -> str:
    return "rv_" + _b32("action", action, target, occurred_at)


def task_id(note_id: str, text_norm: str) -> str:
    return "tk_" + _b32(note_id, text_norm)


def kg_assertion_id(subject: str, predicate: str, obj: str) -> str:
    """Ids of the non-claim assertions (schema:name, skos:altLabel, schema:sameAs)."""
    return "ka_" + _b32(subject, predicate, obj)


def closure_id(revision_id: str, assertion_id: str, kind: str) -> str:
    return "cz_" + _b32(revision_id, assertion_id, kind)


# --------------------------------------------------------------------------- entities

def slugify(name: str, *, max_len: int = 48) -> str:
    """ASCII slug of a model-chosen name (formatting only; identity is decided by the model)."""
    folded = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")
    if len(slug) > max_len:
        slug = slug[:max_len].rstrip("-")
    return slug or "unnamed"


def entity_id_base(kind: str, name: str) -> str:
    if kind not in ENTITY_KINDS:
        raise ValueError(f"The entity kind {kind!r} is not one of {', '.join(ENTITY_KINDS)}.")
    return f"ex:{kind}-{slugify(name)}"


def mint_entity_id(kind: str, name: str, taken: "set[str] | frozenset[str] | Any") -> str:
    """``ex:<kind>-<slug>``, with ``-2``, ``-3`` ... when the id is already taken.

    ``taken`` is any container answering ``in`` (ids are never reused).
    """
    base = entity_id_base(kind, name)
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def fold(text: str) -> str:
    """Case-folded, whitespace-collapsed form used for exact alias matching."""
    return " ".join(str(text or "").casefold().split())
