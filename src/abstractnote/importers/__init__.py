"""Importers: existing corpora into a notebook with faithful history time and provenance (docs/importers.md).

Each importer yields ``NoteDraft`` records; ``write_drafts`` captures them through the core lane's
``Notebook.capture`` at bulk priority (``0``) with ``importer`` + ``source_key`` (note id =
``sha256(importer, source_key)``), so a re-run creates nothing new and live notes always go first.
Processing happens later through the queue (the ``note-drain`` flow), never here.

Noise filtering is structural: record types, flags and the harness's own envelope markers decide
what is machine output (tool results, hook output, system reminders, alerts), never the meaning of
the words (ADR 0004).
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional

BULK_PRIORITY = 0
# ``source.origin.date_basis`` values whose ``occurred_at`` is an approximation of when the thing
# happened (a session start, a last edit, a commit, a file date) rather than its own timestamp.
APPROXIMATE_BASES = frozenset({"origin_session", "front_matter_modified", "git_added", "file_mtime",
                               "store_updated", "last_record"})


@dataclass
class NoteDraft:
    """One note an importer wants to write."""

    importer: str
    source_key: str
    text: str
    occurred_at: str
    kind: str  # what it is in the source (prompt, summary, subagent, memory, decision, thread, ...), for counts
    title: Optional[str] = None
    source: Dict[str, Any] = field(default_factory=dict)
    attachments: List[Dict[str, Any]] = field(default_factory=list)  # {"data": bytes, "mime"?, "filename"?}


@dataclass
class ImportReport:
    importer: str
    dry_run: bool = False
    created: int = 0
    duplicates: int = 0
    skipped: Dict[str, int] = field(default_factory=dict)  # noise and filtered records, by reason
    attachments: int = 0
    by_kind: Dict[str, int] = field(default_factory=dict)
    date_basis: Dict[str, int] = field(default_factory=dict)  # how each occurred_at was found, by basis
    earliest: Optional[str] = None
    latest: Optional[str] = None
    errors: List[Dict[str, str]] = field(default_factory=list)

    def skip(self, reason: str, n: int = 1) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + n

    def seen(self, draft: NoteDraft) -> None:
        self.by_kind[draft.kind] = self.by_kind.get(draft.kind, 0) + 1
        basis = str((draft.source.get("origin") or {}).get("date_basis") or "unrecorded")
        self.date_basis[basis] = self.date_basis.get(basis, 0) + 1
        if self.earliest is None or draft.occurred_at < self.earliest:
            self.earliest = draft.occurred_at
        if self.latest is None or draft.occurred_at > self.latest:
            self.latest = draft.occurred_at

    def as_dict(self) -> Dict[str, Any]:
        return {
            "importer": self.importer, "dry_run": self.dry_run, "created": self.created,
            "duplicates": self.duplicates, "attachments": self.attachments, "by_kind": dict(sorted(self.by_kind.items())),
            "date_basis": dict(sorted(self.date_basis.items())),
            "approximate_dates": sum(n for b, n in self.date_basis.items() if b in APPROXIMATE_BASES or b == "unrecorded"),
            "skipped": dict(sorted(self.skipped.items())), "range": {"earliest": self.earliest, "latest": self.latest},
            "errors": list(self.errors),
        }


def iso_from_epoch(value: Any) -> str:
    return _dt.datetime.fromtimestamp(float(value), tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_utc(value: str) -> str:
    """An ISO 8601 timestamp (any offset, ``Z`` accepted) as UTC ``YYYY-MM-DDTHH:MM:SSZ``."""
    text = str(value).strip().replace("Z", "+00:00")
    dt = _dt.datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def in_window(occurred_at: str, since: Optional[str], until: Optional[str]) -> bool:
    if since and occurred_at < iso_utc(since):
        return False
    if until and occurred_at >= iso_utc(until):
        return False
    return True


def write_drafts(nb: Any, drafts: Iterable[NoteDraft], report: ImportReport, *, dry_run: bool = False,
                 exists: Optional[Callable[[str, str], bool]] = None) -> ImportReport:
    """Capture every draft (bulk priority) or, with ``dry_run``, only count what would happen.

    ``nb`` may be None for a dry run on a notebook that does not exist yet (everything counts as new).
    """
    from abstractnote import ids  # core lane

    report.dry_run = dry_run
    for draft in drafts:
        report.seen(draft)
        if dry_run:
            note_id = ids.note_id_for_import(draft.importer, draft.source_key)
            known = nb is not None and (exists(draft.importer, draft.source_key) if exists else nb.note(note_id) is not None)
            if known:
                report.duplicates += 1
            else:
                report.created += 1
                report.attachments += len(draft.attachments)
            continue
        try:
            result = nb.capture(
                draft.text, title=draft.title, occurred_at=draft.occurred_at, source=draft.source,
                priority=BULK_PRIORITY, attachments=draft.attachments, importer=draft.importer,
                source_key=draft.source_key,
            )
        except ValueError as e:
            report.errors.append({"source_key": draft.source_key, "error": str(e)})
            continue
        if getattr(result, "status", None) == "duplicate":
            report.duplicates += 1
        else:
            report.created += 1
            report.attachments += len(draft.attachments)
    return report


def run(importer: str, nb: Any, *, dry_run: bool = False, **options: Any) -> ImportReport:
    """Run one importer by name (``claude-sessions``, ``agora``, ``markdown``, ``files``)."""
    from . import agora, claude_sessions, files, markdown

    makers: Dict[str, Callable[..., Iterator[NoteDraft]]] = {
        claude_sessions.IMPORTER: claude_sessions.drafts,
        agora.IMPORTER: agora.drafts,
        markdown.IMPORTER: markdown.drafts,
        files.IMPORTER: files.drafts,
    }
    if importer not in makers:
        raise ValueError(f"Unknown importer {importer!r}; choose one of {', '.join(sorted(makers))}.")
    report = ImportReport(importer=importer)
    return write_drafts(nb, makers[importer](report=report, **options), report, dry_run=dry_run)


__all__ = ["APPROXIMATE_BASES", "BULK_PRIORITY", "ImportReport", "NoteDraft", "in_window", "iso_from_epoch", "iso_utc", "run", "write_drafts"]
