"""The pinned response shapes of docs/api.md ("Pinned response shapes"); clients decode them strictly."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, Optional

EXCERPT_CHARS = 280


def excerpt(text: Optional[str], limit: int = EXCERPT_CHARS) -> str:
    """A list excerpt of a note body (the full text is always available from the note itself).

    #[WARNING:TRUNCATION] a bounded preview (root ADR-0026 "hierarchical views", ADR 0007): a cut text always ends
    with the "…" marker, and every shape that carries an excerpt carries the id that opens the whole text."""
    t = " ".join(str(text or "").split())
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"


def note_ref(r: sqlite3.Row) -> Dict[str, Any]:
    """``NoteRef``: ``{note_id, title, kind, occurred_at, excerpt}``."""
    return {"note_id": r["note_id"], "title": r["title"] or "", "kind": r["kind"], "occurred_at": r["occurred_at"],
            "excerpt": excerpt(r["summary"] or r["one_line"] or r["text"])}


def insight(r: sqlite3.Row) -> Dict[str, Any]:
    """``Insight``: ``{note_id, title, body, insight_kind, occurred_at, acked_at, evidence_note_ids, entity_ids}``."""
    return {"note_id": r["note_id"], "title": r["title"] or "", "body": r["text"] or "",
            "insight_kind": r["insight_kind"], "occurred_at": r["occurred_at"], "acked_at": r["acked_at"],
            "evidence_note_ids": _list(r["evidence_note_ids_json"]), "entity_ids": _list(r["entity_ids_json"])}


def insight_facts(r: sqlite3.Row) -> Optional[Dict[str, Any]]:
    """The ``insight`` field of ``GET /note/notes/{id}``: ``{kind, evidence_note_ids, entity_ids, acked_at}`` for an
    insight note, ``None`` for any other note."""
    if not r["insight_kind"]:
        return None
    return {"kind": r["insight_kind"], "evidence_note_ids": _list(r["evidence_note_ids_json"]),
            "entity_ids": _list(r["entity_ids_json"]), "acked_at": r["acked_at"]}


def task(r: sqlite3.Row) -> Dict[str, Any]:
    return {"task_id": r["task_id"], "note_id": r["note_id"], "text": r["text"], "status": r["status"],
            "due": r["due"], "closed_at": r["closed_at"]}


def _list(raw: Optional[str]) -> list:
    if not raw:
        return []
    try:
        v = json.loads(raw)
    except ValueError:
        return []
    return v if isinstance(v, list) else []
