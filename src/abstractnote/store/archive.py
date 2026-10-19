"""The archive as it stood on a date: the third time axis (docs/data-model.md "Time").

``as_received = R`` answers "what did my archive hold on R?": only the notes received by R
(``notes.received_at <= R``) count, so material imported later is left out even when its own dates are
older. Everything derived from notes follows them: a revision belongs to the archive at R when the note
that caused it was received by R, and a user action (no note) when it was recorded by R. Entities exist
when a revision in the archive created them or a note in the archive mentions them; merges, names,
aliases, summaries and claim closures count only when their revision belongs; a claim closed by a later
revision is read as it was before that revision (replayed from the revisions that do belong).

``at`` (history time) composes with it: the graph or card "as of T, from what the archive held on R".
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from .. import ids

FOREVER = "9999-12-31T23:59:59Z"

NOTES_SQL = "SELECT note_id FROM notes WHERE received_at<=?"
REVISIONS_SQL = ("SELECT r.revision_id FROM revisions r LEFT JOIN notes n ON n.note_id=r.note_id"
                 " WHERE r.applied_index=1 AND COALESCE(n.received_at, r.recorded_at)<=?")


class Archive:
    """What the archive held at ``as_received``; reads ask it which rows belong."""

    def __init__(self, store: Any, as_received: str) -> None:
        self.store = store
        self.at = ids.normalize_time(as_received, field="as_received")
        self.revisions = {r[0] for r in store.q(REVISIONS_SQL, (self.at,))}
        self._merges: Optional[Dict[str, List[Tuple[str, str, Optional[str]]]]] = None
        self._claim_states: Dict[str, Dict[str, Any]] = {}

    # ---------------------------------------------------------------- SQL filters
    def notes_in(self, col: str) -> Tuple[str, List[Any]]:
        """``col IN (notes received by R)``."""
        return f"{col} IN ({NOTES_SQL})", [self.at]

    def revisions_in(self, col: str) -> Tuple[str, List[Any]]:
        """``col IN (revisions that belong to the archive at R)``."""
        return f"{col} IN ({REVISIONS_SQL})", [self.at]

    def has(self, revision_id: Optional[str]) -> bool:
        return bool(revision_id) and revision_id in self.revisions

    # ---------------------------------------------------------------- merges
    def merges(self) -> Dict[str, List[Tuple[str, str, Optional[str]]]]:
        """``from_id -> [(at, into_id, undone_at or None)]`` for the merges in the archive, newest first; an undo
        counts only when its own revision belongs."""
        if self._merges is None:
            out: Dict[str, List[Tuple[str, str, Optional[str]]]] = {}
            for r in self.store.q("SELECT * FROM merges ORDER BY at DESC, revision_id DESC"):
                if r["revision_id"] not in self.revisions:
                    continue
                undone = r["undone_at"] if self.has(r["undone_revision_id"]) else None
                out.setdefault(r["from_id"], []).append((r["at"], r["into_id"], undone))
            self._merges = out
        return self._merges

    def canonical(self, entity_id: str, at: Optional[str] = None) -> str:
        """The surviving entity as of history time ``at`` (default: any time), by the merges in the archive."""
        ref = at or FOREVER
        merges = self.merges()
        seen = set()
        cur = entity_id
        while cur not in seen:
            seen.add(cur)
            nxt = None
            for m_at, into, undone in merges.get(cur, []):
                if m_at <= ref and (undone is None or undone > ref):
                    nxt = into
                    break
            if nxt is None:
                return cur
            cur = nxt
        return cur

    def members(self, entity_id: str, at: Optional[str] = None) -> List[str]:
        """The entity and every entry merged into it (transitively) in the archive as of ``at``."""
        out = [entity_id]
        for m in sorted(self.merges()):
            if m != entity_id and self.canonical(m, at) == entity_id:
                out.append(m)
        return out

    # ---------------------------------------------------------------- entities
    def entity_stats(self) -> Dict[str, Dict[str, Any]]:
        """``entity_id -> {first_seen, last_seen, notes}`` of each entity in the archive, from its own creation
        (when the creating revision belongs) and its own notes in the archive (``notes`` is a set of note ids)."""
        out: Dict[str, Dict[str, Any]] = {}
        sql, params = self.revisions_in("created_revision_id")
        for r in self.store.q(f"SELECT entity_id, created_at FROM entities WHERE {sql}", params):
            out[r[0]] = {"first_seen": r[1], "last_seen": r[1], "notes": set()}
        sql, params = self.notes_in("note_id")
        for r in self.store.q(f"SELECT entity_id, note_id, at FROM entity_notes WHERE {sql}", params):
            st = out.setdefault(r[0], {"first_seen": r[2], "last_seen": r[2], "notes": set()})
            st["notes"].add(r[1])
            st["first_seen"] = min(st["first_seen"], r[2])
            st["last_seen"] = max(st["last_seen"], r[2])
        return out

    def holds_entity(self, entity_id: str) -> bool:
        sql, params = self.revisions_in("created_revision_id")
        if self.store.q1(f"SELECT 1 FROM entities WHERE entity_id=? AND {sql}", [entity_id, *params]):
            return True
        sql, params = self.notes_in("note_id")
        return self.store.q1(f"SELECT 1 FROM entity_notes WHERE entity_id=? AND {sql}", [entity_id, *params]) is not None

    def name(self, entity_id: str, at: Optional[str] = None) -> Optional[str]:
        sql, params = self.revisions_in("revision_id")
        r = self.store.q1(f"SELECT name FROM entity_names WHERE entity_id=? AND at<=? AND {sql} ORDER BY at DESC,"
                          " revision_id DESC LIMIT 1", [entity_id, at or FOREVER, *params])
        return r[0] if r else None

    def summary(self, entity_id: str, at: Optional[str] = None) -> Optional[str]:
        sql, params = self.revisions_in("revision_id")
        r = self.store.q1(f"SELECT summary FROM entity_summaries WHERE entity_id=? AND at<=? AND {sql} ORDER BY at"
                          " DESC, revision_id DESC LIMIT 1", [entity_id, at or FOREVER, *params])
        return r[0] if r else None

    # ---------------------------------------------------------------- claims
    def claim_state(self, row: Any) -> Dict[str, Any]:
        """``{status, valid_until, superseded_by}`` of a claim as the archive at R held it."""
        cid = row["claim_id"]
        if not row["closed_revision_id"] or row["closed_revision_id"] in self.revisions:
            return {"status": row["status"], "valid_until": row["valid_until"], "superseded_by": row["superseded_by"]}
        if cid in self._claim_states:
            return self._claim_states[cid]
        valid_until = None
        added = self.store.revision_row(row["revision_id"])
        for op in json.loads(added["ops_json"]) if added else []:
            p = op.get("payload") or {}
            if op.get("op") == "claim.add" and p.get("claim_id") == cid:
                valid_until = p.get("valid_until")
        state = {"status": "active", "valid_until": valid_until, "superseded_by": None}
        for r in self.store.q("SELECT revision_id, at, ops_json FROM revisions WHERE applied_index=1 AND ops_json LIKE ?"
                              " ORDER BY seq", (f'%"{cid}"%',)):
            if r["revision_id"] not in self.revisions:
                continue
            for op in json.loads(r["ops_json"]):
                p = op.get("payload") or {}
                if p.get("claim_id") != cid:
                    continue
                if op.get("op") == "claim.supersede" and state["status"] == "active":
                    state = {"status": "superseded", "valid_until": state["valid_until"] or p.get("valid_until"),
                             "superseded_by": p.get("by_claim_id")}
                elif op.get("op") == "claim.retract" and state["status"] != "retracted":
                    state = {"status": "retracted",
                             "valid_until": state["valid_until"] or p.get("valid_until") or r["at"],
                             "superseded_by": state["superseded_by"]}
        self._claim_states[cid] = state
        return state

    def claim_dict(self, row: Any) -> Dict[str, Any]:
        return {**self.store.claim_dict(row), **self.claim_state(row)}


def archive_for(store: Any, as_received: Optional[str]) -> Optional[Archive]:
    """An :class:`Archive` for a non-empty ``as_received``, else None (the archive as it is now)."""
    if as_received in (None, ""):
        return None
    return Archive(store, as_received)
