"""The note index: ``NoteStore`` over ``note.sqlite3``.

The store owns the SQLite connection and two kinds of code:

- writes of the truth tables (notes, attachments, links, queue rows, revision
  rows, vectors, context packets), used by capture, filing and the queue;
- the **index projection** of a revision (:meth:`NoteStore.project`), the only
  code that changes entities, aliases, claims, relations, topics and tasks. It
  runs in one transaction that also sets the revision's ``applied_index`` flag.

Reads (entity cards, beliefs, graph, day, timeline, feeds) live here too so
that every surface (tools, read API, CLI) shares one implementation of
``as_of``.
"""

from __future__ import annotations

import base64
import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from .. import ids
from .schema import connect, ensure_schema


def _j(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _uj(raw: Optional[str], default: Any = None) -> Any:
    if raw is None or raw == "":
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def encode_cursor(*parts: Any) -> str:
    return base64.urlsafe_b64encode(_j(list(parts)).encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(cursor: Optional[str]) -> Optional[List[Any]]:
    if not cursor:
        return None
    try:
        pad = "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(cursor + pad).decode("utf-8"))
    except Exception:
        raise ValueError("The cursor is not one this notebook issued.") from None
    if not isinstance(value, list):
        raise ValueError("The cursor is not one this notebook issued.")
    return value


_limits_log = logging.getLogger("abstractnote.limits")


def clamp_note(limit: Any, default: int = 50, maximum: int = 500) -> Tuple[int, Optional[str]]:
    """``(applied, sentence)``: the limit a read applies (1..``maximum``; ``default`` when none is given) and, when
    the caller's request was moved into that range, the sentence that says so (None otherwise). ADR 0007: a clamp
    of a caller's request is answered, never silent; it is also logged on ``abstractnote.limits`` with the
    function that clamped."""
    try:
        n = int(limit) if limit is not None else default
    except (TypeError, ValueError):
        raise ValueError("The limit must be a whole number.") from None
    applied = max(1, min(maximum, n))
    if applied == n:
        return applied, None
    import sys

    caller = sys._getframe(2 if sys._getframe(1).f_code.co_name == "clamp_limit" else 1).f_code
    where = f"{caller.co_filename.rsplit('/', 1)[-1]}:{caller.co_name}"
    _limits_log.warning("%s: the limit %d is outside 1..%d; %d was applied", where, n, maximum, applied)
    return applied, f"The limit {n} is outside 1 to {maximum}; {applied} was applied."


def clamp_limit(limit: Any, default: int = 50, maximum: int = 500) -> int:
    """The limit a read applies (see :func:`clamp_note`; a clamp is logged with its caller)."""
    return clamp_note(limit, default, maximum)[0]


def entity_text(name: str, aliases: Sequence[str], summary: Optional[str]) -> str:
    """The embedded text of an entity: name, aliases and summary."""
    parts = [name]
    if aliases:
        parts.append("also known as " + ", ".join(aliases))
    if summary:
        parts.append(summary)
    return "\n".join(parts)


def note_vector_text(title: Optional[str], text: str, attachment_texts: Sequence[str], budget: int) -> str:
    """The embedded text of a note: title, body and attachment text, bounded to
    ``budget`` characters for the embedder call only (full text stays stored and
    searchable through FTS)."""
    parts = [p for p in [title or "", text or "", *attachment_texts] if p]
    joined = "\n".join(parts)
    if len(joined) <= budget:
        return joined
    # #[WARNING:TRUNCATION] the embedder's input bound (NOTE_EMBED_BUDGET): the search vector reads the first
    # ``budget`` characters; the stored note is never cut and its whole text stays searchable by words (FTS)
    _limits_log.warning("note vector: the text has %d characters; the vector reads the first %d (NOTE_EMBED_BUDGET);"
                        " the whole text stays stored and searchable by words", len(joined), budget)
    return joined[:budget]


#: Topics a note keeps from processing: the first the model lists (eval2 D13); a user revision is not capped.
TOPICS_PER_NOTE = 3


#: Meta entry written by :meth:`NoteStore.mark_retro` (retrospective insertions; data-model.md "Time").
RETRO_META = "retro"
RETRO_EVENTS_KEPT = 500
#: A note counts as retrospective for an entity when the entity was already seen more than this much later.
RETRO_MIN_GAP = timedelta(days=1)

#: The order of an entity's summaries (ADR 0008 §2): by the date each one describes (``entity_summaries.at``: the
#: note's date, or for a consolidation summary the latest dated knowledge it was derived from), then by the revision
#: log (``revisions.seq``), never by when it was written alone. The CURRENT summary (``entities.summary``) is the
#: last one in this order; ``summary_history`` lists them in it.
SUMMARY_ORDER = "s.at, COALESCE(v.seq, 0)"
SUMMARY_ORDER_DESC = "s.at DESC, COALESCE(v.seq, 0) DESC"
_SUMMARY_FROM = "entity_summaries s LEFT JOIN revisions v ON v.revision_id=s.revision_id"
#: The revision that re-understands a note (ADR 0008 §1), as its ``action`` or as one of its ops: the action of a
#: revision that supersedes a note's previous interpretation (``REINTERPRET_ACTION`` is the same value).
REINTERPRET = "note.reinterpret"
REINTERPRET_ACTION = REINTERPRET
#: The closure kind of a statement a re-understanding withdrew, stored as the claim's ``status`` and written as the
#: AbstractMemory ClosureRecord kind (``op_claim_reinterpret``). The one definition: other modules import it.
REINTERPRETED = "reinterpreted"
#: A task a re-understanding withdrew (it was the superseded reading's task, the new reading has no such task).
TASK_RETIRED = "retired"
#: A live entity: not merged into another and not retired by a re-understanding (``entity.retire``, ADR 0008).
#: Lists, counts, communities, the timeline and the overview read entities through it (search reaches entities only
#: through their mentions and index rows, which the retirement removes).
LIVE_ENTITY = "merged_into IS NULL AND retired_revision_id IS NULL"
#: User actions whose effects mark the notes after them for re-understanding (ADR 0008 §4, review 3.4). The
#: canonical-name repair (``entity.merge`` from ``normalize``) and duplicate archiving join copies, not meanings.
MARKING_ACTIONS = ("note.redate", "note.archive", "note.unarchive", "claim.retract", "entity.unmerge",
                   "match.confirm")
#: A person's archive withdraws the note's reading (W28): its statements close as a retraction with this reason.
ARCHIVE_REASON = "note archived"
_IS_REINTERPRET = f"(r2.action='{REINTERPRET}' OR r2.ops_json LIKE '%\"{REINTERPRET}\"%')"


#: ADR 0008 §4a: the reason codes of a re-understanding mark.
DIRECT = "direct"
PROPAGATED = "propagated"
#: W28: a person archived a note the marked note builds on (its reading was withdrawn), or brought it back. Both are
#: read like direct marks (the note used what changed): they dominate a propagated mark and are in the first tier.
SOURCE_ARCHIVED = "source_archived"
SOURCE_UNARCHIVED = "source_unarchived"
MARK_CODES = (DIRECT, PROPAGATED, SOURCE_ARCHIVED, SOURCE_UNARCHIVED)


def claim_withdrawn_sql(alias: str = "c") -> str:
    """SQL true for a statement that says nothing any more in the current view: withdrawn by its own note's
    re-understanding (``reinterpreted``), or retracted with an empty window (closed where it opens: a person said
    it was never true, or archived the note that made it, W28). A retraction whose window closed later (a note's
    ``retract`` stance) was believed until then and is not withdrawn."""
    return (f"({alias}.status='{REINTERPRETED}' OR ({alias}.status='retracted' AND {alias}.valid_until IS NOT NULL"
            f" AND {alias}.valid_until<=COALESCE({alias}.valid_from, {alias}.observed_at)))")


def claim_live_sql(alias: str = "c") -> str:
    """SQL true for a claim (table alias ``alias``) that still says something: neither withdrawn by a person
    (``retracted``) nor by its own note's re-understanding (``reinterpreted``). The relation graph of propagation
    (ADR 0008 §4a) uses it; ``status!='retracted'`` alone would still count withdrawn readings."""
    return f"({alias}.status NOT IN ('retracted', '{REINTERPRETED}'))"


def claim_reinterpreted_sql(alias: str = "c") -> str:
    """SQL true for a claim (table alias ``alias``) closed because its own note was understood again (ADR 0008 §1:
    the stored closure kind :data:`REINTERPRETED`, in ``claims.status``): a system event, not a change in what was
    true, so it is no turning point, no earlier belief and no new statement in the dated history views."""
    return f"({alias}.status='{REINTERPRETED}')"


def summary_superseded_sql(within: str = "") -> str:
    """SQL true for a summary (``s``, its revision ``v``) written by an interpretation of a note that a later
    re-interpretation of the same note superseded (ADR 0008 §1): such a summary is out of the dated history and is
    never the current one. ``within`` restricts the superseding revision (``r2``), e.g. to what an archive held."""
    w = f" AND {within}" if within else ""
    # W28: a summary written by a note a person archived (its reading withdrawn) is out of the history too, as long as
    # the note stays archived; as received, only once the archive's revision belongs (``within``)
    return (f"(v.note_id IS NOT NULL AND (EXISTS (SELECT 1 FROM revisions r2 WHERE r2.note_id=v.note_id AND"
            f" r2.seq>v.seq AND r2.abandoned=0 AND {_IS_REINTERPRET}{w}) OR (EXISTS (SELECT 1 FROM notes nx WHERE"
            f" nx.note_id=v.note_id AND nx.archived_at IS NOT NULL) AND EXISTS (SELECT 1 FROM revisions r2 WHERE"
            f" r2.action='note.archive' AND r2.seq>v.seq AND r2.abandoned=0 AND r2.ops_json LIKE '%\"withdrawn\"%'"
            f" AND r2.ops_json LIKE '%' || v.note_id || '%'{w}))))")


#: How many times :func:`summary_superseded_sql` uses its ``within`` condition (its parameters repeat as often).
SUMMARY_WITHIN_USES = 2


class NoteStore:
    """SQLite note index. Thread-safe through one RLock around the connection."""

    def __init__(self, path: str) -> None:
        self.path = str(path)
        self.lock = threading.RLock()
        self.conn = connect(self.path)
        #: ADR 0008 invalidation marks are written while revisions are applied, never while a rebuild replays them
        #: (the marks table is truth and keeps what was already marked and consumed).
        self.marking = True
        with self.lock:
            ensure_schema(self.conn)

    def close(self) -> None:
        with self.lock:
            try:
                self.conn.close()
            except Exception:
                pass

    # ------------------------------------------------------------------ plumbing

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """One write transaction (BEGIN IMMEDIATE ... COMMIT; ROLLBACK on error)."""
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            self.conn.execute("COMMIT")

    def q(self, sql: str, params: Sequence[Any] = ()) -> List[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, params).fetchall()

    def q1(self, sql: str, params: Sequence[Any] = ()) -> Optional[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, params).fetchone()

    def meta_get(self, key: str, default: Any = None) -> Any:
        row = self.q1("SELECT value FROM meta WHERE key=?", (key,))
        return _uj(row[0], default) if row else default

    def meta_set(self, key: str, value: Any, conn: Optional[sqlite3.Connection] = None) -> None:
        c = conn or self.conn
        with self.lock:
            c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, _j(value)))

    # ------------------------------------------------------------------ notes

    def note_exists(self, note_id: str) -> bool:
        return self.q1("SELECT 1 FROM notes WHERE note_id=?", (note_id,)) is not None

    def note_row(self, note_id: str) -> Optional[sqlite3.Row]:
        return self.q1("SELECT * FROM notes WHERE note_id=?", (note_id,))

    def note_topics(self, note_id: str) -> List[str]:
        return [r[0] for r in self.q("SELECT topic FROM note_topics WHERE note_id=? ORDER BY topic", (note_id,))]

    def attachments_of(self, note_id: str) -> List[Dict[str, Any]]:
        rows = self.q("SELECT * FROM attachments WHERE note_id=? ORDER BY position, attachment_id", (note_id,))
        return [self._attachment_dict(r) for r in rows]

    @staticmethod
    def _attachment_dict(r: sqlite3.Row) -> Dict[str, Any]:
        return {
            "attachment_id": r["attachment_id"], "note_id": r["note_id"], "sha256": r["sha256"],
            "size": r["size"], "mime": r["mime"], "filename": r["filename"],
            "artifact_ref": _uj(r["artifact_ref_json"]), "blob_path": r["blob_path"],
            "extracted_by": r["extracted_by"], "extracted_chars": r["extracted_chars"],
        }

    def note_dict(self, r: sqlite3.Row, *, full: bool = True) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "note_id": r["note_id"], "kind": r["kind"], "title": r["title"],
            "summary": r["summary"] or r["one_line"], "occurred_at": r["occurred_at"],
            "received_at": r["received_at"], "processed_at": r["processed_at"],
            "source": _uj(r["source_json"], {}), "location": _uj(r["location_json"]),
            "language": r["language"], "topics": self.note_topics(r["note_id"]),
            "priority": r["priority"], "status": r["status"], "duplicate_of": r["duplicate_of"],
            "session_id": r["session_id"], "run_id": r["run_id"], "archived_at": r["archived_at"],
        }
        if r["insight_kind"]:
            out.update({
                "insight_kind": r["insight_kind"], "evidence_note_ids": _uj(r["evidence_note_ids_json"], []),
                "entity_ids": _uj(r["entity_ids_json"], []), "acked_at": r["acked_at"],
            })
        if full:
            out["text"] = r["text"]
        else:
            text = r["text"] or ""
            # #[WARNING:TRUNCATION] a list preview with its "…" marker; the full text comes with the note (full=True)
            out["excerpt"] = text[:280] + ("…" if len(text) > 280 else "")
        return out

    def insert_note(self, conn: sqlite3.Connection, row: Dict[str, Any]) -> None:
        cols = ",".join(row.keys())
        marks = ",".join("?" for _ in row)
        seq = conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM notes").fetchone()[0]
        conn.execute(f"INSERT INTO notes({cols}, seq) VALUES ({marks}, ?)", [*row.values(), seq])

    def refresh_note_fts(self, conn: sqlite3.Connection, note_id: str, extracted: Optional[Sequence[str]] = None) -> None:
        r = conn.execute("SELECT note_id, title, text, summary, one_line FROM notes WHERE note_id=?", (note_id,)).fetchone()
        if r is None:
            return
        if extracted is None:
            prev = conn.execute("SELECT attachments FROM notes_fts WHERE note_id=?", (note_id,)).fetchone()
            att = prev[0] if prev else ""
        else:
            att = "\n".join(extracted)
        conn.execute("DELETE FROM notes_fts WHERE note_id=?", (note_id,))
        conn.execute(
            "INSERT INTO notes_fts(note_id, title, body, summary, attachments) VALUES (?,?,?,?,?)",
            (note_id, r["title"] or "", r["text"] or "", r["summary"] or r["one_line"] or "", att or ""),
        )

    # ------------------------------------------------------------------ revisions (truth rows)

    def revision_row(self, revision_id: str) -> Optional[sqlite3.Row]:
        return self.q1("SELECT * FROM revisions WHERE revision_id=?", (revision_id,))

    @staticmethod
    def revision_dict(r: sqlite3.Row) -> Dict[str, Any]:
        return {
            "revision_id": r["revision_id"], "note_id": r["note_id"], "attempt": r["attempt"],
            "action": r["action"], "target": r["target"], "at": r["at"], "recorded_at": r["recorded_at"],
            "run_id": r["run_id"], "ops": _uj(r["ops_json"], []), "summary": r["summary"],
            "source": _uj(r["source_json"]), "warnings": _uj(r["warnings_json"], []),
            "applied_index": bool(r["applied_index"]), "applied_kg": bool(r["applied_kg"]),
            "context": _uj(r["context_json"]) if "context_json" in r.keys() else None,
        }

    def insert_revision(self, rev: Dict[str, Any]) -> bool:
        """Write the revision row (the intent). Returns False when the id exists (no-op)."""
        with self.lock:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO revisions(revision_id, note_id, attempt, action, target, at, recorded_at,"
                " run_id, ops_json, summary, source_json, warnings_json, result_json, applied_index, applied_kg,"
                " context_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0,0,?)",
                (rev["revision_id"], rev.get("note_id"), rev.get("attempt"), rev.get("action"), rev.get("target"),
                 rev["at"], rev["recorded_at"], rev.get("run_id"), _j(rev.get("ops") or []), rev.get("summary") or "",
                 _j(rev.get("source")) if rev.get("source") is not None else None, _j(rev.get("warnings") or []),
                 _j(rev.get("result")) if rev.get("result") is not None else None,
                 _j(rev["context"]) if rev.get("context") is not None else None),
            )
            return cur.rowcount > 0

    def pending_revisions(self) -> List[sqlite3.Row]:
        return self.q("SELECT * FROM revisions WHERE abandoned=0 AND (applied_index=0 OR applied_kg=0) ORDER BY seq")

    def abandon_revision(self, revision_id: str, error: str) -> None:
        with self.lock:
            self.conn.execute("UPDATE revisions SET abandoned=1, error=? WHERE revision_id=? AND applied_index=0",
                              (error, revision_id))

    def set_applied_kg(self, revision_id: str) -> None:
        with self.lock:
            self.conn.execute("UPDATE revisions SET applied_kg=1 WHERE revision_id=?", (revision_id,))

    # ------------------------------------------------------------------ entities helpers

    def entity_row(self, entity_id: str) -> Optional[sqlite3.Row]:
        return self.q1("SELECT * FROM entities WHERE entity_id=?", (entity_id,))

    def entity_ids_taken(self) -> "_TakenIds":
        return _TakenIds(self)

    def canonical(self, entity_id: str, conn: Optional[sqlite3.Connection] = None) -> str:
        """Follow ``merged_into`` to the surviving entity."""
        c = conn or self.conn
        seen = set()
        cur = entity_id
        with self.lock:
            while cur not in seen:
                seen.add(cur)
                r = c.execute("SELECT merged_into FROM entities WHERE entity_id=?", (cur,)).fetchone()
                if r is None or not r[0]:
                    return cur
                cur = r[0]
        return cur

    def canonical_at(self, entity_id: str, at: Optional[str]) -> str:
        """The surviving entity as of history time ``at`` (merges done by then and not undone by then)."""
        if at is None:
            return self.canonical(entity_id)
        seen = set()
        cur = entity_id
        while cur not in seen:
            seen.add(cur)
            r = self.q1(
                "SELECT into_id FROM merges WHERE from_id=? AND at<=? AND (undone_at IS NULL OR undone_at>?)"
                " ORDER BY at DESC LIMIT 1", (cur, at, at))
            if r is None:
                return cur
            cur = r[0]
        return cur

    def merged_members(self, entity_id: str, conn: Optional[sqlite3.Connection] = None) -> List[str]:
        """The entity and every entity currently merged into it (transitively)."""
        c = conn or self.conn
        out = [entity_id]
        frontier = [entity_id]
        with self.lock:
            while frontier:
                nxt = []
                for eid in frontier:
                    for r in c.execute("SELECT entity_id FROM entities WHERE merged_into=?", (eid,)):
                        if r[0] not in out:
                            out.append(r[0])
                            nxt.append(r[0])
                frontier = nxt
        return out

    def aliases(self, entity_id: str, at: Optional[str] = None) -> List[str]:
        """The entity's other names as shown: one surface form per canonical name (the first seen), none equal
        to the entity's own canonical name (``Memory`` is not another name of ``memory``)."""
        if at is None:
            rows = self.q("SELECT alias FROM entity_aliases WHERE entity_id=? ORDER BY added_at, alias", (entity_id,))
        else:
            rows = self.q("SELECT alias FROM entity_aliases WHERE entity_id=? AND added_at<=? ORDER BY added_at, alias",
                          (entity_id, at))
        own = self.q1("SELECT name_key FROM entities WHERE entity_id=?", (entity_id,))
        return distinct_names([r[0] for r in rows], exclude=[own[0]] if own and own[0] else [])

    def name_key_holder(self, key: str, exclude: Sequence[str] = (), conn: Optional[sqlite3.Connection] = None
                        ) -> Optional[str]:
        """The live entity whose canonical name (``name_key``) is ``key``, the earliest first; None when there is
        none. Aliases never decide here: an alias is the model's word for an entity (ADR 0004), and only
        candidate generation matches on alias keys."""
        c = conn or self.conn
        if not key:
            return None
        with self.lock:
            rows = c.execute("SELECT entity_id FROM entities WHERE name_key=? AND merged_into IS NULL"
                             " ORDER BY key_clash_with IS NOT NULL, created_at, entity_id", (key,)).fetchall()
        for r in rows:
            if r[0] not in exclude:
                return r[0]
        return None

    def entity_brief(self, entity_id: str) -> Optional[Dict[str, Any]]:
        r = self.entity_row(entity_id)
        if r is None:
            return None
        return {
            "entity_id": r["entity_id"], "kind": r["kind"], "name": r["name"],
            "display_name": r["display_name"] or r["name"],
            "aliases": self.aliases(r["entity_id"]), "summary": r["summary"], "first_seen": r["first_seen"],
            "last_seen": r["last_seen"], "note_count": r["note_count"], "merged_into": r["merged_into"],
            "retired": bool(r["retired_revision_id"]),
        }

    def name_key_groups(self) -> List[List[str]]:
        """Groups of live entities that share a canonical name (two or more each), earliest entity first: the
        groups :meth:`Notebook.normalize_entities` merges."""
        groups: Dict[str, List[str]] = {}
        for r in self.q("SELECT entity_id, name_key FROM entities WHERE merged_into IS NULL AND name_key IS NOT NULL"
                        " ORDER BY created_at, recorded_at, entity_id"):
            groups.setdefault(r[1], []).append(r[0])
        return [g for g in groups.values() if len(g) > 1]

    # ------------------------------------------------------------------ claims helpers

    def claim_row(self, claim_id: str) -> Optional[sqlite3.Row]:
        return self.q1("SELECT * FROM claims WHERE claim_id=?", (claim_id,))

    def claim_dict(self, r: sqlite3.Row, *, names: bool = True) -> Dict[str, Any]:
        out = {
            "claim_id": r["claim_id"], "subject_id": r["subject_id"], "predicate": r["predicate"],
            "object_id": r["object_id"], "object_literal": r["object_literal"], "certainty": r["certainty"],
            "stance": r["stance"], "observed_at": r["observed_at"], "valid_from": r["valid_from"],
            "valid_until": r["valid_until"], "status": r["status"], "superseded_by": r["superseded_by"],
            "evidence": r["evidence"], "note_id": r["note_id"], "revision_id": r["revision_id"],
            "assertion_id": r["assertion_id"], "disputed_with": _uj(r["disputed_with_json"], []),
            "reinforced_count": r["reinforced_count"],
        }
        if names:
            out["subject_name"] = self._name(r["subject_id"])
            if r["object_id"]:
                out["object_name"] = self._name(r["object_id"])
        return out

    def _name(self, entity_id: Optional[str]) -> Optional[str]:
        if not entity_id:
            return None
        r = self.q1("SELECT name FROM entities WHERE entity_id=?", (entity_id,))
        return r[0] if r else None

    def _display(self, entity_id: Optional[str]) -> Optional[str]:
        """The surface name a person reads (``display_name``, else the canonical name)."""
        if not entity_id:
            return None
        r = self.q1("SELECT display_name, name FROM entities WHERE entity_id=?", (entity_id,))
        return (r[0] or r[1]) if r else entity_id

    @staticmethod
    def claim_text(subject_name: str, predicate: str, object_text: str, evidence: Optional[str]) -> str:
        return f"{subject_name} {predicate} {object_text}" + (f"\n{evidence}" if evidence else "")

    # ------------------------------------------------------------------ projection

    def project(self, rev: Dict[str, Any], *, keys: Any) -> Dict[str, Any]:
        """Apply one revision's ops to the index in one transaction and set ``applied_index``.

        ``keys.entity(text)`` and ``keys.claim(payload, conn)`` name the vectors of
        changed entities and new claims (the caller fills them after commit).
        Returns the projection stats. Re-projection is a no-op once the flag is set.
        """
        stats: Dict[str, Any] = {
            "entities_created": [], "entities_merged": [], "claims_added": [], "claims_reinforced": [],
            "claims_superseded": [], "claims_retracted": [], "tasks_opened": [], "tasks_closed": [],
            "touched_entities": [], "touched_claims": [], "claims_inserted_before": [], "retro_entities": [],
            "claims_started_earlier": [],
        }
        with self.tx() as c:
            row = c.execute("SELECT applied_index FROM revisions WHERE revision_id=?", (rev["revision_id"],)).fetchone()
            if row is None:
                raise RuntimeError(f"Revision {rev['revision_id']} has no row; write the intent first.")
            if row[0]:
                return stats
            ctx = _OpContext(self, c, rev, stats, keys)
            for op in rev.get("ops") or []:
                ctx.apply(op["op"], dict(op.get("payload") or {}))
            ctx.finish()
            c.execute("UPDATE revisions SET applied_index=1 WHERE revision_id=?", (rev["revision_id"],))
        return stats

    def refresh_relation(self, c: sqlite3.Connection, subject_id: str, object_id: str, predicate: str) -> None:
        """Recompute one relation row (canonical endpoints) from its claims."""
        s_members = self.merged_members(subject_id, c)
        o_members = self.merged_members(object_id, c)
        qs = ",".join("?" for _ in s_members)
        qo = ",".join("?" for _ in o_members)
        rows = c.execute(
            f"SELECT claim_id, observed_at, status, valid_until FROM claims WHERE subject_id IN ({qs})"
            f" AND object_id IN ({qo}) AND predicate=?", [*s_members, *o_members, predicate]).fetchall()
        c.execute("DELETE FROM relations WHERE subject_id=? AND object_id=? AND predicate=?",
                  (subject_id, object_id, predicate))
        if not rows:
            return
        ids_ = [r["claim_id"] for r in rows]
        reinf = c.execute(
            f"SELECT COUNT(*), MAX(at) FROM claim_reinforcements WHERE claim_id IN ({','.join('?' for _ in ids_)})",
            ids_).fetchone()
        first = min(r["observed_at"] for r in rows)
        last = max([r["observed_at"] for r in rows] + ([reinf[1]] if reinf[1] else []))
        active = sorted(r["claim_id"] for r in rows if r["status"] == "active" and r["valid_until"] is None)
        c.execute(
            "INSERT INTO relations(subject_id, object_id, predicate, first_seen, last_seen, count, active_claim_ids_json)"
            " VALUES (?,?,?,?,?,?,?)",
            (subject_id, object_id, predicate, first, last, len(rows) + int(reinf[0] or 0), _j(active)))

    def refresh_relations_for(self, c: sqlite3.Connection, entity_ids: Iterable[str]) -> None:
        """Recompute every relation touching the given entities (after merges/unmerges)."""
        members: set = set()
        for eid in entity_ids:
            members.update(self.merged_members(self.canonical(eid, c), c))
            members.add(eid)
        if not members:
            return
        q = ",".join("?" for _ in members)
        triples = c.execute(
            f"SELECT DISTINCT subject_id, object_id, predicate FROM claims WHERE object_id IS NOT NULL AND"
            f" (subject_id IN ({q}) OR object_id IN ({q}))", [*members, *members]).fetchall()
        c.execute(f"DELETE FROM relations WHERE subject_id IN ({q}) OR object_id IN ({q})", [*members, *members])
        seen = set()
        for t in triples:
            key = (self.canonical(t[0], c), self.canonical(t[1], c), t[2])
            if key in seen:
                continue
            seen.add(key)
            self.refresh_relation(c, *key)

    def refresh_entity_stats(self, c: sqlite3.Connection, entity_id: str) -> None:
        members = self.merged_members(entity_id, c)
        q = ",".join("?" for _ in members)
        r = c.execute(f"SELECT MIN(at), MAX(at), COUNT(DISTINCT note_id) FROM entity_notes WHERE entity_id IN ({q})",
                      members).fetchone()
        created = c.execute("SELECT created_at FROM entities WHERE entity_id=?", (entity_id,)).fetchone()
        first = min([x for x in (r[0], created[0] if created else None) if x], default=None)
        c.execute("UPDATE entities SET first_seen=?, last_seen=?, note_count=? WHERE entity_id=?",
                  (first, r[1] or first, int(r[2] or 0), entity_id))

    def refresh_entity_fts(self, c: sqlite3.Connection, entity_id: str) -> None:
        r = c.execute("SELECT name, display_name, summary FROM entities WHERE entity_id=?", (entity_id,)).fetchone()
        if r is None:
            return
        aliases = [a[0] for a in c.execute("SELECT alias FROM entity_aliases WHERE entity_id=? ORDER BY alias",
                                           (entity_id,))]
        name = r["name"] if (r["display_name"] or r["name"]) == r["name"] else f"{r['name']} {r['display_name']}"
        c.execute("DELETE FROM entities_fts WHERE entity_id=?", (entity_id,))
        c.execute("INSERT INTO entities_fts(entity_id, name, aliases, summary) VALUES (?,?,?,?)",
                  (entity_id, name, " ".join(aliases), r["summary"] or ""))

    def refresh_topic(self, c: sqlite3.Connection, topic: str) -> None:
        r = c.execute(
            "SELECT MIN(n.occurred_at), MAX(n.occurred_at), COUNT(*) FROM note_topics t JOIN notes n"
            " ON n.note_id=t.note_id WHERE t.topic=?", (topic,)).fetchone()
        if not r[2]:
            c.execute("DELETE FROM topics WHERE topic=?", (topic,))
            return
        c.execute("INSERT OR REPLACE INTO topics(topic, first_seen, last_seen, note_count) VALUES (?,?,?,?)",
                  (topic, r[0], r[1], r[2]))

    def assign_topic(self, c: sqlite3.Connection, note_id: str, topic: str, *, by: str,
                     revision_id: Optional[str], at: str) -> bool:
        """File the note under the topic's canonical name (:func:`abstractnote.ids.canonical_name`: ``Bug-Fix``,
        ``bug fix`` and ``bug_fix`` are one topic)."""
        topic = ids.canonical_name(topic)
        if not topic:
            return False
        cur = c.execute("INSERT OR IGNORE INTO note_topics(note_id, topic, assigned_by, revision_id, at)"
                        " VALUES (?,?,?,?,?)", (note_id, topic, by, revision_id, at))
        self.refresh_topic(c, topic)
        return cur.rowcount > 0

    # ------------------------------------------------------------------ reads: beliefs and graph

    def beliefs_at(self, at: str, *, subject_id: Optional[str] = None, entity_id: Optional[str] = None,
                   as_received: Optional[str] = None) -> List[Dict[str, Any]]:
        """Claims valid at ``at``: ``valid_from <= at`` and (``valid_until`` empty or ``> at``); with
        ``as_received``, from what the archive held on that date (see :mod:`abstractnote.store.archive`)."""
        from .archive import archive_for

        at = ids.normalize_time(at, field="at")
        arch = archive_for(self, as_received)
        sql = "SELECT * FROM claims WHERE 1"
        params: List[Any] = []
        if arch is None:
            sql += (" AND (valid_from IS NULL OR valid_from<=?) AND (valid_until IS NULL OR valid_until>?) AND status!=?"
                    f" AND NOT {claim_withdrawn_sql('claims')}")
            params += [at, at, REINTERPRETED]  # ADR 0008: a withdrawn reading (W28: or an empty retraction)
        else:  # the start is the archive's (a start a later re-reading gave back is still moved as received then)
            frag, fp = arch.revisions_in("revision_id")
            sql += f" AND {frag}"
            params += fp

        def members_of(eid: str) -> List[str]:
            return arch.members(arch.canonical(eid)) if arch else self.merged_members(self.canonical(eid))

        if subject_id:
            members = members_of(subject_id)
            sql += f" AND subject_id IN ({','.join('?' for _ in members)})"
            params += members
        if entity_id:
            members = members_of(entity_id)
            q = ",".join("?" for _ in members)
            sql += f" AND (subject_id IN ({q}) OR object_id IN ({q}))"
            params += members + members
        sql += " ORDER BY observed_at, claim_id"
        rows = self.q(sql, params)
        if arch is None:
            return [self.claim_dict(r) for r in rows]
        out = []
        for r in rows:
            d = arch.claim_dict(r)
            if d["status"] == "retracted" and d["valid_until"] is not None and \
                    d["valid_until"] <= (d["valid_from"] or d["observed_at"]):
                continue  # an empty retraction (W28: never true, or its note archived) is never a belief
            if (d["valid_until"] is None or d["valid_until"] > at) and (d["valid_from"] is None or d["valid_from"] <= at):
                out.append(d)  # a start an earlier note moved back counts only when that note was received
        return out

    def graph(self, *, focus: Optional[Sequence[str]] = None, at: Optional[str] = None, depth: int = 1,
              since: Optional[str] = None, until: Optional[str] = None, kinds: Optional[Sequence[str]] = None,
              min_count: int = 1, limit: int = 200, as_received: Optional[str] = None) -> Dict[str, Any]:
        """Nodes and edges as of ``at`` (None = now). An entity exists at T when it was
        first seen at or before T; an edge exists when one of its claims was observed by T.
        ``as_received`` restricts both to what the archive held on that date (notes received by then and what
        they caused; :mod:`abstractnote.store.archive`); it composes with ``at``."""
        from .archive import archive_for

        arch = archive_for(self, as_received)
        at_n = ids.optional_time(at, field="at")
        canon = (lambda e: arch.canonical(e, at_n)) if arch else (lambda e: self.canonical_at(e, at_n))
        since_n = ids.optional_time(since, field="since")
        until_n = ids.optional_time(until, field="until")
        limit = clamp_limit(limit, 200, 2000)
        depth, _ = clamp_note(depth or 1, 1, 3)  # a clamp is logged (ADR 0007)
        bound = at_n
        if until_n and (bound is None or until_n < bound):
            bound = until_n
        sql = "SELECT * FROM claims WHERE object_id IS NOT NULL"
        params: List[Any] = []
        if bound:
            sql += " AND observed_at<=?"
            params.append(bound)
        if since_n:
            sql += " AND observed_at>=?"
            params.append(since_n)
        if arch:
            frag, fp = arch.revisions_in("revision_id")
            sql += f" AND {frag}"
            params += fp
        else:  # ADR 0008: a statement a re-understanding withdrew is not an edge (the archive view shows it before);
            # W28: nor one retracted with an empty window (its note archived, or a person said it was never true)
            sql += f" AND status!=? AND NOT {claim_withdrawn_sql('claims')}"
            params.append(REINTERPRETED)
        claims = self.q(sql, params)
        edges: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        for r in claims:
            s = canon(r["subject_id"])
            o = canon(r["object_id"])
            if s == o:
                continue  # both ends are one entity by then (a confirmed close match): no edge to draw
            key = (s, o, r["predicate"])
            e = edges.setdefault(key, {"source": s, "target": o, "predicate": r["predicate"], "first_seen": None,
                                       "last_seen": None, "count": 0, "active": False, "claim_ids": []})
            e["count"] += 1
            e["claim_ids"].append(r["claim_id"])
            e["first_seen"] = min(filter(None, [e["first_seen"], r["observed_at"]]))
            e["last_seen"] = max(filter(None, [e["last_seen"], r["observed_at"]]))
            ref = at_n or "9999-12-31T23:59:59Z"
            st = arch.claim_state(r) if arch else r
            vf, vu = r["valid_from"], st["valid_until"]
            if (vf is None or vf <= ref) and (vu is None or vu > ref) and (at_n is not None or st["status"] == "active"):
                e["active"] = True
        edge_list = [e for e in edges.values() if e["count"] >= max(1, int(min_count or 1))]

        nodes: Dict[str, Any]
        if arch:
            nodes = self._archive_nodes(arch, at_n)
        else:
            node_sql = "SELECT * FROM entities WHERE retired_revision_id IS NULL"  # ADR 0008: a retired entity
            nparams: List[Any] = []
            if at_n:
                node_sql += " AND COALESCE(first_seen, created_at)<=? AND (merged_into IS NULL OR merged_at>?)"
                nparams += [at_n, at_n]
            else:
                node_sql += " AND merged_into IS NULL"
            nodes = {r["entity_id"]: r for r in self.q(node_sql, nparams)}
        if kinds:
            nodes = {k: v for k, v in nodes.items() if v["kind"] in set(kinds)}
        edge_list = [e for e in edge_list if e["source"] in nodes and e["target"] in nodes]

        if focus:
            keep = {canon(f) for f in focus}
            frontier = set(keep)
            for _ in range(depth):
                nxt = set()
                for e in edge_list:
                    if e["source"] in frontier and e["target"] not in keep:
                        nxt.add(e["target"])
                    if e["target"] in frontier and e["source"] not in keep:
                        nxt.add(e["source"])
                keep |= nxt
                frontier = nxt
            nodes = {k: v for k, v in nodes.items() if k in keep}
            edge_list = [e for e in edge_list if e["source"] in nodes and e["target"] in nodes]
        elif since_n or until_n:
            touched = {e["source"] for e in edge_list} | {e["target"] for e in edge_list}
            nodes = {k: v for k, v in nodes.items() if k in touched or (
                (not since_n or (v["last_seen"] or "") >= since_n) and (not until_n or (v["first_seen"] or "") <= until_n))}

        # a selection (the most mentioned first), recorded (ADR 0007): ``omitted`` counts the nodes past ``limit``
        # and the edges that touch them
        ranked = sorted(nodes.values(), key=lambda r: (-(r["note_count"] or 0), r["entity_id"]))
        omitted = {"nodes": max(0, len(ranked) - limit), "edges": 0}
        ranked = ranked[:limit]
        keep_ids = {r["entity_id"] for r in ranked}
        kept_edges = [e for e in edge_list if e["source"] in keep_ids and e["target"] in keep_ids]
        omitted["edges"] = len(edge_list) - len(kept_edges)
        edge_list = kept_edges
        out_nodes = []
        for r in ranked:
            summary = r["summary"]
            if at_n and not arch:
                srow = self.current_summary_row(r["entity_id"], at=at_n)
                summary = srow["summary"] if srow else None
            out_nodes.append({"id": r["entity_id"], "kind": r["kind"], "name": r["name"],
                              "display_name": r["display_name"] or r["name"],
                              "first_seen": r["first_seen"], "last_seen": r["last_seen"],
                              "note_count": r["note_count"], "summary": summary})
        for e in edge_list:
            e.pop("claim_ids", None)
        edge_list.sort(key=lambda e: (e["source"], e["target"], e["predicate"]))
        out: Dict[str, Any] = {"as_of": at_n or ids.utc_now(), "nodes": out_nodes, "edges": edge_list,
                               "omitted": omitted}
        if arch:
            out["as_received"] = arch.at
        return out

    def _archive_nodes(self, arch: Any, at_n: Optional[str]) -> Dict[str, Dict[str, Any]]:
        """Graph nodes from what the archive held (as of ``at_n``): stats over the archive's notes of each surviving
        entity and the entries merged into it, its name and summary as the archive's revisions left them."""
        nodes: Dict[str, Dict[str, Any]] = {}
        for eid, st in arch.entity_stats().items():
            c = arch.canonical(eid, at_n)
            agg = nodes.setdefault(c, {"first_seen": st["first_seen"], "last_seen": st["last_seen"], "notes": set()})
            agg["first_seen"] = min(agg["first_seen"], st["first_seen"])
            agg["last_seen"] = max(agg["last_seen"], st["last_seen"])
            agg["notes"] |= st["notes"]
        out: Dict[str, Dict[str, Any]] = {}
        for c, agg in nodes.items():
            row = self.entity_row(c)
            if row is None or (at_n and agg["first_seen"] > at_n):
                continue
            out[c] = {"entity_id": c, "kind": row["kind"], "name": arch.name(c, at_n) or row["name"],
                      "display_name": row["display_name"] or row["name"],
                      "first_seen": agg["first_seen"], "last_seen": agg["last_seen"], "note_count": len(agg["notes"]),
                      "summary": arch.summary(c, at_n)}
        return out

    # ------------------------------------------------------------------ reads: lists

    def list_notes(self, *, q: Optional[str] = None, since: Optional[str] = None, until: Optional[str] = None,
                   kind: Optional[str] = None, topic: Optional[str] = None, entity: Optional[str] = None,
                   status: Optional[str] = None, cursor: Optional[str] = None, limit: int = 50,
                   include_archived: bool = False) -> Dict[str, Any]:
        limit = clamp_limit(limit)
        sql = "SELECT n.* FROM notes n WHERE 1=1"
        params: List[Any] = []
        if not include_archived and status != "archived":
            sql += " AND n.archived_at IS NULL"
        if since:
            sql += " AND n.occurred_at>=?"
            params.append(ids.normalize_time(since, field="since"))
        if until:
            sql += " AND n.occurred_at<=?"
            params.append(ids.normalize_time(until, field="until"))
        if kind:
            sql += " AND n.kind=?"
            params.append(kind)
        if status:
            sql += " AND n.status=?" if status != "archived" else " AND n.archived_at IS NOT NULL"
            if status != "archived":
                params.append(status)
        if topic:
            sql += " AND n.note_id IN (SELECT note_id FROM note_topics WHERE topic IN (?, ?))"
            params += [topic, ids.canonical_name(topic)]
        if entity:
            members = self.merged_members(self.canonical(entity))
            sql += f" AND n.note_id IN (SELECT note_id FROM entity_notes WHERE entity_id IN ({','.join('?' for _ in members)}))"
            params += members
        if q:
            terms = fts_terms(q)
            if terms:
                sql += " AND n.note_id IN (SELECT note_id FROM notes_fts WHERE notes_fts MATCH ?)"
                params.append(terms)
        cur = decode_cursor(cursor)
        if cur:
            sql += " AND (n.occurred_at<? OR (n.occurred_at=? AND n.note_id<?))"
            params += [cur[0], cur[0], cur[1]]
        sql += " ORDER BY n.occurred_at DESC, n.note_id DESC LIMIT ?"
        params.append(limit + 1)
        rows = self.q(sql, params)
        items = [self.note_dict(r, full=False) for r in rows[:limit]]
        nxt = encode_cursor(rows[limit - 1]["occurred_at"], rows[limit - 1]["note_id"]) if len(rows) > limit else None
        return {"items": items, "next_cursor": nxt}

    def list_entities(self, *, q: Optional[str] = None, kind: Optional[str] = None, sort: str = "recent",
                      cursor: Optional[str] = None, limit: int = 50, include_merged: bool = False) -> Dict[str, Any]:
        limit = clamp_limit(limit)
        sort = sort or "recent"
        if sort not in ("recent", "frequent", "name"):
            raise ValueError("The sort must be recent, frequent or name.")
        sql = "SELECT e.* FROM entities e WHERE 1=1"
        params: List[Any] = []
        if not include_merged:
            sql += " AND e.merged_into IS NULL AND e.retired_revision_id IS NULL"
        if kind:
            sql += " AND e.kind=?"
            params.append(kind)
        if q:
            terms = fts_terms(q)
            if terms:
                sql += " AND e.entity_id IN (SELECT entity_id FROM entities_fts WHERE entities_fts MATCH ?)"
                params.append(terms)
        key_col = {"recent": "COALESCE(e.last_seen, '')", "frequent": "printf('%012d', e.note_count)",
                   "name": "lower(e.name)"}[sort]
        direction = "ASC" if sort == "name" else "DESC"
        cur = decode_cursor(cursor)
        if cur:
            op = ">" if direction == "ASC" else "<"
            sql += f" AND ({key_col}{op}? OR ({key_col}=? AND e.entity_id{op}?))"
            params += [cur[0], cur[0], cur[1]]
        sql = sql.replace("SELECT e.*", f"SELECT e.*, {key_col} AS sort_key")
        sql += f" ORDER BY sort_key {direction}, e.entity_id {direction} LIMIT ?"
        params.append(limit + 1)
        rows = self.q(sql, params)
        items = [self.entity_brief(r["entity_id"]) for r in rows[:limit]]
        nxt = encode_cursor(rows[limit - 1]["sort_key"], rows[limit - 1]["entity_id"]) if len(rows) > limit else None
        return {"items": items, "next_cursor": nxt}

    def revisions_feed(self, *, since: Optional[str] = None, until: Optional[str] = None,
                       entity: Optional[str] = None, cursor: Optional[str] = None, limit: int = 50) -> Dict[str, Any]:
        limit = clamp_limit(limit)
        sql = "SELECT * FROM revisions WHERE applied_index=1"
        params: List[Any] = []
        if since:
            sql += " AND at>=?"
            params.append(ids.normalize_time(since, field="since"))
        if until:
            sql += " AND at<=?"
            params.append(ids.normalize_time(until, field="until"))
        if entity:
            members = self.merged_members(self.canonical(entity))
            likes = " OR ".join("ops_json LIKE ?" for _ in members)
            sql += f" AND ({likes})"
            params += [f'%"{m}"%' for m in members]  # exact id string inside the stored ops JSON
        cur = decode_cursor(cursor)
        if cur:
            sql += " AND seq<?"
            params.append(int(cur[0]))
        sql += " ORDER BY seq DESC LIMIT ?"
        params.append(limit + 1)
        rows = self.q(sql, params)
        items = [self.revision_dict(r) for r in rows[:limit]]
        nxt = encode_cursor(rows[limit - 1]["seq"]) if len(rows) > limit else None
        return {"items": items, "next_cursor": nxt}

    def topics(self) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.q("SELECT topic, first_seen, last_seen, note_count FROM topics"
                                        " ORDER BY note_count DESC, topic")]

    def topic_names(self) -> List[str]:
        return [r[0] for r in self.q("SELECT topic FROM topics ORDER BY note_count DESC, topic")]

    def tasks(self, *, status: Optional[str] = "open", entity: Optional[str] = None, limit: int = 100
              ) -> List[Dict[str, Any]]:
        limit = clamp_limit(limit, 100, 1000)
        sql = "SELECT * FROM tasks WHERE 1=1"
        params: List[Any] = []
        if status:
            sql += " AND status=?"
            params.append(status)
        if entity:
            members = self.merged_members(self.canonical(entity))
            sql += (" AND note_id IN (SELECT note_id FROM entity_notes WHERE entity_id IN"
                    f" ({','.join('?' for _ in members)}))")
            params += members
        sql += " ORDER BY opened_at, task_id LIMIT ?"
        params.append(limit)
        return [self.task_dict(r) for r in self.q(sql, params)]

    @staticmethod
    def task_dict(r: sqlite3.Row) -> Dict[str, Any]:
        return {"task_id": r["task_id"], "note_id": r["note_id"], "text": r["text"], "status": r["status"],
                "due": r["due"], "opened_at": r["opened_at"], "closed_at": r["closed_at"],
                "closed_by_note_id": r["closed_by_note_id"]}

    def insights(self, *, kind: Optional[str] = None, acked: Optional[bool] = None, limit: int = 50
                 ) -> List[Dict[str, Any]]:
        limit = clamp_limit(limit)
        sql = "SELECT * FROM notes WHERE insight_kind IS NOT NULL AND archived_at IS NULL"
        params: List[Any] = []
        if kind:
            sql += " AND insight_kind=?"
            params.append(kind)
        if acked is True:
            sql += " AND acked_at IS NOT NULL"
        elif acked is False:
            sql += " AND acked_at IS NULL"
        sql += " ORDER BY received_at DESC, note_id DESC LIMIT ?"
        params.append(limit)
        return [self.note_dict(r) for r in self.q(sql, params)]

    def day(self, day: str, tz: Optional[str] = None, as_received: Optional[str] = None) -> Dict[str, Any]:
        """Notes, revisions and entities touched on one calendar day in ``tz`` (default UTC); with ``as_received``,
        only what the archive held on that date (notes received by then and the revisions they caused)."""
        from .archive import archive_for

        arch = archive_for(self, as_received)
        try:
            d = datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            raise ValueError("The day must be written YYYY-MM-DD.") from None
        zone = timezone.utc
        if tz:
            try:
                from zoneinfo import ZoneInfo

                zone = ZoneInfo(tz)
            except Exception:
                raise ValueError(f"The time zone {tz!r} is not known.") from None
        start = d.replace(tzinfo=zone).astimezone(timezone.utc)
        end = (d + timedelta(days=1)).replace(tzinfo=zone).astimezone(timezone.utc)
        s, e = start.strftime("%Y-%m-%dT%H:%M:%SZ"), end.strftime("%Y-%m-%dT%H:%M:%SZ")

        def only(col: str, kind: str) -> Tuple[str, List[Any]]:
            if arch is None:
                return "", []
            frag, fp = arch.notes_in(col) if kind == "note" else arch.revisions_in(col)
            return f" AND {frag}", fp

        f_note, p_note = only("note_id", "note")
        f_rev, p_rev = only("revision_id", "revision")
        f_created, p_created = only("created_revision_id", "revision")
        f_closed, p_closed = only("closed_revision_id", "revision")
        notes = [self.note_dict(r, full=False) for r in self.q(
            "SELECT * FROM notes WHERE occurred_at>=? AND occurred_at<? AND archived_at IS NULL" + f_note +
            " ORDER BY occurred_at, note_id", (s, e, *p_note))]
        revs = [self.revision_dict(r) for r in self.q(
            "SELECT * FROM revisions WHERE applied_index=1 AND at>=? AND at<?" + f_rev + " ORDER BY at, seq",
            (s, e, *p_rev))]
        if arch is None:
            touched_rows = self.q("SELECT DISTINCT entity_id FROM entity_notes WHERE at>=? AND at<? ORDER BY entity_id",
                                  (s, e))
        else:  # the mentions as the archive held them (ADR 0008: a superseded reading's mentions, before it was)
            msql, mp = arch.mentions_sql()
            touched_rows = self.q(f"SELECT DISTINCT entity_id FROM ({msql}) WHERE at>=? AND at<? ORDER BY entity_id",
                                  (*mp, s, e))
        touched = [self.entity_brief(r[0]) for r in touched_rows]
        # ADR 0008: an entry retired by a re-understanding is left out (as received before the retirement, kept)
        created = [r[0] for r in self.q("SELECT entity_id, retired_revision_id FROM entities WHERE created_at>=? AND"
                                        " created_at<?" + f_created, (s, e, *p_created))
                   if not (r[1] and (arch is None or arch.has(r[1])))]
        superseded = [self.claim_dict(r) for r in self.q(
            "SELECT * FROM claims WHERE status='superseded' AND valid_until>=? AND valid_until<?" + f_closed,
            (s, e, *p_closed))]
        for r in revs:
            r.pop("ops", None)
        out = {"day": day, "tz": tz or "UTC", "start": s, "end": e, "notes": notes, "revisions": revs,
               "entities_touched": [t for t in touched if t], "entities_created": created,
               "claims_superseded": superseded}
        if arch:
            out["as_received"] = arch.at
        return out

    def timeline(self, *, since: Optional[str] = None, until: Optional[str] = None,
                 granularity: str = "day") -> Dict[str, Any]:
        """Activity per bucket (history time): notes, revisions, new entities, superseded claims."""
        width = {"day": 10, "month": 7, "week": None}
        if granularity not in width:
            raise ValueError("The granularity must be day, week or month.")
        since_n = ids.optional_time(since, field="since")
        until_n = ids.optional_time(until, field="until")

        def bucket(ts: Optional[str]) -> Optional[str]:
            if not ts:
                return None
            if granularity == "week":
                dt = ids.parse_time(ts)
                monday = dt - timedelta(days=dt.weekday())
                return monday.strftime("%Y-%m-%d")
            return ts[: width[granularity]]

        def rng(col: str, table: str, extra: str = "") -> List[str]:
            sql = f"SELECT {col} FROM {table} WHERE {col} IS NOT NULL {extra}"
            params: List[Any] = []
            if since_n:
                sql += f" AND {col}>=?"
                params.append(since_n)
            if until_n:
                sql += f" AND {col}<=?"
                params.append(until_n)
            return [r[0] for r in self.q(sql, params)]

        buckets: Dict[str, Dict[str, int]] = {}
        for name, values in (
            ("notes", rng("occurred_at", "notes", "AND archived_at IS NULL")),
            ("revisions", rng("at", "revisions", "AND applied_index=1")),
            # ADR 0008: an entry a re-understanding retired is not counted as created
            ("entities_created", rng("COALESCE(first_seen, created_at)", "entities", "AND retired_revision_id IS NULL")),
            ("claims_superseded", rng("valid_until", "claims", "AND status='superseded'")),
        ):
            for v in values:
                b = bucket(v)
                if b is None:
                    continue
                buckets.setdefault(b, {"notes": 0, "revisions": 0, "entities_created": 0, "claims_superseded": 0})
                buckets[b][name] += 1
        return {"granularity": granularity, "since": since_n, "until": until_n,
                "buckets": [{"bucket": k, **v} for k, v in sorted(buckets.items())]}

    # ------------------------------------------------------------------ dated summaries

    def summary_rows(self, entity_ids: Sequence[str], *, at: Optional[str] = None, where: str = "",
                     params: Sequence[Any] = (), superseded_within: Tuple[str, Sequence[Any]] = ("", ()),
                     conn: Optional[sqlite3.Connection] = None) -> List[sqlite3.Row]:
        """The dated history of these entities' summaries (:data:`SUMMARY_ORDER`), the ones describing a date at or
        before ``at`` when given; ``where`` adds a condition on ``s`` (the summary) or ``v`` (its revision). A summary
        written by a superseded interpretation of its note (:func:`summary_superseded_sql`) is left out; with
        ``superseded_within`` = (condition on ``r2``, params) only when the superseding revision satisfies it (an
        archive as received keeps the summaries the archive had not seen superseded yet). Each row carries the
        summary's columns plus ``seq`` and ``recorded_at`` of its revision."""
        ids_ = list(entity_ids)
        if not ids_:
            return []
        within, within_params = superseded_within
        sql = (f"SELECT s.*, v.seq AS seq, v.recorded_at AS recorded_at FROM {_SUMMARY_FROM} WHERE s.entity_id IN"
               f" ({','.join('?' for _ in ids_)}) AND NOT {summary_superseded_sql(within)}")
        args: List[Any] = [*ids_, *(list(within_params) * SUMMARY_WITHIN_USES if within else [])]
        if at is not None:
            sql += " AND s.at<=?"
            args.append(at)
        if where:
            sql += f" AND ({where})"
            args.extend(params)
        sql += f" ORDER BY {SUMMARY_ORDER}"
        c = conn or self.conn
        with self.lock:
            return list(c.execute(sql, args).fetchall())

    def current_summary_row(self, entity_id: str, *, at: Optional[str] = None,
                            conn: Optional[sqlite3.Connection] = None) -> Optional[sqlite3.Row]:
        """The entity's current summary (as of ``at`` when given): the last one in :data:`SUMMARY_ORDER`."""
        rows = self.summary_rows([entity_id], at=at, conn=conn)
        return rows[-1] if rows else None

    def current_summaries(self, conn: Optional[sqlite3.Connection] = None) -> Dict[str, sqlite3.Row]:
        """``{entity_id: row}`` of every entity's current summary (one query; :meth:`current_summary_row`)."""
        c = conn or self.conn
        out: Dict[str, sqlite3.Row] = {}
        with self.lock:
            for r in c.execute(f"SELECT s.*, v.seq AS seq FROM {_SUMMARY_FROM} WHERE NOT {summary_superseded_sql()}"
                               f" ORDER BY {SUMMARY_ORDER}").fetchall():
                out[r["entity_id"]] = r  # the last in dated order wins
        return out

    def refresh_superseded_summaries(self, c: sqlite3.Connection, revision_id: str, note_id: Optional[str]) -> List[str]:
        """After a re-interpretation of a note (ADR 0008 §1), the summaries its earlier interpretations wrote are out
        of the dated history: every entity they described gets its current summary selected again (null when none
        is left: the consolidation then writes one). Returns those entity ids. A no-op for any other revision."""
        if not note_id:
            return []
        r = c.execute("SELECT action, ops_json FROM revisions WHERE revision_id=?", (revision_id,)).fetchone()
        if r is None or not (r["action"] == REINTERPRET or
                             any(op.get("op") == REINTERPRET for op in _uj(r["ops_json"], []) or [])):
            return []
        eids = [x[0] for x in c.execute("SELECT DISTINCT s.entity_id FROM entity_summaries s JOIN revisions v ON"
                                        " v.revision_id=s.revision_id WHERE v.note_id=? AND v.revision_id!=?"
                                        " ORDER BY s.entity_id", (note_id, revision_id)).fetchall()]
        for eid in eids:
            cur = self.current_summary_row(eid, conn=c)
            c.execute("UPDATE entities SET summary=? WHERE entity_id=?", (cur["summary"] if cur else None, eid))
        return eids

    # ------------------------------------------------------------------ retrospective insertion

    def mark_retro(self, c: sqlite3.Connection, entity_ids: Iterable[str], revision_id: Optional[str],
                   note_id: Optional[str], recorded_at: str) -> None:
        """Record a retrospective insertion (material older than what was known about these entities) in the
        ``retro`` meta entry, inside the caller's transaction: bump ``graph_version``, mark each entity's summary
        stale as of this revision's sequence number (cleared by a later summary that is the current one), log the
        event (communities count them since their last computation) and drop the entities' then-versus-now
        cards. See :meth:`retro_state`."""
        eids = sorted({e for e in entity_ids if e})
        if not eids:
            return
        seq_row = c.execute("SELECT seq FROM revisions WHERE revision_id=?", (revision_id,)).fetchone() \
            if revision_id else None
        seq = int(seq_row[0]) if seq_row else int(c.execute("SELECT COALESCE(MAX(seq), 0) FROM revisions")
                                                     .fetchone()[0])
        row = c.execute("SELECT value FROM meta WHERE key=?", (RETRO_META,)).fetchone()
        state = _uj(row[0], {}) if row else {}
        state = state if isinstance(state, dict) else {}
        marks = dict(state.get("stale_summaries") or {})
        for e in eids:
            marks[e] = max(int(marks.get(e) or 0), seq)
        current = self.current_summary_seqs(c, list(marks))
        marks = {e: q for e, q in marks.items() if current.get(e, 0) <= q}  # refreshed since: no longer stale
        events = list(state.get("events") or [])
        events.append({"revision_id": revision_id, "note_id": note_id, "recorded_at": recorded_at,
                       "entities": len(eids)})
        state.update({"graph_version": int(state.get("graph_version") or 0) + 1,
                      "insertions": int(state.get("insertions") or 0) + 1,
                      "events": events[-RETRO_EVENTS_KEPT:], "stale_summaries": marks})
        c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (RETRO_META, _j(state)))
        keys = ["entity:" + e for e in eids]
        c.execute(f"DELETE FROM then_now_cache WHERE key IN ({','.join('?' for _ in keys)})", keys)

    def current_summary_seqs(self, c: sqlite3.Connection, entity_ids: Sequence[str]) -> Dict[str, int]:
        """Revision sequence number of each entity's CURRENT summary (the last in :data:`SUMMARY_ORDER`, the one
        ``entities.summary`` shows), 0 when it has none."""
        out: Dict[str, int] = {}
        for e in entity_ids:
            r = c.execute(f"SELECT v.seq FROM {_SUMMARY_FROM} WHERE s.entity_id=? AND NOT {summary_superseded_sql()}"
                          f" ORDER BY {SUMMARY_ORDER_DESC} LIMIT 1", (e,)).fetchone()
            out[e] = int(r[0]) if r else 0
        return out

    def retro_state(self, *, threshold: int) -> Dict[str, Any]:
        """``{graph_version, insertions, stale_summaries: [{entity_id, marked_seq}], retro_notes_since_communities,
        communities_due, threshold}``: what retrospective insertions left to refresh (read only)."""
        with self.lock:
            state = self.meta_get(RETRO_META, {}) or {}
            marks = dict(state.get("stale_summaries") or {})
            current = self.current_summary_seqs(self.conn, list(marks))
        stale = [{"entity_id": e, "marked_seq": int(q)} for e, q in sorted(marks.items())
                 if current.get(e, 0) <= int(q) and self.entity_row(e) is not None
                 and self.entity_row(e)["merged_into"] is None]
        meta = self.meta_get("communities", {}) or {}
        computed = meta.get("computed_at") if isinstance(meta, dict) else None
        since = [ev for ev in state.get("events") or [] if not computed or str(ev.get("recorded_at") or "") > computed]
        notes = len({ev.get("note_id") or ev.get("revision_id") for ev in since})
        return {"graph_version": int(state.get("graph_version") or 0), "insertions": int(state.get("insertions") or 0),
                "stale_summaries": stale, "retro_notes_since_communities": notes,
                "communities_due": notes >= int(threshold), "threshold": int(threshold)}

    # ------------------------------------------------------------------ ADR 0008: interpretations and marks

    def interpretation_chain(self, note_id: str, c: Optional[sqlite3.Connection] = None) -> List[str]:
        """The applied interpretation revisions of a note in order: its processing revision and the
        ``note.reinterpret`` revisions that superseded it (the last one not superseded is the current one)."""
        conn = c or self.conn
        with self.lock:
            rows = conn.execute("SELECT revision_id FROM revisions WHERE note_id=? AND applied_index=1 AND abandoned=0 AND"
                                " (action IS NULL OR action=?) ORDER BY seq", (note_id, REINTERPRET_ACTION)).fetchall()
        return [r[0] for r in rows]

    def reading_record(self, note_id: str, revision_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """The dependency record the note's current reading (or ``revision_id``) stands on: the newest
        ``reading_confirmations`` row for it (an unchanged re-reading: what it was read against), else the revision's
        own ``context_json`` as written at apply time; None for a reading without a record (``context_unknown``)."""
        rid = revision_id or self.current_interpretation(note_id)
        if rid is None:
            return None
        row = self.q1("SELECT context_json FROM reading_confirmations WHERE note_id=? AND revision_id=? ORDER BY"
                      " confirmation_id DESC LIMIT 1", (note_id, rid))
        if row is None:
            rev = self.revision_row(rid)
            row = (rev["context_json"],) if rev is not None else None
        value = _uj(row[0]) if row is not None and row[0] else None
        return value if isinstance(value, dict) else None

    def current_interpretation(self, note_id: str, c: Optional[sqlite3.Connection] = None) -> Optional[str]:
        """The note's current interpretation revision (the latest applied one no ``note.reinterpret`` superseded),
        or None when the note was never understood."""
        conn = c or self.conn
        with self.lock:
            row = conn.execute(
                "SELECT revision_id FROM revisions WHERE note_id=? AND applied_index=1 AND abandoned=0 AND (action IS NULL"
                " OR action=?) AND revision_id NOT IN (SELECT revision_id FROM revision_supersessions) ORDER BY seq DESC"
                " LIMIT 1", (note_id, REINTERPRET_ACTION)).fetchone()
        return row[0] if row else None

    @staticmethod
    def note_order_key(row: Any) -> Tuple[str, str, str]:
        """History order of notes (ADR 0008 §4): the date, then (same date) the time it was received, then its id."""
        return (row["occurred_at"], row["received_at"], row["note_id"])

    def used_by(self, c: sqlite3.Connection, note_id: str) -> Dict[str, List[str]]:
        """What the note's current interpretation USED (the invalidation key, never what its packet merely showed):
        ``{entity, claim, task_opened, task_closed}`` from ``note_dependencies`` plus its live mentions."""
        out: Dict[str, List[str]] = {"entity": [], "claim": [], "task_opened": [], "task_closed": []}
        for kind, key in c.execute("SELECT kind, key FROM note_dependencies WHERE note_id=? ORDER BY kind, key",
                                   (note_id,)).fetchall():
            out.setdefault(kind, []).append(key)
        for (eid,) in c.execute("SELECT entity_id FROM entity_notes WHERE note_id=?", (note_id,)).fetchall():
            if eid not in out["entity"]:
                out["entity"].append(eid)
        return out

    def mark_reinterpret(self, c: sqlite3.Connection, marks: Sequence[Dict[str, Any]], marked_at: str,
                         upgraded: Optional[List[str]] = None) -> List[str]:
        """Insert re-understanding marks ``[{note_id, reason, by_note_id?, by_revision_id?, entities?, from_at?,
        reason_code?, source_entity_id?, source_revision_id?, impact?, hops?}]`` in the caller's transaction.
        ``reason_code`` is ``direct`` (the default: the note used what changed, ADR 0008 §4) or ``propagated`` (an idea
        it relies on was understood differently, §4a: with its source entity, the re-understanding that changed it,
        the ``impact`` carried and the ``hops`` from the first change). Idempotent: a note that already has an untaken
        mark is not marked twice (the unique index); a note whose mark a pass has taken gets a new row, for the next
        pass (review 3.5). An untaken propagated mark gives way to a direct one, and to a stronger propagated one
        (max, never a sum); ``upgraded`` collects those notes. Returns the notes newly marked."""
        out: List[str] = []
        for m in marks:
            code = m.get("reason_code") or DIRECT
            if code not in MARK_CODES:
                raise ValueError(f"A re-understanding mark is one of {', '.join(MARK_CODES)}, not {code!r}.")
            impact = 1.0 if code != PROPAGATED else float(m["impact"])
            vals = (m["reason"], m.get("by_note_id"), m.get("by_revision_id"), _j(sorted(set(m.get("entities") or []))),
                    m.get("from_at"), marked_at, code, m.get("source_entity_id"), m.get("source_revision_id"), impact,
                    int(m.get("hops") or 0))
            cur = c.execute(
                "INSERT OR IGNORE INTO reinterpret_marks(note_id, reason, by_note_id, by_revision_id, entities_json,"
                " from_at, marked_at, reason_code, source_entity_id, source_revision_id, impact, hops)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (m["note_id"], *vals))
            if cur.rowcount:
                out.append(m["note_id"])
                continue
            have = c.execute("SELECT mark_id, reason_code, impact FROM reinterpret_marks WHERE note_id=? AND"
                             " consumed_at IS NULL AND pass_id IS NULL", (m["note_id"],)).fetchone()
            if have is None or (have["reason_code"] or DIRECT) != PROPAGATED:
                continue  # direct dominates (and a direct mark is never doubled)
            if code != PROPAGATED or impact > float(have["impact"]):
                c.execute("UPDATE reinterpret_marks SET reason=?, by_note_id=?, by_revision_id=?, entities_json=?,"
                          " from_at=?, marked_at=?, reason_code=?, source_entity_id=?, source_revision_id=?, impact=?,"
                          " hops=? WHERE mark_id=?", (*vals, have["mark_id"]))
                if upgraded is not None:
                    upgraded.append(m["note_id"])
        return out

    def take_mark(self, note_id: str, pass_id: str) -> int:
        """A pass takes the note's pending marks (``pass_id``, ``taken_at``): the untaken ones and those an earlier
        pass took but did not consume (it failed or held them); returns how many."""
        with self.tx() as c:
            cur = c.execute("UPDATE reinterpret_marks SET pass_id=?, taken_at=?, error=NULL WHERE note_id=? AND"
                            " consumed_at IS NULL AND (pass_id IS NULL OR pass_id!=?)",
                            (pass_id, ids.utc_now(), note_id, pass_id))
            return int(cur.rowcount or 0)

    def consume_marks(self, note_id: str, pass_id: Optional[str], outcome: str, revision_id: Optional[str] = None,
                      calls: Optional[int] = None, c: Optional[sqlite3.Connection] = None) -> int:
        """Stamp the marks a pass took for the note as consumed with ``outcome`` (reinterpreted | unchanged); without a
        ``pass_id`` (a direct call), every taken or untaken mark of the note marked before now."""
        now = ids.utc_now()

        def run(conn: sqlite3.Connection) -> int:
            if pass_id:
                cur = conn.execute("UPDATE reinterpret_marks SET consumed_at=?, outcome=?, consumed_revision_id=?, calls=?,"
                                   " error=NULL WHERE note_id=? AND pass_id=? AND consumed_at IS NULL",
                                   (now, outcome, revision_id, calls, note_id, pass_id))
            else:
                cur = conn.execute("UPDATE reinterpret_marks SET consumed_at=?, outcome=?, consumed_revision_id=?, calls=?,"
                                   " pass_id=COALESCE(pass_id, 'direct'), taken_at=COALESCE(taken_at, ?), error=NULL"
                                   " WHERE note_id=? AND consumed_at IS NULL AND marked_at<=?",
                                   (now, outcome, revision_id, calls, now, note_id, now))
            return int(cur.rowcount or 0)

        if c is not None:
            return run(c)
        with self.tx() as conn:
            return run(conn)

    def fail_mark(self, note_id: str, pass_id: str, error: str) -> None:
        """A failed re-understanding (review 4.2): the mark stays pending (taken by this pass, so the next pass takes it
        again), with the error; the note's previous interpretation stays current."""
        with self.tx() as c:
            c.execute("UPDATE reinterpret_marks SET error=? WHERE note_id=? AND pass_id=? AND consumed_at IS NULL",
                      (" ".join(str(error or "").split()) or "It could not be understood again.", note_id, pass_id))

    def pending_marks(self, *, pass_id: Optional[str] = None, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Notes waiting to be re-understood, in history order (ADR 0008: ascending date, then received time, then
        id), one row per note: ``{note_id, occurred_at, received_at, reasons, by_note_ids, entities, marked_at,
        taken_by}``. With ``pass_id``, the notes that pass has not taken yet (a note it took is left for the next
        pass, review §6)."""
        from ..recall.propagation import TUNING

        sql = ("SELECT m.*, n.occurred_at, n.received_at, n.archived_at FROM reinterpret_marks m JOIN notes n ON"
               " n.note_id=m.note_id WHERE m.consumed_at IS NULL AND n.archived_at IS NULL")
        params: List[Any] = []
        if pass_id:
            sql += " AND m.note_id NOT IN (SELECT note_id FROM reinterpret_marks WHERE pass_id=?)"
            params.append(pass_id)
        sql += " ORDER BY n.occurred_at, n.received_at, n.note_id, m.mark_id"
        by_note: Dict[str, Dict[str, Any]] = {}
        for r in self.q(sql, params):
            d = by_note.get(r["note_id"])
            if d is None:
                d = by_note[r["note_id"]] = {"note_id": r["note_id"], "occurred_at": r["occurred_at"],
                                             "received_at": r["received_at"], "reasons": [], "by_note_ids": [],
                                             "entities": [], "marked_at": r["marked_at"], "taken_by": r["pass_id"],
                                             "error": r["error"], "reason_code": PROPAGATED, "impact": 0.0,
                                             "hops": None, "sources": [], "tier": "B"}
            if r["reason"] not in d["reasons"]:
                d["reasons"].append(r["reason"])
            if r["by_note_id"] and r["by_note_id"] not in d["by_note_ids"]:
                d["by_note_ids"].append(r["by_note_id"])
            for e in _uj(r["entities_json"], []) or []:
                if e not in d["entities"]:
                    d["entities"].append(e)
            # ADR 0008 §4a: a direct mark dominates; otherwise the strongest propagated one decides the tier
            code = r["reason_code"] or DIRECT
            if code != PROPAGATED and d["reason_code"] in (PROPAGATED, code):
                d["reason_code"] = code  # read like direct (W28: an archived or restored foundation)
            elif code != PROPAGATED:
                d["reason_code"] = DIRECT
            if r["source_entity_id"] and r["source_entity_id"] not in d["sources"]:
                d["sources"].append(r["source_entity_id"])
            d["impact"] = max(d["impact"], float(r["impact"] if r["impact"] is not None else 1.0))
            h = int(r["hops"] or 0)
            d["hops"] = h if d["hops"] is None else min(d["hops"], h)
            if code != PROPAGATED or d["impact"] >= TUNING.TIER_A_IMPACT:
                d["tier"] = "A"
        # two tiers (direct and strong propagated marks first), each one forward sweep in history order: a single
        # queue by impact would read a note before its own earlier foundation was read again
        out = sorted(by_note.values(), key=lambda d: d["tier"] != "A")
        return out if limit is None else out[: max(0, int(limit))]

    def reinterpret_marks(self) -> List[Dict[str, Any]]:
        """The untaken pending marks, one row per mark, in history order: ``[{mark_id, note_id, reason, by_note_id,
        marked_at, from_at, entities}]`` (a mark a pass has taken is in flight, :meth:`consume_reinterpret_mark`)."""
        rows = self.q("SELECT m.*, n.occurred_at, n.received_at FROM reinterpret_marks m JOIN notes n ON"
                      " n.note_id=m.note_id WHERE m.consumed_at IS NULL AND m.pass_id IS NULL AND n.archived_at IS NULL"
                      " ORDER BY n.occurred_at, n.received_at, n.note_id, m.mark_id")
        return [{"mark_id": r["mark_id"], "note_id": r["note_id"], "reason": r["reason"], "by_note_id": r["by_note_id"],
                 "marked_at": r["marked_at"], "from_at": r["from_at"], "entities": _uj(r["entities_json"], []),
                 "reason_code": r["reason_code"] or DIRECT, "source_entity_id": r["source_entity_id"],
                 "source_revision_id": r["source_revision_id"], "impact": r["impact"], "hops": r["hops"]}
                for r in rows]

    def consume_reinterpret_mark(self, note_id: str, pass_id: str = "direct") -> Optional[Dict[str, Any]]:
        """Take the note's untaken mark for re-understanding (``pass_id``, ``taken_at``): the note is in flight; a
        mark arriving from now on is a new row for the next pass (review 3.5). Returns the taken mark (the first, with
        ``taken`` = how many were taken) or None. The apply of the re-understanding stamps it consumed."""
        first = self.q1("SELECT * FROM reinterpret_marks WHERE note_id=? AND consumed_at IS NULL AND pass_id IS NULL"
                        " ORDER BY mark_id LIMIT 1", (note_id,))
        if first is None:
            return None
        n = self.take_mark(note_id, pass_id)
        return {"mark_id": first["mark_id"], "note_id": note_id, "reason": first["reason"],
                "by_note_id": first["by_note_id"], "marked_at": first["marked_at"], "pass_id": pass_id, "taken": n}

    def reinterpret_state(self) -> Dict[str, Any]:
        """``{pending, by_entity: [{entity_id, name, notes}], reasons: [sentence], context_unknown, consumed_today,
        last_marked_at, propagated: {pending, by_source: [{entity_id, name, notes}]}}``: what is waiting to be
        re-understood and why, readable before the stage runs (review 3.2). ``propagated`` = the notes waiting only
        because an idea they rely on was understood differently (ADR 0008 §4a), by source idea;
        ``Notebook.reinterpret_state`` adds the upper bound of what may follow."""
        pend = self.pending_marks()
        sources: Dict[str, int] = {}
        prop = [p for p in pend if p["reason_code"] == PROPAGATED]
        for p in prop:
            for e in p["sources"]:
                sources[e] = sources.get(e, 0) + 1
        top_sources = sorted(sources.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
        counts: Dict[str, int] = {}
        reasons: List[str] = []
        for p in pend:
            for e in p["entities"]:
                counts[e] = counts.get(e, 0) + 1
            for why in p["reasons"]:
                if why not in reasons:
                    reasons.append(why)
        top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
        unknown = 0
        if pend:
            q = ",".join("?" for _ in pend)
            unknown = int(self.q1(
                f"SELECT COUNT(DISTINCT r.note_id) FROM revisions r WHERE r.note_id IN ({q}) AND r.applied_index=1 AND"
                f" r.action IS NULL AND r.context_json IS NULL AND r.revision_id NOT IN (SELECT revision_id FROM"
                f" revision_supersessions)", [p["note_id"] for p in pend])[0] or 0)
        day = ids.utc_now()[:10]
        consumed = int(self.q1("SELECT COUNT(*) FROM reinterpret_marks WHERE consumed_at>=?", (day,))[0] or 0)
        last = self.q1("SELECT MAX(marked_at) FROM reinterpret_marks")[0]
        return {"pending": len(pend), "by_entity": [{"entity_id": e, "name": self._name(e), "notes": n} for e, n in top],
                "reasons": reasons[:10], "context_unknown": unknown, "consumed_today": consumed, "last_marked_at": last,
                "propagated": {"pending": len(prop), "by_source": [{"entity_id": e, "name": self._display(e), "notes": n}
                                                                   for e, n in top_sources]}}

    def later_dependents(self, c: sqlite3.Connection, entity_ids: Iterable[str], *, after: Optional[Tuple[str, str, str]]
                         = None, from_at: Optional[str] = None, exclude: Sequence[str] = (),
                         opened_task_texts: Sequence[str] = ()) -> Dict[str, Dict[str, Any]]:
        """The notes after a point in history whose CURRENT interpretation used one of ``entity_ids`` (or an entity
        whose name or alias has the same canonical name as one of theirs: the F2 match, exact keys only, ADR 0004),
        or closed a task like one of ``opened_task_texts`` (whole-word containment either way; review 3.7). ``after``
        = the order key of the note that caused it (only notes strictly after it in history order: forward only, same
        date ordered by received time); ``from_at`` = a date (notes on or after it; user actions). Only notes that
        were understood and are not archived. Returns ``{note_id: {entities: [...], tasks: bool}}``."""
        canon = {self.canonical(e, c) for e in entity_ids if e}
        members: set = set()
        for e in canon:
            members.update(self.merged_members(e, c))
        keys: set = set()
        for e in sorted(members):
            r = c.execute("SELECT name_key, display_name, name FROM entities WHERE entity_id=?", (e,)).fetchone()
            if r is not None:
                keys.update(k for k in (r["name_key"], ids.name_key(r["display_name"] or r["name"])) if k)
            keys.update(a[0] for a in c.execute("SELECT alias_key FROM entity_aliases WHERE entity_id=? AND alias_key IS"
                                                " NOT NULL", (e,)).fetchall())
        named: Dict[str, str] = {}
        if keys:
            kq = ",".join("?" for _ in keys)
            for (eid,) in c.execute(f"SELECT entity_id FROM entities WHERE name_key IN ({kq}) UNION SELECT entity_id FROM"
                                    f" entity_aliases WHERE alias_key IN ({kq})", [*keys, *keys]).fetchall():
                if eid not in members:
                    named[eid] = self.canonical(eid, c)
        targets = {e: self.canonical(e, c) for e in members}
        targets.update(named)
        where, params = self._after_sql(after, from_at)
        out: Dict[str, Dict[str, Any]] = {}
        skip = set(exclude)
        if targets:
            tq = ",".join("?" for _ in targets)
            rows = c.execute(
                f"SELECT x.note_id, x.entity_id FROM (SELECT note_id, entity_id FROM entity_notes WHERE entity_id IN ({tq})"
                f" UNION SELECT note_id, key AS entity_id FROM note_dependencies WHERE kind='entity' AND key IN ({tq}))"
                f" x JOIN notes n ON n.note_id=x.note_id WHERE n.archived_at IS NULL AND n.processed_at IS NOT NULL"
                f" AND {where}", [*targets, *targets, *params]).fetchall()
            for nid, eid in rows:
                if nid in skip:
                    continue
                d = out.setdefault(nid, {"entities": [], "tasks": False})
                e = targets.get(eid, eid)
                if e not in d["entities"]:
                    d["entities"].append(e)
        texts = [" " + " ".join(_words(t)) + " " for t in opened_task_texts if _words(t)]
        if texts:
            rows = c.execute(
                f"SELECT d.note_id, t.text FROM note_dependencies d JOIN tasks t ON t.task_id=d.key JOIN notes n ON"
                f" n.note_id=d.note_id WHERE d.kind='task_closed' AND n.archived_at IS NULL AND n.processed_at IS NOT"
                f" NULL AND {where}", params).fetchall()
            for nid, text in rows:
                if nid in skip:
                    continue
                w = " " + " ".join(_words(text)) + " "
                if w.strip() and any(w in t or t in w for t in texts):
                    out.setdefault(nid, {"entities": [], "tasks": False})["tasks"] = True
        return out

    @staticmethod
    def _after_sql(after: Optional[Tuple[str, str, str]], from_at: Optional[str]) -> Tuple[str, List[Any]]:
        if after is not None:
            t, rcv, nid = after
            return ("(n.occurred_at>? OR (n.occurred_at=? AND (n.received_at>? OR (n.received_at=? AND"
                    " n.note_id>?))))", [t, t, rcv, rcv, nid])
        if from_at is not None:
            return "n.occurred_at>=?", [from_at]
        raise ValueError("later_dependents needs a point in history (after) or a date (from_at).")

    def counts(self) -> Dict[str, int]:
        def n(sql: str) -> int:
            return int(self.q1(sql)[0])

        return {
            "notes": n("SELECT COUNT(*) FROM notes WHERE insight_kind IS NULL"),
            "insights": n("SELECT COUNT(*) FROM notes WHERE insight_kind IS NOT NULL"),
            "attachments": n("SELECT COUNT(*) FROM attachments"),
            "entities": n("SELECT COUNT(*) FROM entities WHERE merged_into IS NULL AND retired_revision_id IS NULL"),
            "entities_merged": n("SELECT COUNT(*) FROM entities WHERE merged_into IS NOT NULL"),
            "claims": n("SELECT COUNT(*) FROM claims"),
            "claims_active": n("SELECT COUNT(*) FROM claims WHERE status='active'"),
            "relations": n("SELECT COUNT(*) FROM relations"),
            "revisions": n("SELECT COUNT(*) FROM revisions WHERE applied_index=1"),
            "topics": n("SELECT COUNT(*) FROM topics"),
            "tasks_open": n("SELECT COUNT(*) FROM tasks WHERE status='open'"),
        }


class _TakenIds:
    """Container view of used entity ids (``in`` support for ``ids.mint_entity_id``)."""

    def __init__(self, store: NoteStore, extra: Optional[set] = None) -> None:
        self._store = store
        self.extra = set(extra or ())

    def __contains__(self, entity_id: object) -> bool:
        return entity_id in self.extra or self._store.entity_row(str(entity_id)) is not None


def distinct_names(names: Iterable[str], exclude: Iterable[str] = ()) -> List[str]:
    """Names with one surface form per canonical name (the first), leaving out those whose canonical name is in
    ``exclude`` (names or keys): what a person sees as "also known as"."""
    seen = {ids.name_key(x) for x in exclude if str(x or "").strip()}
    out: List[str] = []
    for n in names:
        k = ids.name_key(n)
        if k and k not in seen:
            seen.add(k)
            out.append(n)
    return out


def _words(text: Any) -> List[str]:
    """Case-folded words (letters and digits), for the structural whole-word checks of ADR 0008 marks."""
    return "".join(ch if ch.isalnum() else " " for ch in ids.fold(str(text or ""))).split()


def fts_terms(text: str) -> str:
    """FTS5 MATCH string: every word of the query as a quoted term, OR-joined (BM25 ranks)."""
    words = [w for w in "".join(ch if ch.isalnum() else " " for ch in str(text or "")).split() if w]
    return " OR ".join(f'"{w}"' for w in words)


class _OpContext:
    """Applies ops inside the projection transaction."""

    def __init__(self, store: NoteStore, c: sqlite3.Connection, rev: Dict[str, Any], stats: Dict[str, Any],
                 keys: Any) -> None:
        self.s = store
        self.c = c
        self.rev = rev
        self.stats = stats
        self.at = rev["at"]
        self.recorded_at = rev["recorded_at"]
        self.rid = rev["revision_id"]
        self.note_id = rev.get("note_id")
        self.keys = keys
        self.dirty_entities: set = set()
        self.dirty_relations: set = set()
        self.dirty_relation_entities: set = set()
        self.retro: set = set()
        # ADR 0008: what this revision did that marks later notes for re-understanding, and the entities it retired
        self.adr8: Dict[str, Any] = {"redated": [], "archived": [], "retracted": [], "paired": [], "retired": set(),
                                     "stale": []}

    def apply(self, op: str, p: Dict[str, Any]) -> None:
        fn = getattr(self, "op_" + op.replace(".", "_"), None)
        if fn is None:
            raise ValueError(f"The revision op {op!r} is not known.")
        fn(p)

    # --- entities
    def _key_holder(self, key: str, eid: str) -> Optional[str]:
        """Another live entity holding the canonical name ``key`` (the unique index's view), or None."""
        r = self.c.execute("SELECT entity_id FROM entities WHERE name_key=? AND merged_into IS NULL AND"
                           " key_clash_with IS NULL AND entity_id!=?", (key, eid)).fetchone()
        return r[0] if r else None

    def op_entity_create(self, p: Dict[str, Any]) -> None:
        eid = p["entity_id"]
        if self.c.execute("SELECT 1 FROM entities WHERE entity_id=?", (eid,)).fetchone():
            return
        surface = str(p["name"]).strip()
        key = ids.canonical_name(surface)
        # Notebook.apply_revision turns a create of a taken canonical name into a reuse; only a revision recorded
        # before canonical names (replayed by a rebuild) gets here with a taken key: it is created as a marked
        # duplicate that normalize_entities merges (its own later merge revision, when it was recorded).
        clash = self._key_holder(key, eid)
        self.c.execute(
            "INSERT INTO entities(entity_id, kind, name, summary, created_at, created_revision_id, recorded_at,"
            " first_seen, last_seen, note_count, name_key, display_name, key_clash_with)"
            " VALUES (?,?,?,?,?,?,?,?,?,0,?,?,?)",
            (eid, p["kind"], key, p.get("summary"), self.at, self.rid, self.recorded_at, self.at, self.at,
             key, surface, clash))
        self.c.execute("INSERT OR IGNORE INTO entity_names(entity_id, name, at, revision_id) VALUES (?,?,?,?)",
                       (eid, key, self.at, self.rid))
        for alias in p.get("aliases") or []:
            self._alias(eid, alias)
        if p.get("summary"):
            self.c.execute("INSERT OR IGNORE INTO entity_summaries(entity_id, revision_id, at, note_id, summary,"
                           " previous) VALUES (?,?,?,?,?,NULL)", (eid, self.rid, self.at, self.note_id, p["summary"]))
        self.stats["entities_created"].append(eid)
        self.dirty_entities.add(eid)

    def _alias(self, eid: str, alias: str) -> None:
        alias = str(alias or "").strip()
        if not alias:
            return
        f = ids.fold(alias)
        row = self.c.execute("SELECT name, display_name FROM entities WHERE entity_id=?", (eid,)).fetchone()
        if row is not None and f in {ids.fold(row[0]), ids.fold(row[1] or row[0])}:
            return
        self.c.execute("INSERT OR IGNORE INTO entity_aliases(entity_id, alias, alias_fold, added_at, revision_id,"
                       " alias_key) VALUES (?,?,?,?,?,?)", (eid, alias, f, self.at, self.rid, ids.name_key(alias)))
        self.dirty_entities.add(eid)

    def op_entity_alias(self, p: Dict[str, Any]) -> None:
        self._require_entity(p["entity_id"])
        self._alias(p["entity_id"], p["alias"])

    def op_entity_rename(self, p: Dict[str, Any]) -> None:
        eid = p["entity_id"]
        row = self._require_entity(eid)
        previous = row["display_name"] or row["name"]
        surface = str(p["name"]).strip()
        if previous == surface:
            return
        key = ids.canonical_name(surface)
        clash = self._key_holder(key, eid) if row["merged_into"] is None else None
        self.c.execute("UPDATE entities SET name=?, name_key=?, display_name=?, key_clash_with=? WHERE entity_id=?",
                       (key, key, surface, clash, eid))
        self.c.execute("INSERT OR IGNORE INTO entity_names(entity_id, name, at, revision_id) VALUES (?,?,?,?)",
                       (eid, key, self.at, self.rid))
        self.c.execute("DELETE FROM entity_aliases WHERE entity_id=? AND alias_fold=?", (eid, ids.fold(surface)))
        self._alias(eid, previous)
        self.dirty_entities.add(eid)

    def op_entity_summary(self, p: Dict[str, Any]) -> None:
        """A summary dated at the revision's ``at`` (a note's date), or at ``as_of`` when the op gives one (a
        consolidation summary re-derived from the dated history is dated at the latest knowledge it was derived
        from). The entity's current summary is then the last one in :data:`SUMMARY_ORDER` (ADR 0008 §2)."""
        eid = p["entity_id"]
        row = self._require_entity(eid)
        at = ids.normalize_time(p["as_of"], field="as_of") if p.get("as_of") else self.at
        self.c.execute("INSERT OR IGNORE INTO entity_summaries(entity_id, revision_id, at, note_id, summary, previous)"
                       " VALUES (?,?,?,?,?,?)", (eid, self.rid, at, self.note_id, p["summary"], row["summary"]))
        latest = self.s.current_summary_row(eid, conn=self.c)
        self.c.execute("UPDATE entities SET summary=? WHERE entity_id=?",
                       (latest["summary"] if latest else p["summary"], eid))
        self.dirty_entities.add(eid)

    def op_entity_merge(self, p: Dict[str, Any]) -> None:
        from_id, into_id = p["from_id"], p["into_id"]
        self._require_entity(from_id)
        self._require_entity(into_id)
        if from_id == into_id or self.s.canonical(into_id, self.c) == from_id:
            raise ValueError(f"Merging {from_id} into {into_id} would make a cycle.")
        self.c.execute("UPDATE entities SET merged_into=?, merged_at=? WHERE entity_id=?", (into_id, self.at, from_id))
        self.c.execute("INSERT OR IGNORE INTO merges(from_id, into_id, at, revision_id, reason, assertion_id)"
                       " VALUES (?,?,?,?,?,?)", (from_id, into_id, self.at, self.rid, p.get("reason"),
                                                 ids.kg_assertion_id(from_id, "schema:sameAs", into_id)))
        into = self.c.execute("SELECT name_key, key_clash_with FROM entities WHERE entity_id=?", (into_id,)).fetchone()
        if into["key_clash_with"] and self._key_holder(into["name_key"], into_id) is None:
            self.c.execute("UPDATE entities SET key_clash_with=NULL WHERE entity_id=?", (into_id,))
        self.stats["entities_merged"].append({"from_id": from_id, "into_id": into_id})
        self.adr8["paired"].append((from_id, into_id, "joined"))
        self.dirty_relation_entities.update({from_id, into_id})
        self.dirty_entities.update({from_id, into_id})

    def op_entity_unmerge(self, p: Dict[str, Any]) -> None:
        into_id, from_id = p["entity_id"], p["from_id"]
        row = self._require_entity(from_id)
        if row["merged_into"] != into_id:
            raise ValueError(f"{from_id} is not merged into {into_id}.")
        self.c.execute("UPDATE entities SET merged_into=NULL, merged_at=NULL WHERE entity_id=?", (from_id,))
        self.c.execute("UPDATE merges SET undone_at=?, undone_revision_id=? WHERE from_id=? AND into_id=? AND"
                       " undone_at IS NULL", (self.at, self.rid, from_id, into_id))
        self._separate_name(from_id, row)
        self.adr8["paired"].append((from_id, into_id, "separated"))
        self.dirty_relation_entities.update({from_id, into_id})
        self.dirty_entities.update({from_id, into_id})

    def _separate_name(self, eid: str, row: sqlite3.Row) -> None:
        """An entity separated again whose canonical name another live entity holds gets the first free
        ``<name>_2``, ``<name>_3`` ...: two entities a person sees never share a canonical name."""
        key = row["name_key"] or ids.canonical_name(row["display_name"] or row["name"])
        if self._key_holder(key, eid) is None:
            if row["key_clash_with"]:
                self.c.execute("UPDATE entities SET key_clash_with=NULL WHERE entity_id=?", (eid,))
            return
        n = 2
        while self.c.execute("SELECT 1 FROM entities WHERE name_key=? AND entity_id!=?", (f"{key}_{n}", eid)).fetchone():
            n += 1
        new = f"{key}_{n}"
        self.c.execute("UPDATE entities SET name=?, name_key=?, key_clash_with=NULL WHERE entity_id=?", (new, new, eid))
        self.c.execute("INSERT OR IGNORE INTO entity_names(entity_id, name, at, revision_id) VALUES (?,?,?,?)",
                       (eid, new, self.at, self.rid))

    def op_entity_mention(self, p: Dict[str, Any]) -> None:
        eid = p["entity_id"]
        row = self._require_entity(eid)
        if row["retired_revision_id"]:  # ADR 0008: a retired entity a note mentions again comes back
            self.c.execute("UPDATE entities SET retired_revision_id=NULL, retired_at=NULL WHERE entity_id=?", (eid,))
            self.adr8["retired"].discard(eid)
        note_id = p.get("note_id") or self.note_id
        at = self.at
        if note_id:
            nrow = self.c.execute("SELECT occurred_at FROM notes WHERE note_id=?", (note_id,)).fetchone()
            if nrow:
                at = nrow[0]
        if row["last_seen"] and eid not in self.stats["entities_created"] and \
                ids.parse_time(row["last_seen"]) - ids.parse_time(at) > RETRO_MIN_GAP:
            # A note older than what is already known about the entity: a retrospective insertion (its summary is
            # marked stale, its then-versus-now card dropped). Live notes a few minutes out of order are not.
            self.retro.add(eid)
        self.c.execute("INSERT OR IGNORE INTO entity_notes(entity_id, note_id, at, revision_id) VALUES (?,?,?,?)",
                       (eid, note_id, at, self.rid))
        self.dirty_entities.add(eid)

    def _require_entity(self, eid: str) -> sqlite3.Row:
        row = self.c.execute("SELECT * FROM entities WHERE entity_id=?", (eid,)).fetchone()
        if row is None:
            raise ValueError(f"The entity {eid} does not exist in this notebook.")
        return row

    # --- claims
    def op_claim_add(self, p: Dict[str, Any]) -> None:
        cid = p["claim_id"]
        if self.c.execute("SELECT 1 FROM claims WHERE claim_id=?", (cid,)).fetchone():
            return
        self._require_entity(p["subject_id"])
        if p.get("object_id"):
            self._require_entity(p["object_id"])
        subject_name = self.c.execute("SELECT name FROM entities WHERE entity_id=?", (p["subject_id"],)).fetchone()[0]
        disputed = [p["disputes"]] if p.get("disputes") else []
        seq = self.c.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM claims").fetchone()[0]
        self.c.execute(
            "INSERT INTO claims(claim_id, subject_id, predicate, object_id, object_literal, certainty, stance,"
            " observed_at, valid_from, valid_until, status, evidence, note_id, revision_id, recorded_at, assertion_id,"
            " vector_key, disputed_with_json, seq) VALUES (?,?,?,?,?,?,?,?,?,?,'active',?,?,?,?,?,?,?,?)",
            (cid, p["subject_id"], p["predicate"], p.get("object_id"), p.get("object_literal"), p["certainty"],
             p.get("stance") or "assert", p["observed_at"], p.get("valid_from"), p.get("valid_until"),
             p.get("evidence"), p.get("note_id") or self.note_id, self.rid, self.recorded_at, cid,
             self.keys.claim(p, self.c), _j(disputed), seq))
        if p.get("disputes"):
            other = self.c.execute("SELECT disputed_with_json FROM claims WHERE claim_id=?", (p["disputes"],)).fetchone()
            if other is not None:
                lst = _uj(other[0], [])
                if cid not in lst:
                    lst.append(cid)
                self.c.execute("UPDATE claims SET disputed_with_json=? WHERE claim_id=?", (_j(lst), p["disputes"]))
        obj_text = p.get("object_literal") or ""
        if p.get("object_id"):
            obj_text = self.c.execute("SELECT name FROM entities WHERE entity_id=?", (p["object_id"],)).fetchone()[0]
        self.c.execute("INSERT INTO claims_fts(claim_id, text) VALUES (?,?)",
                       (cid, NoteStore.claim_text(subject_name, p["predicate"], obj_text, p.get("evidence"))))
        self.stats["claims_added"].append(cid)
        self.stats["touched_claims"].append(cid)
        self.dirty_entities.add(p["subject_id"])
        if p.get("object_id"):
            self.dirty_entities.add(p["object_id"])
            self.dirty_relations.add((p["subject_id"], p["object_id"], p["predicate"]))

    def _claim(self, cid: str) -> sqlite3.Row:
        row = self.c.execute("SELECT * FROM claims WHERE claim_id=?", (cid,)).fetchone()
        if row is None:
            raise ValueError(f"The claim {cid} does not exist in this notebook.")
        return row

    def _dirty_claim_relation(self, row: sqlite3.Row) -> None:
        self.dirty_entities.add(row["subject_id"])  # its history changed (then-versus-now cache key)
        if row["object_id"]:
            self.dirty_entities.add(row["object_id"])
            self.dirty_relations.add((row["subject_id"], row["object_id"], row["predicate"]))

    @staticmethod
    def window_end(row: Any, until: Optional[str]) -> Optional[str]:
        """``until`` never before the claim's ``valid_from`` (none: no known start, nothing to clamp): a window
        closed before it opened is an empty window, never a negative one."""
        start = row["valid_from"]
        return start if (until and start and until < start) else until

    def op_claim_supersede(self, p: Dict[str, Any]) -> None:
        row = self._claim(p["claim_id"])
        cur = self.c.execute(
            "UPDATE claims SET status='superseded', superseded_by=?, valid_until=COALESCE(valid_until, ?),"
            " closed_reason=?, closed_revision_id=? WHERE claim_id=? AND status='active'",
            (p.get("by_claim_id"), self.window_end(row, p["valid_until"]), p.get("reason"), self.rid, p["claim_id"]))
        if cur.rowcount:
            self.stats["claims_superseded"].append(p["claim_id"])
        elif row["status"] != "active" and row["closed_revision_id"] != self.rid:
            self.adr8["stale"].append(p["claim_id"])  # closed since the note was read (ADR 0008 §5, review 4.1)
        self._dirty_claim_relation(row)

    def op_claim_insert_before(self, p: Dict[str, Any]) -> None:
        """A statement from a note OLDER than the belief it gives way to (docs/data-model.md "Time"): the claim
        just added is the earlier belief, closed at the later one's date and superseded by it; the later belief
        is untouched. ``after_claim_id`` is the belief before it, whose window now ends where this one starts."""
        row = self._claim(p["claim_id"])
        self._claim(p["before_claim_id"])
        end = self.window_end(row, p["valid_until"])
        cur = self.c.execute(
            "UPDATE claims SET status='superseded', superseded_by=?, valid_until=CASE WHEN valid_until IS NULL OR"
            " valid_until>? THEN ? ELSE valid_until END, closed_reason=?, closed_revision_id=? WHERE claim_id=? AND"
            " status='active'", (p["before_claim_id"], end, end, p.get("reason"), self.rid, p["claim_id"]))
        if cur.rowcount:
            self.stats["claims_inserted_before"].append(p["claim_id"])
        self._dirty_claim_relation(row)
        self.retro.update(x for x in (row["subject_id"], row["object_id"]) if x)
        if p.get("after_claim_id"):
            prev = self._claim(p["after_claim_id"])
            end = self.window_end(prev, p.get("after_valid_until") or row["valid_from"] or row["observed_at"])
            cur = self.c.execute(
                "UPDATE claims SET status=CASE WHEN status='active' THEN 'superseded' ELSE status END,"
                " superseded_by=?, valid_until=?, closed_reason=COALESCE(?, closed_reason), closed_revision_id=?"
                " WHERE claim_id=? AND status NOT IN ('retracted', 'reinterpreted') AND (valid_until IS NULL OR"
                " valid_until>?)",
                (p["claim_id"], end, p.get("reason"), self.rid, p["after_claim_id"], end))
            if cur.rowcount:
                self.stats["claims_superseded"].append(p["after_claim_id"])
            self._dirty_claim_relation(prev)

    def op_claim_start_earlier(self, p: Dict[str, Any]) -> None:
        """An earlier note shows a belief already held on its date (ADR 0008 §2, R1): the claim's ``observed_at``
        and ``valid_from`` move back to that date, never later (a claim without a known start keeps none); the
        belief before it in its history, whose window covered that date, now ends there (``after_claim_id``).
        The relation's ``first_seen`` follows (``refresh_relation``: the earliest ``observed_at``)."""
        row = self._claim(p["claim_id"])
        at = p["observed_at"]
        cur = self.c.execute(
            "UPDATE claims SET observed_at=CASE WHEN observed_at>? THEN ? ELSE observed_at END, valid_from=CASE WHEN"
            " valid_from IS NOT NULL AND valid_from>? THEN ? ELSE valid_from END WHERE claim_id=? AND"
            " status!='retracted'", (at, at, p.get("valid_from") or at, p.get("valid_from") or at, p["claim_id"]))
        if cur.rowcount:
            self.stats["claims_started_earlier"].append(p["claim_id"])
        self._dirty_claim_relation(row)
        if ids.parse_time(row["observed_at"]) - ids.parse_time(at) > RETRO_MIN_GAP:
            # a retrospective insertion (live notes minutes apart are not, as for mentions)
            self.retro.update(x for x in (row["subject_id"], row["object_id"]) if x)
        if p.get("after_claim_id") and p.get("after_valid_until"):
            prev = self._claim(p["after_claim_id"])
            end = self.window_end(prev, p["after_valid_until"])
            cur = self.c.execute(
                "UPDATE claims SET status=CASE WHEN status='active' THEN 'superseded' ELSE status END,"
                " superseded_by=COALESCE(superseded_by, ?), valid_until=?, closed_reason=COALESCE(?, closed_reason),"
                " closed_revision_id=? WHERE claim_id=? AND status!='retracted' AND (valid_until IS NULL OR"
                " valid_until>?)", (p["claim_id"], end, p.get("reason"), self.rid, p["after_claim_id"], end))
            if cur.rowcount:
                self.stats["claims_superseded"].append(p["after_claim_id"])
            self._dirty_claim_relation(prev)

    def op_claim_start_restore(self, p: Dict[str, Any]) -> None:
        """The inverse of ``claim.start_earlier`` (ADR 0008, data-model.md "Re-understanding"): the reading whose
        earlier evidence moved the belief's start back was withdrawn; ``observed_at`` and ``valid_from`` return to
        the start replayed without it (``{observed_at, valid_from}``, from the claim's own add and the moves that
        remain). The belief it had narrowed is re-opened by the same revision's ``claim.reopen``."""
        row = self._claim(p["claim_id"])
        cur = self.c.execute("UPDATE claims SET observed_at=?, valid_from=? WHERE claim_id=? AND (observed_at IS NOT ?"
                             " OR valid_from IS NOT ?)", (p["observed_at"], p.get("valid_from"), p["claim_id"],
                                                          p["observed_at"], p.get("valid_from")))
        if cur.rowcount:
            self.stats.setdefault("claims_start_restored", []).append(p["claim_id"])
        self._dirty_claim_relation(row)

    def op_claim_retract(self, p: Dict[str, Any]) -> None:
        """Close a statement as a retraction (``{claim_id, reason, valid_until?, withdrawn?}``). ``withdrawn`` (W28: the
        note that made it was archived): the window empties at the statement's own start even when another revision
        had closed it later, so it is no belief at any date (the archive as received before still shows it)."""
        row = self._claim(p["claim_id"])
        end = self.window_end(row, p.get("valid_until") or self.at)
        keep = ("CASE WHEN valid_until IS NULL OR valid_until>? THEN ? ELSE valid_until END" if p.get("withdrawn")
                else "COALESCE(valid_until, ?)")
        cur = self.c.execute(
            f"UPDATE claims SET status='retracted', valid_until={keep}, closed_reason=?,"
            " closed_revision_id=? WHERE claim_id=? AND status NOT IN ('retracted', 'reinterpreted')",
            (*((end, end) if p.get("withdrawn") else (end,)), p.get("reason"), self.rid, p["claim_id"]))
        if cur.rowcount:
            self.stats["claims_retracted"].append(p["claim_id"])
            self.adr8["retracted"].append(row)
        elif row["status"] == REINTERPRETED:
            self.adr8["stale"].append(p["claim_id"])
        self._dirty_claim_relation(row)

    def op_claim_reinforce(self, p: Dict[str, Any]) -> None:
        row = self._claim(p["claim_id"])
        note_id = p.get("note_id") or self.note_id
        at = p.get("at") or self.at  # W28: a reinforcement an unarchive restores keeps its date
        cur = self.c.execute("INSERT OR IGNORE INTO claim_reinforcements(claim_id, note_id, revision_id, at)"
                             " VALUES (?,?,?,?)", (p["claim_id"], note_id, self.rid, at))
        if cur.rowcount:
            self.c.execute("UPDATE claims SET reinforced_count=reinforced_count+1 WHERE claim_id=?", (p["claim_id"],))
            self.stats["claims_reinforced"].append(p["claim_id"])
        self._dirty_claim_relation(row)

    # --- topics, tasks, notes
    def op_topic_assign(self, p: Dict[str, Any]) -> None:
        note_id = p.get("note_id") or self.note_id
        if not self.rev.get("action"):  # processing keeps the first TOPICS_PER_NOTE topics the model lists
            have = {r[0] for r in self.c.execute("SELECT topic FROM note_topics WHERE note_id=? AND"
                                                 " assigned_by='revision'", (note_id,)).fetchall()}
            if len(have) >= TOPICS_PER_NOTE and ids.canonical_name(p["topic"]) not in have:
                # a selection (the model lists the main ones first), recorded (ADR 0007): the result's
                # topics_not_assigned, the revision's warnings and its summary say which were not kept
                self.stats.setdefault("topics_not_assigned", []).append(p["topic"])
                return
        self.s.assign_topic(self.c, note_id, p["topic"], by="revision", revision_id=self.rid, at=self.at)

    def op_topic_unassign(self, p: Dict[str, Any]) -> None:
        """Take a note out of a topic as it is stored (``abstractnote normalize-topics``)."""
        note_id = p.get("note_id") or self.note_id
        self.c.execute("DELETE FROM note_topics WHERE note_id=? AND topic=?", (note_id, p["topic"]))
        self.s.refresh_topic(self.c, p["topic"])

    def op_task_open(self, p: Dict[str, Any]) -> None:
        cur = self.c.execute(
            "INSERT OR IGNORE INTO tasks(task_id, note_id, text, status, due, opened_at, opened_revision_id)"
            " VALUES (?,?,?,'open',?,?,?)",
            (p["task_id"], p.get("note_id") or self.note_id, p["text"], p.get("due"), self.at, self.rid))
        if cur.rowcount:
            self.stats["tasks_opened"].append(p["task_id"])

    def _close_task(self, p: Dict[str, Any], status: str) -> None:
        row = self.c.execute("SELECT status FROM tasks WHERE task_id=?", (p["task_id"],)).fetchone()
        if row is None:
            raise ValueError(f"The task {p['task_id']} does not exist in this notebook.")
        cur = self.c.execute(
            "UPDATE tasks SET status=?, closed_at=?, closed_by_note_id=?, closed_revision_id=?, recorded_closed_at=?"
            " WHERE task_id=? AND status='open'",
            (status, self.at, p.get("by_note_id"), self.rid, self.recorded_at, p["task_id"]))
        if cur.rowcount and status == "done":
            self.stats["tasks_closed"].append(p["task_id"])

    def op_task_close(self, p: Dict[str, Any]) -> None:
        self._close_task(p, "done")

    def op_task_drop(self, p: Dict[str, Any]) -> None:
        self._close_task(p, "dropped")

    def op_note_summary(self, p: Dict[str, Any]) -> None:
        note_id = p.get("note_id") or self.note_id
        sets, params = ["summary=?"], [p.get("summary")]
        if p.get("kind"):
            sets.append("kind=?")
            params.append(p["kind"])
        if p.get("language"):
            sets.append("language=?")
            params.append(p["language"])
        self.c.execute(f"UPDATE notes SET {', '.join(sets)} WHERE note_id=?", [*params, note_id])
        self.s.refresh_note_fts(self.c, note_id)

    def op_note_archive(self, p: Dict[str, Any]) -> None:
        """Archive a note (``{note_id, duplicate_of?, reason?, withdrawn?, previous?}``): it leaves lists, search and
        the queue (a waiting copy is never processed), keeps its links, and names the note it duplicates (``abstractnote
        dedupe-sources``). ``withdrawn`` (W28, every archive since): its reading is withdrawn with it: its mentions and
        topics are soft-removed (``entity_notes_removed`` / ``note_topics_removed`` with this revision, so the archive
        as received before still shows them), the summaries it wrote leave the dated history (the entities' current
        summary is selected again and marked stale), and the same revision's ``claim.retract`` / ``claim.reopen`` /
        ``reinforcement.drop`` / ``claim.start_restore`` / ``task.reopen`` / ``task.retire`` / ``entity.retire`` ops
        undo the rest (the inverse table, data-model.md "Archive"). An archive written before (no ``withdrawn``) replays
        as it did: the note leaves the lists only."""
        note_id = p["note_id"]
        if self.c.execute("SELECT 1 FROM notes WHERE note_id=?", (note_id,)).fetchone() is None:
            raise ValueError(f"The note {note_id} is not in this notebook.")
        # what its reading used, before its mentions go (the dependents marked at the end of the revision)
        used = self.s.used_by(self.c, note_id)["entity"]
        self.c.execute("UPDATE notes SET archived_at=COALESCE(archived_at, ?), status='archived',"
                       " duplicate_of=COALESCE(?, duplicate_of) WHERE note_id=?",
                       (self.recorded_at, p.get("duplicate_of"), note_id))
        if p.get("withdrawn"):
            self._withdraw_note(note_id)
        # a foundation removed marks its dependents (ADR 0008 §4); a duplicate's copy marks only the notes that
        # repeated a statement it withdraws (W28)
        self.adr8["archived"].append({"note_id": note_id, "used": used, "duplicate": bool(p.get("duplicate_of")),
                                      "withdrawn": bool(p.get("withdrawn"))})
        self.c.execute("UPDATE queue SET state='done', leased_by=NULL, lease_until=NULL, next_attempt_at=NULL"
                       " WHERE note_id=? AND state IN ('queued','failed')", (note_id,))

    def _withdraw_note(self, note_id: str) -> None:
        """The index side of an archive's withdrawal (W28): mentions and topics soft-removed, summaries re-selected."""
        for r in self.c.execute("SELECT entity_id, note_id, at, revision_id FROM entity_notes WHERE note_id=?",
                                (note_id,)).fetchall():
            self.c.execute("INSERT OR IGNORE INTO entity_notes_removed(entity_id, note_id, at, revision_id,"
                           " removed_revision_id) VALUES (?,?,?,?,?)", (*tuple(r), self.rid))
            self.dirty_entities.add(r[0])
            self.retro.add(r[0])
        self.c.execute("DELETE FROM entity_notes WHERE note_id=?", (note_id,))
        topics = [r[0] for r in self.c.execute("SELECT topic FROM note_topics WHERE note_id=?", (note_id,)).fetchall()]
        self.c.execute("INSERT OR IGNORE INTO note_topics_removed(note_id, topic, assigned_by, revision_id, at,"
                       " removed_revision_id) SELECT note_id, topic, assigned_by, revision_id, at, ? FROM note_topics"
                       " WHERE note_id=?", (self.rid, note_id))
        self.c.execute("DELETE FROM note_topics WHERE note_id=?", (note_id,))
        for t in topics:
            self.s.refresh_topic(self.c, t)
        self._reselect_summaries(note_id)

    def _reselect_summaries(self, note_id: str) -> None:
        """The entities a note's revisions summarised get their current summary selected again (the archived note's
        are out of the dated history, a restored note's are back), and are marked for a fresh summary (retro)."""
        for (eid,) in self.c.execute("SELECT DISTINCT s.entity_id FROM entity_summaries s JOIN revisions v ON"
                                     " v.revision_id=s.revision_id WHERE v.note_id=? ORDER BY s.entity_id",
                                     (note_id,)).fetchall():
            cur = self.s.current_summary_row(eid, conn=self.c)
            self.c.execute("UPDATE entities SET summary=? WHERE entity_id=?", (cur["summary"] if cur else None, eid))
            self.dirty_entities.add(eid)
            self.retro.add(eid)

    def op_note_unarchive(self, p: Dict[str, Any]) -> None:
        """Bring an archived note back (``{note_id, archive_revision_id, status, duplicate_of, requeue}``, W28): it is
        in the lists again with its earlier status; the mentions and topics the archive soft-removed come back (new
        rows of this revision, so the archive as received between the two still lacks them), its summaries return
        to the dated history, and a note that was waiting in the queue waits again. The same revision's
        ``claim.reopen`` / ``claim.reinforce`` / ``claim.start_earlier`` / ``task.restore`` / ``entity.restore`` ops
        restore what the archive withdrew."""
        note_id = p["note_id"]
        row = self.c.execute("SELECT archived_at FROM notes WHERE note_id=?", (note_id,)).fetchone()
        if row is None:
            raise ValueError(f"The note {note_id} is not in this notebook.")
        self.c.execute("UPDATE notes SET archived_at=NULL, status=?, duplicate_of=? WHERE note_id=?",
                       (p.get("status") or "processed", p.get("duplicate_of"), note_id))
        arid = p.get("archive_revision_id")
        if arid:
            for r in self.c.execute("SELECT entity_id, at FROM entity_notes_removed WHERE note_id=? AND"
                                    " removed_revision_id=?", (note_id, arid)).fetchall():
                self.c.execute("INSERT OR IGNORE INTO entity_notes(entity_id, note_id, at, revision_id) VALUES"
                               " (?,?,?,?)", (r[0], note_id, r[1], self.rid))
                self.dirty_entities.add(r[0])
                self.retro.add(r[0])
            topics = []
            for r in self.c.execute("SELECT topic, assigned_by, at FROM note_topics_removed WHERE note_id=? AND"
                                    " removed_revision_id=?", (note_id, arid)).fetchall():
                self.c.execute("INSERT OR IGNORE INTO note_topics(note_id, topic, assigned_by, revision_id, at) VALUES"
                               " (?,?,?,?,?)", (note_id, r[0], r[1], self.rid, r[2]))
                topics.append(r[0])
            for t in topics:
                self.s.refresh_topic(self.c, t)
            self._reselect_summaries(note_id)
        if p.get("requeue"):
            self.c.execute("UPDATE queue SET state='queued', next_attempt_at=NULL WHERE note_id=? AND state='done'",
                           (note_id,))
        self.adr8.setdefault("unarchived", []).append(note_id)
        self.stats.setdefault("notes_unarchived", []).append(note_id)

    def op_task_restore(self, p: Dict[str, Any]) -> None:
        """A task the archive of a note retired or re-opened, back as it was (``{task_id, status, closed_at,
        closed_by_note_id, closed_revision_id, recorded_closed_at}``, W28 ``note.unarchive``)."""
        cur = self.c.execute("UPDATE tasks SET status=?, closed_at=?, closed_by_note_id=?, closed_revision_id=?,"
                             " recorded_closed_at=?, removed_revision_id=NULL WHERE task_id=?",
                             (p["status"], p.get("closed_at"), p.get("closed_by_note_id"), p.get("closed_revision_id"),
                              p.get("recorded_closed_at"), p["task_id"]))
        if cur.rowcount:
            self.stats.setdefault("tasks_restored", []).append(p["task_id"])

    def op_entity_restore(self, p: Dict[str, Any]) -> None:
        """An entity the archive of a note retired (``{entity_id, retired_revision_id}``), back in the lists (W28
        ``note.unarchive``); only while that archive's retirement stands."""
        eid = p["entity_id"]
        self._require_entity(eid)
        cur = self.c.execute("UPDATE entities SET retired_revision_id=NULL, retired_at=NULL WHERE entity_id=? AND"
                             " retired_revision_id=?", (eid, p["retired_revision_id"]))
        if cur.rowcount:
            self.adr8["retired"].discard(eid)
            self.stats.setdefault("entities_restored", []).append(eid)
        self.dirty_entities.add(eid)

    def op_note_redate(self, p: Dict[str, Any]) -> None:
        """Move a note in history time (``{note_id, from, to, basis, quote?, reason?}``): its ``occurred_at``, the
        dates of its mentions, its topics' ranges, its queue place, and the statements and tasks it wrote that
        carried the old date. ``source.redated`` records the basis (``content`` or ``user``). ``keep_statements``
        (a person's re-date of an understood note, ingest/dates.py ``redate_note``): its statements and their
        reinforcements stay where its interpretation placed them, in this index as in AbstractMemory, until the
        note is understood again at its new date (ADR 0008 §4, §7)."""
        note_id = p.get("note_id") or self.note_id
        row = self.c.execute("SELECT occurred_at, source_json FROM notes WHERE note_id=?", (note_id,)).fetchone()
        if row is None:
            raise ValueError(f"The note {note_id} is not in this notebook.")
        old, new = p["from"], p["to"]
        self.adr8["redated"].append((note_id, old, new))
        src = _uj(row["source_json"], {}) or {}
        src["redated"] = {"basis": p["basis"], "from": old, "to": new, "revision_id": self.rid}
        self.c.execute("UPDATE notes SET occurred_at=?, source_json=? WHERE note_id=?", (new, _j(src), note_id))
        self.c.execute("UPDATE queue SET occurred_at=? WHERE note_id=?", (new, note_id))
        for (eid,) in self.c.execute("SELECT entity_id FROM entity_notes WHERE note_id=?", (note_id,)).fetchall():
            self.dirty_entities.add(eid)
            self.retro.add(eid)
        self.c.execute("UPDATE entity_notes SET at=? WHERE note_id=?", (new, note_id))
        moved = [] if p.get("keep_statements") else self.c.execute(
            "SELECT * FROM claims WHERE note_id=? AND observed_at=?", (note_id, old)).fetchall()
        for cr in moved:
            self.c.execute("UPDATE claims SET observed_at=?, valid_from=CASE WHEN valid_from=? THEN ? ELSE valid_from"
                           " END WHERE claim_id=?", (new, old, new, cr["claim_id"]))
            self._dirty_claim_relation(cr)
        if not p.get("keep_statements"):
            self.c.execute("UPDATE claim_reinforcements SET at=? WHERE note_id=? AND at=?", (new, note_id, old))
        self.c.execute("UPDATE tasks SET opened_at=? WHERE note_id=? AND opened_at=?", (new, note_id, old))
        for (topic,) in self.c.execute("SELECT topic FROM note_topics WHERE note_id=?", (note_id,)).fetchall():
            self.s.refresh_topic(self.c, topic)

    # --- end of revision
    def _record_topics_not_assigned(self) -> None:
        """Say which of the model's topics the note did not keep (TOPICS_PER_NOTE): a warning and a sentence added
        to the revision once (a rebuild replays the same ops and finds them there)."""
        topics = list(dict.fromkeys(self.stats.get("topics_not_assigned") or []))
        if not topics:
            return
        self.stats["topics_not_assigned"] = topics
        names = ", ".join(topics)
        warning = (f"topics_not_assigned: a note keeps the first {TOPICS_PER_NOTE} topics from processing; not"
                   f" assigned: {names}.")
        sentence = f"Topics not kept (a note keeps {TOPICS_PER_NOTE}): {names}."
        row = self.c.execute("SELECT summary, warnings_json FROM revisions WHERE revision_id=?", (self.rid,)).fetchone()
        if row is None:
            return
        warnings = list(_uj(row["warnings_json"], []) or [])
        summary = str(row["summary"] or "")
        if warning not in warnings:
            warnings.append(warning)
        if sentence not in summary:
            summary = f"{summary.rstrip()} {sentence}".strip()
        self.c.execute("UPDATE revisions SET summary=?, warnings_json=? WHERE revision_id=?",
                       (summary, json.dumps(warnings), self.rid))
        _limits_log.warning("revision %s, note %s: %s", self.rid, self.note_id, warning)

    def finish(self) -> None:
        # a re-interpretation takes its note's earlier summaries out of the dated history (current re-selected)
        self.dirty_entities.update(self.s.refresh_superseded_summaries(self.c, self.rid, self.note_id))
        if self.dirty_relation_entities:
            self.s.refresh_relations_for(self.c, self.dirty_relation_entities)
        for s, o, p in sorted(self.dirty_relations):
            self.s.refresh_relation(self.c, self.s.canonical(s, self.c), self.s.canonical(o, self.c), p)
        stat_targets = set()
        for eid in self.dirty_entities:
            stat_targets.add(eid)
            stat_targets.add(self.s.canonical(eid, self.c))
        for eid in sorted(stat_targets):
            self.s.refresh_entity_stats(self.c, eid)
            row = self.c.execute("SELECT name, display_name, summary FROM entities WHERE entity_id=?", (eid,)).fetchone()
            aliases = [a[0] for a in self.c.execute(
                "SELECT alias FROM entity_aliases WHERE entity_id=? ORDER BY added_at, alias", (eid,))]
            # The embedded text keeps the surface form the notes wrote (a canonical snake_case name reads worse).
            key = self.keys.entity(entity_text(row["display_name"] or row["name"], aliases, row["summary"]))
            self.c.execute("UPDATE entities SET vector_key=?, revision_count=revision_count+1 WHERE entity_id=?",
                           (key, eid))
            self.s.refresh_entity_fts(self.c, eid)
        self.stats["touched_entities"] = sorted(stat_targets)
        self._record_topics_not_assigned()
        if self.retro:
            retro = sorted({self.s.canonical(e, self.c) for e in self.retro})
            self.stats["retro_entities"] = retro
            self.s.mark_retro(self.c, retro, self.rid, self.note_id, self.recorded_at)
        if self.note_id and not self.rev.get("action"):
            self.c.execute("UPDATE notes SET status=CASE WHEN status='archived' THEN status ELSE 'processed' END,"
                           " processed_at=? WHERE note_id=?", (self.recorded_at, self.note_id))
            self.c.execute("UPDATE queue SET state='done', done_at=?, leased_by=NULL, lease_until=NULL,"
                           " last_error=NULL WHERE note_id=?", (self.recorded_at, self.note_id))
        self.adr8_finish()

    # --- ADR 0008: a note understood again (``note.reinterpret``; the inverse table is in docs/data-model.md)
    def op_note_reinterpret(self, p: Dict[str, Any]) -> None:
        """The first op of a re-understanding: the previous interpretation ``supersedes`` is superseded. Its
        mentions and the topics it assigned are soft-removed (moved to ``entity_notes_removed`` /
        ``note_topics_removed`` with this revision, so the archive as received before still shows them; the new
        reading's ops add the mentions and topics again), its dependency record is dropped (recomputed at the end)."""
        note_id = p["note_id"]
        if self.c.execute("SELECT 1 FROM notes WHERE note_id=?", (note_id,)).fetchone() is None:
            raise ValueError(f"The note {note_id} is not in this notebook.")
        prev = p["supersedes"]
        if self.c.execute("SELECT 1 FROM revisions WHERE revision_id=? AND note_id=? AND applied_index=1",
                          (prev, note_id)).fetchone() is None:
            raise ValueError(f"The revision {prev} is not an applied interpretation of {note_id}.")
        for r in self.c.execute("SELECT entity_id, note_id, at, revision_id FROM entity_notes WHERE note_id=?",
                                (note_id,)).fetchall():
            self.c.execute("INSERT OR IGNORE INTO entity_notes_removed(entity_id, note_id, at, revision_id,"
                           " removed_revision_id) VALUES (?,?,?,?,?)", (*tuple(r), self.rid))
            self.dirty_entities.add(r[0])
        self.c.execute("DELETE FROM entity_notes WHERE note_id=?", (note_id,))
        topics = [r[0] for r in self.c.execute("SELECT topic FROM note_topics WHERE note_id=? AND assigned_by='revision'",
                                               (note_id,)).fetchall()]
        self.c.execute("INSERT OR IGNORE INTO note_topics_removed(note_id, topic, assigned_by, revision_id, at,"
                       " removed_revision_id) SELECT note_id, topic, assigned_by, revision_id, at, ? FROM note_topics"
                       " WHERE note_id=? AND assigned_by='revision'", (self.rid, note_id))
        self.c.execute("DELETE FROM note_topics WHERE note_id=? AND assigned_by='revision'", (note_id,))
        for t in topics:
            self.s.refresh_topic(self.c, t)
        self.c.execute("DELETE FROM note_dependencies WHERE note_id=?", (note_id,))
        self.c.execute("INSERT OR REPLACE INTO revision_supersessions(revision_id, superseded_by, note_id, recorded_at)"
                       " VALUES (?,?,?,?)", (prev, self.rid, note_id, self.recorded_at))
        self.stats["reinterpreted"] = {"note_id": note_id, "supersedes": prev}

    def op_claim_reinterpret(self, p: Dict[str, Any]) -> None:
        """A statement of the superseded reading the new one does not make: closed with the kind ``reinterpreted``
        (an empty window: never a belief in the current reading; the archive as received before shows it)."""
        row = self._claim(p["claim_id"])
        end = row["valid_from"] or row["observed_at"]
        cur = self.c.execute(
            "UPDATE claims SET status=?, valid_until=CASE WHEN valid_until IS NULL OR valid_until>? THEN ? ELSE"
            " valid_until END, closed_reason=?, closed_revision_id=? WHERE claim_id=? AND status!=?",
            (REINTERPRETED, end, end, p.get("reason") or "read again", self.rid, p["claim_id"], REINTERPRETED))
        if cur.rowcount:
            self.stats.setdefault("claims_reinterpreted", []).append(p["claim_id"])
        self._dirty_claim_relation(row)

    def op_claim_revise(self, p: Dict[str, Any]) -> None:
        """The same statement (subject, predicate, object) read again with another certainty, stance, window or
        evidence: updated in place (``previous`` keeps the earlier values; review 1.2: never dropped, never a second
        copy). A window another revision closed stays closed. ``assertion_id`` names its new AbstractMemory copy."""
        row = self._claim(p["claim_id"])
        self.c.execute(
            "UPDATE claims SET certainty=?, stance=?, valid_from=?, evidence=?, assertion_id=?, valid_until=CASE WHEN"
            " closed_revision_id IS NULL THEN ? ELSE valid_until END WHERE claim_id=?",
            (p["certainty"], p.get("stance") or "assert", p.get("valid_from"), p.get("evidence"), p["assertion_id"],
             p.get("valid_until"), p["claim_id"]))
        obj_text = row["object_literal"] or ""
        if row["object_id"]:
            obj_text = self.c.execute("SELECT name FROM entities WHERE entity_id=?", (row["object_id"],)).fetchone()[0]
        subject_name = self.c.execute("SELECT name FROM entities WHERE entity_id=?", (row["subject_id"],)).fetchone()[0]
        self.c.execute("DELETE FROM claims_fts WHERE claim_id=?", (p["claim_id"],))
        self.c.execute("INSERT INTO claims_fts(claim_id, text) VALUES (?,?)", (p["claim_id"], NoteStore.claim_text(
            subject_name, row["predicate"], obj_text, p.get("evidence"))))
        self.stats.setdefault("claims_revised", []).append(p["claim_id"])
        self._dirty_claim_relation(row)

    def op_claim_reopen(self, p: Dict[str, Any]) -> None:
        """A statement of another note that the superseded reading had closed (superseded, retracted or narrowed)
        and the new reading leaves alone: restored to the state the other revisions give it (``status``,
        ``valid_until``, ``superseded_by``, ``closed_reason``, ``closed_revision_id``). ``assertion_id`` names its
        re-opened AbstractMemory copy (a window cannot widen there)."""
        row = self._claim(p["claim_id"])
        self.c.execute("UPDATE claims SET status=?, valid_until=?, superseded_by=?, closed_reason=?,"
                       " closed_revision_id=?, assertion_id=? WHERE claim_id=?",
                       (p["status"], p.get("valid_until"), p.get("superseded_by"), p.get("closed_reason"),
                        p.get("closed_revision_id"), p["assertion_id"], p["claim_id"]))
        self.stats.setdefault("claims_reopened", []).append(p["claim_id"])
        self._dirty_claim_relation(row)

    def op_claim_repoint(self, p: Dict[str, Any]) -> None:
        """A statement the superseded reading closed and the new reading closes on the same slot: it stays closed,
        now superseded by the new reading's statement."""
        row = self._claim(p["claim_id"])
        self.c.execute("UPDATE claims SET superseded_by=? WHERE claim_id=?", (p["superseded_by"], p["claim_id"]))
        self._dirty_claim_relation(row)

    def op_reinforcement_drop(self, p: Dict[str, Any]) -> None:
        """A reinforcement the superseded reading made (``revision_id``) that the new reading does not repeat."""
        row = self._claim(p["claim_id"])
        cur = self.c.execute("DELETE FROM claim_reinforcements WHERE claim_id=? AND revision_id=?",
                             (p["claim_id"], p["revision_id"]))
        if cur.rowcount:
            self.c.execute("UPDATE claims SET reinforced_count=MAX(reinforced_count-1, 0) WHERE claim_id=?",
                           (p["claim_id"],))
            self.stats.setdefault("reinforcements_dropped", []).append(p["claim_id"])
        self._dirty_claim_relation(row)

    def op_task_reopen(self, p: Dict[str, Any]) -> None:
        """A task the superseded reading closed (``closed_revision_id``) that the new reading does not close."""
        cur = self.c.execute("UPDATE tasks SET status='open', closed_at=NULL, closed_by_note_id=NULL,"
                             " closed_revision_id=NULL, recorded_closed_at=NULL WHERE task_id=? AND closed_revision_id=?",
                             (p["task_id"], p["closed_revision_id"]))
        if cur.rowcount:
            self.stats.setdefault("tasks_reopened", []).append(p["task_id"])

    def op_task_retire(self, p: Dict[str, Any]) -> None:
        """A task of the superseded reading that the new reading does not have: status ``retired`` (kept, with the
        revision that withdrew it)."""
        cur = self.c.execute("UPDATE tasks SET status=?, removed_revision_id=? WHERE task_id=? AND status!=?",
                             (TASK_RETIRED, self.rid, p["task_id"], TASK_RETIRED))
        if cur.rowcount:
            self.stats.setdefault("tasks_retired", []).append(p["task_id"])

    def op_entity_retire(self, p: Dict[str, Any]) -> None:
        """An entity only the superseded reading created and that nothing references any more: retired (kept for the
        archive as received before; left out of lists, the graph, search and candidates; a later mention brings it
        back)."""
        eid = p["entity_id"]
        self._require_entity(eid)
        cur = self.c.execute("UPDATE entities SET retired_revision_id=?, retired_at=? WHERE entity_id=? AND"
                             " retired_revision_id IS NULL", (self.rid, self.at, eid))
        if cur.rowcount:
            self.adr8["retired"].add(eid)
            self.stats.setdefault("entities_retired", []).append(eid)
        self.dirty_entities.add(eid)

    def adr8_finish(self) -> None:
        """End of a revision (ADR 0008): the dependency record of a note's current interpretation, the cleanup of
        retired entities, warnings for statements closed since the note was read, then the forward marks."""
        action = self.rev.get("action")
        interp = bool(self.note_id) and (not action or action == REINTERPRET_ACTION)
        if interp:
            self._record_used(self.note_id)
        if action == REINTERPRET_ACTION:
            self.c.execute("UPDATE notes SET processed_at=? WHERE note_id=?", (self.recorded_at, self.note_id))
        for eid in sorted(self.adr8["retired"]):
            self.c.execute("UPDATE entities SET vector_key=NULL WHERE entity_id=? AND retired_revision_id IS NOT NULL",
                           (eid,))
            self.c.execute("DELETE FROM entities_fts WHERE entity_id=?", (eid,))
        if self.adr8["stale"]:
            stale = list(dict.fromkeys(self.adr8["stale"]))
            self.stats["stale_context"] = stale
            row = self.c.execute("SELECT warnings_json FROM revisions WHERE revision_id=?", (self.rid,)).fetchone()
            warnings = list(_uj(row[0], []) or []) if row else []
            w = (f"stale_context: {len(stale)} statement(s) this note named are no longer believed (they changed since"
                 " the note was read); its own statements were kept as new.")
            if w not in warnings:
                warnings.append(w)
                self.c.execute("UPDATE revisions SET warnings_json=? WHERE revision_id=?", (_j(warnings), self.rid))
        if self.s.marking:
            self._mark_dependents(interp)

    def _record_used(self, note_id: str) -> None:
        """``note_dependencies`` of the note = what its current interpretation USED (review 3.1): the subjects and
        objects of its statements, the statements it related to (reinforced, closed), its tasks and the tasks it
        closed. Its mentions are ``entity_notes``."""
        c = self.c
        chain = set(self.s.interpretation_chain(note_id, c)) | {self.rid}
        superseded = {r[0] for r in c.execute("SELECT revision_id FROM revision_supersessions WHERE note_id=?",
                                              (note_id,)).fetchall()}
        current = chain - superseded
        rows: set = set()
        for r in c.execute("SELECT subject_id, object_id FROM claims WHERE note_id=? AND status!=?",
                           (note_id, REINTERPRETED)).fetchall():
            rows.update(("entity", x) for x in r if x)
        related = [r[0] for r in c.execute("SELECT claim_id FROM claim_reinforcements WHERE note_id=?", (note_id,))]
        if current:
            q = ",".join("?" for _ in current)
            related += [r[0] for r in c.execute(f"SELECT claim_id FROM claims WHERE closed_revision_id IN ({q}) AND"
                                                f" (note_id IS NULL OR note_id!=?)", [*current, note_id])]
            rows.update(("task_closed", r[0]) for r in c.execute(
                f"SELECT task_id FROM tasks WHERE closed_revision_id IN ({q}) AND status!=?", [*current, TASK_RETIRED]))
        for cid in related:
            rows.add(("claim", cid))
            r = c.execute("SELECT subject_id, object_id FROM claims WHERE claim_id=?", (cid,)).fetchone()
            if r is not None:
                rows.update(("entity", x) for x in r if x)
        rows.update(("task_opened", r[0]) for r in c.execute("SELECT task_id FROM tasks WHERE note_id=? AND status!=?",
                                                             (note_id, TASK_RETIRED)))
        c.execute("DELETE FROM note_dependencies WHERE note_id=?", (note_id,))
        c.executemany("INSERT OR IGNORE INTO note_dependencies(note_id, kind, key, revision_id) VALUES (?,?,?,?)",
                      [(note_id, k, v, self.rid) for k, v in sorted(rows)])

    def _names_in_note(self, note: Any) -> List[str]:
        """Live entities whose canonical name or an alias occurs in the note's own words (case-folded whole words:
        the exact-name rule of candidate generation, ADR 0004). An earlier note that NAMES an entity a later note
        used changes what that later note could mean, even when it decided its mention was another entity (F2)."""
        from pathlib import Path

        parts = [note["title"] or "", note["text"] or ""]
        root = Path(self.s.path).parent
        for a in self.c.execute("SELECT sha256, extracted_chars FROM attachments WHERE note_id=?", (note["note_id"],)):
            path = root / "extracted" / f"{a[0]}.txt"
            if a[1] and path.exists():
                parts.append(path.read_text(encoding="utf-8"))
        tokens = _words("\n".join(parts))
        if not tokens:
            return []
        token_set = set(tokens)
        joined = " " + " ".join(tokens) + " "
        found: List[str] = []
        forms = [(r[0], r[1]) for r in self.c.execute(
            "SELECT entity_id, name FROM entities WHERE retired_revision_id IS NULL UNION ALL SELECT entity_id,"
            " display_name FROM entities WHERE display_name IS NOT NULL AND retired_revision_id IS NULL UNION ALL"
            " SELECT entity_id, alias FROM entity_aliases").fetchall()]
        for eid, form in forms:
            ws = _words(str(form or "").replace("_", " "))
            if not ws or any(w not in token_set for w in ws):
                continue
            if len(ws) > 1 and (" " + " ".join(ws) + " ") not in joined:
                continue
            e = self.s.canonical(eid, self.c)
            if e not in found:
                found.append(e)
        return found

    def _mark_repeaters(self, marks: List[Dict[str, Any]], note_id: str, from_at: str, op: str, why: str,
                        code: str) -> None:
        """W28: the notes that repeated (reinforced) a statement of ``note_id`` this revision withdrew (``op``
        ``retract``) or restored (``reopen``) are marked, whatever their date: their reading relied on it."""
        cids = [o["payload"]["claim_id"] for o in self.rev.get("ops") or [] if o["op"] == f"claim.{op}" and
                (o.get("payload") or {}).get("withdrawn_note_id", (o.get("payload") or {}).get("note_id")) == note_id]
        for nid, eids in _repeaters(self.c, cids, note_id).items():
            marks.append({"note_id": nid, "reason": why, "by_note_id": None, "by_revision_id": self.rid,
                          "entities": eids, "from_at": from_at, "reason_code": code})

    def _mark_dependents(self, interp: bool) -> None:
        """ADR 0008 §4: mark the later notes whose current interpretation used what this revision touched. Forward
        only: a note's own revision marks only notes after it in history order (date, then received time, then id);
        a person's re-date marks from the earlier of the two dates, an archive from the note's date, a retraction
        from the statement's date, a join or separation from the earlier first mention."""
        c = self.c
        marks: List[Dict[str, Any]] = []
        names = lambda eids: _names_sentence(self.s, c, eids)  # noqa: E731
        if interp:
            note = c.execute("SELECT * FROM notes WHERE note_id=?", (self.note_id,)).fetchone()
            # A note understood for the first time is new information: it marks every note on or after its date
            # (same date included, review 3.3). A re-understanding marks only the notes after it in history order
            # (date, then received time, then id), so a pass is one forward sweep and terminates (ADR 0008 §4).
            first = self.rev.get("action") != REINTERPRET_ACTION
            key = None if first else NoteStore.note_order_key(note)
            frm = note["occurred_at"] if first else None
            where, params = NoteStore._after_sql(key, frm)
            later = c.execute(f"SELECT 1 FROM notes n WHERE n.processed_at IS NOT NULL AND n.archived_at IS NULL AND"
                              f" n.note_id!=? AND {where} LIMIT 1", [self.note_id, *params]).fetchone()
            if later is not None:
                touched = {self.s.canonical(e, c) for e in self.dirty_entities if e}
                touched |= set(self._names_in_note(note)) | self.adr8["retired"]
                opened = [op["payload"].get("text") for op in self.rev.get("ops") or [] if op["op"] == "task.open"]
                day = note["occurred_at"][:10]
                for nid, d in self.s.later_dependents(c, touched, after=key, from_at=frm, exclude=[self.note_id],
                                                      opened_task_texts=[t for t in opened if t]).items():
                    why = (f"An earlier note ({day}) says more about {names(d['entities'])}." if d["entities"] else
                           f"An earlier note ({day}) opened a task like one this note closed.")
                    marks.append({"note_id": nid, "reason": why, "by_note_id": self.note_id, "by_revision_id": self.rid,
                                  "entities": d["entities"], "from_at": note["occurred_at"]})
        action = self.rev.get("action")
        if action in MARKING_ACTIONS or self.adr8["archived"]:
            def forward(from_at: str, eids: Iterable[str], why: str, exclude: Sequence[str] = (),
                        code: str = DIRECT) -> None:
                for nid, d in self.s.later_dependents(c, eids, from_at=from_at, exclude=exclude).items():
                    marks.append({"note_id": nid, "reason": why, "by_note_id": None, "by_revision_id": self.rid,
                                  "entities": d["entities"], "from_at": from_at, "reason_code": code})

            for note_id, old, new in self.adr8["redated"]:
                used = self.s.used_by(c, note_id)["entity"]
                forward(min(old, new), used, f"A note it builds on was moved to {new[:10]}.", exclude=[note_id])
                if c.execute("SELECT processed_at FROM notes WHERE note_id=? AND archived_at IS NULL",
                             (note_id,)).fetchone() not in (None, (None,)):
                    marks.append({"note_id": note_id, "reason": f"Its date was set to {new[:10]}.", "by_note_id": None,
                                  "by_revision_id": self.rid, "entities": used, "from_at": min(old, new)})
            for a in self.adr8["archived"]:
                n = c.execute("SELECT occurred_at FROM notes WHERE note_id=?", (a["note_id"],)).fetchone()
                why = f"A note it builds on ({n[0][:10]}) was archived."
                if not a["duplicate"]:  # a foundation removed (a duplicate's copy joins copies, not meanings)
                    forward(n[0], a["used"], why, exclude=[a["note_id"]], code=SOURCE_ARCHIVED)
                if a["withdrawn"]:  # W28: the notes that repeat a statement it withdrew, whatever their date
                    self._mark_repeaters(marks, a["note_id"], n[0], "retract", why, SOURCE_ARCHIVED)
            for note_id in self.adr8.get("unarchived") or []:
                n = c.execute("SELECT occurred_at FROM notes WHERE note_id=?", (note_id,)).fetchone()
                why = f"A note it builds on ({n[0][:10]}) was brought back from the archive."
                forward(n[0], self.s.used_by(c, note_id)["entity"], why, exclude=[note_id], code=SOURCE_UNARCHIVED)
                self._mark_repeaters(marks, note_id, n[0], "reopen", why, SOURCE_UNARCHIVED)
            for row in self.adr8["retracted"] if action == "claim.retract" else []:
                forward(row["observed_at"], [x for x in (row["subject_id"], row["object_id"]) if x],
                        "A statement it builds on was withdrawn.")
            for a, b, how in self.adr8["paired"]:
                first = [r[0] for r in c.execute("SELECT first_seen FROM entities WHERE entity_id IN (?, ?) AND first_seen"
                                                 " IS NOT NULL", (a, b))]
                if first:
                    forward(min(first), [a, b], f"Two entries it uses were {how}.")
        if not marks:
            return
        if self.adr8["archived"]:  # an archived note is never marked (its reading is withdrawn)
            gone = {a["note_id"] for a in self.adr8["archived"]}
            marks = [m for m in marks if m["note_id"] not in gone]
        new = self.s.mark_reinterpret(c, marks, self.recorded_at)
        by_entity: Dict[str, int] = {}
        for m in marks:
            if m["note_id"] in new:
                for e in m.get("entities") or []:
                    by_entity[e] = by_entity.get(e, 0) + 1
        self.stats["reinterpret_marked"] = {
            "count": len(set(new)), "notes": sorted(set(new)),
            "by_entity": [{"entity_id": e, "name": self.s._name(e), "notes": n}
                          for e, n in sorted(by_entity.items(), key=lambda kv: (-kv[1], kv[0]))[:10]]}


def _repeaters(c: sqlite3.Connection, claim_ids: Sequence[str], note_id: str) -> Dict[str, List[str]]:
    """``{note_id: [entity ids]}`` of the understood, not archived notes other than ``note_id`` that reinforced one of
    ``claim_ids`` (they repeat a statement an archive withdrew or an unarchive restored, W28)."""
    if not claim_ids:
        return {}
    q = ",".join("?" for _ in claim_ids)
    out: Dict[str, List[str]] = {}
    for nid, s, o in c.execute(
            f"SELECT DISTINCT r.note_id, cl.subject_id, cl.object_id FROM claim_reinforcements r JOIN claims cl ON"
            f" cl.claim_id=r.claim_id JOIN notes n ON n.note_id=r.note_id WHERE r.claim_id IN ({q}) AND r.note_id!=? AND"
            f" n.archived_at IS NULL AND n.processed_at IS NOT NULL ORDER BY r.note_id", [*claim_ids, note_id]).fetchall():
        lst = out.setdefault(nid, [])
        lst.extend(x for x in (s, o) if x and x not in lst)
    return out


def _names_sentence(store: NoteStore, c: sqlite3.Connection, eids: Sequence[str]) -> str:
    """"Priya, the calibration plan and 2 more": the display names of the entities that caused a mark."""
    shown = []
    for e in list(eids)[:3]:
        r = c.execute("SELECT display_name, name FROM entities WHERE entity_id=?", (e,)).fetchone()
        if r is not None:
            shown.append(r[0] or r[1])
    if not shown:
        return "what it is about"
    more = max(0, len(eids) - len(shown))
    if more:
        return ", ".join(shown) + f" and {more} more"
    return shown[0] if len(shown) == 1 else ", ".join(shown[:-1]) + " and " + shown[-1]
