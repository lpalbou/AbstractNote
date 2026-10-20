"""The archive as it stood on a date: the third time axis (docs/data-model.md "Time").

``as_received = R`` answers "what did my archive hold on R?": only the notes received by R
(``notes.received_at <= R``) count, so material imported later is left out even when its own dates are
older. Everything derived from notes follows them: a revision belongs to the archive at R when the note
that caused it was received by R, and a user action (no note) when it was recorded by R. Entities exist
when a revision in the archive created them or a note in the archive mentions them; merges, names,
aliases, summaries and claim closures count only when their revision belongs; a claim closed by a later
revision is read as it was before that revision (replayed from the revisions that do belong).

``at`` (history time) composes with it: the graph or card "as of T, from what the archive held on R".

A re-understanding (``note.reinterpret``, ADR 0008) belongs when it was RECORDED by R (it is what the processor did
then, not part of the note as received): before it, the archive shows the previous interpretation: its statements
(closed ``reinterpreted``, revised in place or re-opened since), its mentions and topics (soft-removed into
``entity_notes_removed`` / ``note_topics_removed``) and the entities it alone created (retired since).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from .. import ids

FOREVER = "9999-12-31T23:59:59Z"

NOTES_SQL = "SELECT note_id FROM notes WHERE received_at<=?"
REVISIONS_SQL = ("SELECT r.revision_id FROM revisions r LEFT JOIN notes n ON n.note_id=r.note_id"
                 " WHERE r.applied_index=1 AND CASE WHEN r.action='note.reinterpret' THEN r.recorded_at"
                 " ELSE COALESCE(n.received_at, r.recorded_at) END<=?")
#: Ops of a re-understanding that change a statement written by another revision.
REINTERPRET_CLAIM_OPS = ("claim.reinterpret", "claim.revise", "claim.reopen", "claim.repoint")


class Archive:
    """What the archive held at ``as_received``; reads ask it which rows belong."""

    def __init__(self, store: Any, as_received: str) -> None:
        self.store = store
        self.at = ids.normalize_time(as_received, field="as_received")
        self.revisions = {r[0] for r in store.q(REVISIONS_SQL, (self.at,))}
        self._merges: Optional[Dict[str, List[Tuple[str, str, Optional[str]]]]] = None
        self._claim_states: Dict[str, Dict[str, Any]] = {}
        self._later_reinterpret: Optional[Dict[str, List[Dict[str, Any]]]] = None

    # ---------------------------------------------------------------- SQL filters
    def notes_in(self, col: str) -> Tuple[str, List[Any]]:
        """``col IN (notes received by R)``."""
        return f"{col} IN ({NOTES_SQL})", [self.at]

    def revisions_in(self, col: str) -> Tuple[str, List[Any]]:
        """``col IN (revisions that belong to the archive at R)``."""
        return f"{col} IN ({REVISIONS_SQL})", [self.at]

    def has(self, revision_id: Optional[str]) -> bool:
        return bool(revision_id) and revision_id in self.revisions

    def mentions_sql(self) -> Tuple[str, List[Any]]:
        """A subquery of ``(entity_id, note_id, at, revision_id)`` = the mentions as the archive held them: the live
        rows whose revision belongs, and the rows a later re-understanding soft-removed (ADR 0008)."""
        frag, fp = self.revisions_in("revision_id")
        frag2, fp2 = self.revisions_in("removed_revision_id")
        return (f"SELECT entity_id, note_id, at, revision_id FROM entity_notes WHERE {frag} UNION ALL SELECT entity_id,"
                f" note_id, at, revision_id FROM entity_notes_removed WHERE {frag} AND NOT {frag2}", [*fp, *fp, *fp2])

    def retired(self, entity_id: str) -> bool:
        """The entity was retired by a re-understanding the archive holds."""
        r = self.store.q1("SELECT retired_revision_id FROM entities WHERE entity_id=?", (entity_id,))
        return bool(r and r[0] and r[0] in self.revisions)

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
        for r in self.store.q(f"SELECT entity_id, created_at, retired_revision_id FROM entities WHERE {sql}", params):
            if r[2] and r[2] in self.revisions:
                continue  # retired by a re-understanding the archive holds (ADR 0008)
            out[r[0]] = {"first_seen": r[1], "last_seen": r[1], "notes": set()}
        sql, params = self.mentions_sql()
        for r in self.store.q(f"SELECT entity_id, note_id, at FROM ({sql})", params):
            st = out.setdefault(r[0], {"first_seen": r[2], "last_seen": r[2], "notes": set()})
            st["notes"].add(r[1])
            st["first_seen"] = min(st["first_seen"], r[2])
            st["last_seen"] = max(st["last_seen"], r[2])
        return out

    def holds_entity(self, entity_id: str) -> bool:
        sql, params = self.revisions_in("created_revision_id")
        if self.store.q1(f"SELECT 1 FROM entities WHERE entity_id=? AND {sql}", [entity_id, *params]):
            return not self.retired(entity_id)
        sql, params = self.mentions_sql()
        return self.store.q1(f"SELECT 1 FROM ({sql}) WHERE entity_id=?", [*params, entity_id]) is not None

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
    def later_reinterpret(self) -> Dict[str, List[Dict[str, Any]]]:
        """``claim_id -> [op payload + {op, revision_id}]`` of the re-understanding ops the archive does NOT hold (a
        statement they changed is replayed, never read as it stands now)."""
        if self._later_reinterpret is None:
            out: Dict[str, List[Dict[str, Any]]] = {}
            for r in self.store.q("SELECT revision_id, ops_json FROM revisions WHERE applied_index=1 AND"
                                  " action='note.reinterpret' ORDER BY seq"):
                if r["revision_id"] in self.revisions:
                    continue
                for op in json.loads(r["ops_json"]):
                    if op.get("op") in REINTERPRET_CLAIM_OPS:
                        p = op.get("payload") or {}
                        out.setdefault(p.get("claim_id"), []).append({**p, "op": op["op"],
                                                                      "revision_id": r["revision_id"]})
            self._later_reinterpret = out
        return self._later_reinterpret

    def claim_state(self, row: Any) -> Dict[str, Any]:
        """``{status, valid_until, superseded_by}`` of a claim as the archive at R held it."""
        cid = row["claim_id"]
        if cid not in self.later_reinterpret() and (not row["closed_revision_id"] or
                                                    row["closed_revision_id"] in self.revisions):
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
                if op.get("op") in ("claim.insert_before", "claim.start_earlier") and p.get("after_claim_id") == cid \
                        and state["status"] != "retracted" and p.get("after_valid_until"):
                    # the belief before a retrospective statement: its window now ends where that one starts
                    if state["valid_until"] is None or state["valid_until"] > p["after_valid_until"]:
                        state = {"status": "superseded", "valid_until": p["after_valid_until"],
                                 "superseded_by": p.get("claim_id")}
                    continue
                if p.get("claim_id") != cid:
                    continue
                if op.get("op") == "claim.reinterpret":
                    state = {"status": "reinterpreted", "valid_until": p.get("valid_until"),
                             "superseded_by": state["superseded_by"]}
                    continue
                if op.get("op") == "claim.reopen":
                    state = {"status": p.get("status") or "active", "valid_until": p.get("valid_until"),
                             "superseded_by": p.get("superseded_by")}
                    continue
                if op.get("op") == "claim.repoint":
                    state = {**state, "superseded_by": p.get("superseded_by")}
                    continue
                if op.get("op") == "claim.revise":
                    if state["status"] == "active" and (p.get("assertion") or {}).get("valid_until") is not None:
                        state = {**state, "valid_until": p["assertion"]["valid_until"]}
                    continue
                if op.get("op") == "claim.insert_before" and state["status"] == "active":
                    until = p.get("valid_until")
                    if state["valid_until"] is not None and (until is None or state["valid_until"] < until):
                        until = state["valid_until"]
                    state = {"status": "superseded", "valid_until": until, "superseded_by": p.get("before_claim_id")}
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

    def claim_start(self, row: Any) -> Dict[str, Any]:
        """``{observed_at, valid_from}`` of a claim as the archive at R held it: a ``claim.start_earlier`` (an earlier
        note moving the belief's start back) counts only when its revision belongs; the start is replayed from the
        claim's own ``claim.add`` and the moves that do belong."""
        cid = row["claim_id"]
        moves = [] if not row["observed_at"] else self.store.q(
            "SELECT revision_id, ops_json FROM revisions WHERE applied_index=1 AND ops_json LIKE ? ORDER BY seq",
            (f'%"claim.start_earlier"%"{cid}"%',))
        if not moves or all(r["revision_id"] in self.revisions for r in moves):
            return {"observed_at": row["observed_at"], "valid_from": row["valid_from"]}
        start = {"observed_at": row["observed_at"], "valid_from": row["valid_from"]}
        added = self.store.revision_row(row["revision_id"])
        for op in json.loads(added["ops_json"]) if added else []:
            p = op.get("payload") or {}
            if op.get("op") == "claim.add" and p.get("claim_id") == cid:
                start = {"observed_at": p.get("observed_at"), "valid_from": p.get("valid_from")}
        for r in moves:
            if r["revision_id"] not in self.revisions:
                continue
            for op in json.loads(r["ops_json"]):
                p = op.get("payload") or {}
                if op.get("op") == "claim.start_earlier" and p.get("claim_id") == cid:
                    if p.get("observed_at") and (not start["observed_at"] or p["observed_at"] < start["observed_at"]):
                        start["observed_at"] = p["observed_at"]
                    if start["valid_from"] and p.get("valid_from") and p["valid_from"] < start["valid_from"]:
                        start["valid_from"] = p["valid_from"]
        return start

    def claim_dict(self, row: Any) -> Dict[str, Any]:
        out = {**self.store.claim_dict(row), **self.claim_start(row), **self.claim_state(row)}
        revised = [x for x in self.later_reinterpret().get(row["claim_id"], []) if x["op"] == "claim.revise"]
        if revised:  # read again in place after R: the archive shows the reading it held (the first one revised)
            prev = revised[0].get("previous") or {}
            for k in ("certainty", "stance", "valid_from", "evidence"):
                if k in prev:
                    out[k] = prev[k]
        return out


def archive_for(store: Any, as_received: Optional[str]) -> Optional[Archive]:
    """An :class:`Archive` for a non-empty ``as_received``, else None (the archive as it is now)."""
    if as_received in (None, ""):
        return None
    return Archive(store, as_received)
