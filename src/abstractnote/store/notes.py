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


def clamp_limit(limit: Any, default: int = 50, maximum: int = 500) -> int:
    try:
        n = int(limit) if limit is not None else default
    except (TypeError, ValueError):
        raise ValueError("The limit must be a whole number.") from None
    return max(1, min(maximum, n))


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
    return joined[:budget]  # embedder input bound; the stored note is never cut


class NoteStore:
    """SQLite note index. Thread-safe through one RLock around the connection."""

    def __init__(self, path: str) -> None:
        self.path = str(path)
        self.lock = threading.RLock()
        self.conn = connect(self.path)
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
            out["excerpt"] = text[:280] + ("…" if len(text) > 280 else "")  # excerpt for lists; full text via get
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
        }

    def insert_revision(self, rev: Dict[str, Any]) -> bool:
        """Write the revision row (the intent). Returns False when the id exists (no-op)."""
        with self.lock:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO revisions(revision_id, note_id, attempt, action, target, at, recorded_at,"
                " run_id, ops_json, summary, source_json, warnings_json, result_json, applied_index, applied_kg)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0,0)",
                (rev["revision_id"], rev.get("note_id"), rev.get("attempt"), rev.get("action"), rev.get("target"),
                 rev["at"], rev["recorded_at"], rev.get("run_id"), _j(rev.get("ops") or []), rev.get("summary") or "",
                 _j(rev.get("source")) if rev.get("source") is not None else None, _j(rev.get("warnings") or []),
                 _j(rev.get("result")) if rev.get("result") is not None else None),
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
            "touched_entities": [], "touched_claims": [],
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
        sql = "SELECT * FROM claims WHERE (valid_from IS NULL OR valid_from<=?)"
        params: List[Any] = [at]
        if arch is None:
            sql += " AND (valid_until IS NULL OR valid_until>?)"
            params.append(at)
        else:
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
            if d["valid_until"] is None or d["valid_until"] > at:
                out.append(d)
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
        depth = max(1, min(3, int(depth or 1)))
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
            node_sql = "SELECT * FROM entities WHERE 1=1"
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

        ranked = sorted(nodes.values(), key=lambda r: (-(r["note_count"] or 0), r["entity_id"]))[:limit]
        keep_ids = {r["entity_id"] for r in ranked}
        edge_list = [e for e in edge_list if e["source"] in keep_ids and e["target"] in keep_ids]
        out_nodes = []
        for r in ranked:
            summary = r["summary"]
            if at_n and not arch:
                srow = self.q1("SELECT summary FROM entity_summaries WHERE entity_id=? AND at<=? ORDER BY at DESC,"
                               " revision_id DESC LIMIT 1", (r["entity_id"], at_n))
                summary = srow[0] if srow else None
            out_nodes.append({"id": r["entity_id"], "kind": r["kind"], "name": r["name"],
                              "display_name": r["display_name"] or r["name"],
                              "first_seen": r["first_seen"], "last_seen": r["last_seen"],
                              "note_count": r["note_count"], "summary": summary})
        for e in edge_list:
            e.pop("claim_ids", None)
        edge_list.sort(key=lambda e: (e["source"], e["target"], e["predicate"]))
        out: Dict[str, Any] = {"as_of": at_n or ids.utc_now(), "nodes": out_nodes, "edges": edge_list}
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
            sql += " AND n.note_id IN (SELECT note_id FROM note_topics WHERE topic=?)"
            params.append(topic)
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
            sql += " AND e.merged_into IS NULL"
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
        touched = [self.entity_brief(r[0]) for r in self.q(
            "SELECT DISTINCT entity_id FROM entity_notes WHERE at>=? AND at<?" + f_note + " ORDER BY entity_id",
            (s, e, *p_note))]
        created = [r[0] for r in self.q("SELECT entity_id FROM entities WHERE created_at>=? AND created_at<?" +
                                        f_created, (s, e, *p_created))]
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
            ("entities_created", rng("created_at", "entities")),
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

    def counts(self) -> Dict[str, int]:
        def n(sql: str) -> int:
            return int(self.q1(sql)[0])

        return {
            "notes": n("SELECT COUNT(*) FROM notes WHERE insight_kind IS NULL"),
            "insights": n("SELECT COUNT(*) FROM notes WHERE insight_kind IS NOT NULL"),
            "attachments": n("SELECT COUNT(*) FROM attachments"),
            "entities": n("SELECT COUNT(*) FROM entities WHERE merged_into IS NULL"),
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
        eid = p["entity_id"]
        row = self._require_entity(eid)
        self.c.execute("INSERT OR IGNORE INTO entity_summaries(entity_id, revision_id, at, note_id, summary, previous)"
                       " VALUES (?,?,?,?,?,?)", (eid, self.rid, self.at, self.note_id, p["summary"], row["summary"]))
        latest = self.c.execute("SELECT summary FROM entity_summaries WHERE entity_id=? ORDER BY at DESC,"
                                " rowid DESC LIMIT 1", (eid,)).fetchone()
        self.c.execute("UPDATE entities SET summary=? WHERE entity_id=?", (latest[0] if latest else p["summary"], eid))
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
        self._require_entity(eid)
        note_id = p.get("note_id") or self.note_id
        at = self.at
        if note_id:
            nrow = self.c.execute("SELECT occurred_at FROM notes WHERE note_id=?", (note_id,)).fetchone()
            if nrow:
                at = nrow[0]
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

    def op_claim_supersede(self, p: Dict[str, Any]) -> None:
        row = self._claim(p["claim_id"])
        cur = self.c.execute(
            "UPDATE claims SET status='superseded', superseded_by=?, valid_until=COALESCE(valid_until, ?),"
            " closed_reason=?, closed_revision_id=? WHERE claim_id=? AND status='active'",
            (p.get("by_claim_id"), p["valid_until"], p.get("reason"), self.rid, p["claim_id"]))
        if cur.rowcount:
            self.stats["claims_superseded"].append(p["claim_id"])
        self._dirty_claim_relation(row)

    def op_claim_retract(self, p: Dict[str, Any]) -> None:
        row = self._claim(p["claim_id"])
        cur = self.c.execute(
            "UPDATE claims SET status='retracted', valid_until=COALESCE(valid_until, ?), closed_reason=?,"
            " closed_revision_id=? WHERE claim_id=? AND status!='retracted'",
            (p.get("valid_until") or self.at, p.get("reason"), self.rid, p["claim_id"]))
        if cur.rowcount:
            self.stats["claims_retracted"].append(p["claim_id"])
        self._dirty_claim_relation(row)

    def op_claim_reinforce(self, p: Dict[str, Any]) -> None:
        row = self._claim(p["claim_id"])
        note_id = p.get("note_id") or self.note_id
        at = self.at
        cur = self.c.execute("INSERT OR IGNORE INTO claim_reinforcements(claim_id, note_id, revision_id, at)"
                             " VALUES (?,?,?,?)", (p["claim_id"], note_id, self.rid, at))
        if cur.rowcount:
            self.c.execute("UPDATE claims SET reinforced_count=reinforced_count+1 WHERE claim_id=?", (p["claim_id"],))
            self.stats["claims_reinforced"].append(p["claim_id"])
        self._dirty_claim_relation(row)

    # --- topics, tasks, notes
    def op_topic_assign(self, p: Dict[str, Any]) -> None:
        note_id = p.get("note_id") or self.note_id
        self.s.assign_topic(self.c, note_id, p["topic"], by="revision", revision_id=self.rid, at=self.at)

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
        """Archive a note (``{note_id, duplicate_of?, reason?}``): it leaves lists, search and the queue (a waiting
        copy is never processed), keeps its links, and names the note it duplicates (``abstractnote
        dedupe-sources``)."""
        note_id = p["note_id"]
        if self.c.execute("SELECT 1 FROM notes WHERE note_id=?", (note_id,)).fetchone() is None:
            raise ValueError(f"The note {note_id} is not in this notebook.")
        self.c.execute("UPDATE notes SET archived_at=COALESCE(archived_at, ?), status='archived',"
                       " duplicate_of=COALESCE(?, duplicate_of) WHERE note_id=?",
                       (self.recorded_at, p.get("duplicate_of"), note_id))
        self.c.execute("UPDATE queue SET state='done', leased_by=NULL, lease_until=NULL, next_attempt_at=NULL"
                       " WHERE note_id=? AND state IN ('queued','failed')", (note_id,))

    # --- end of revision
    def finish(self) -> None:
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
        if self.note_id and not self.rev.get("action"):
            self.c.execute("UPDATE notes SET status=CASE WHEN status='archived' THEN status ELSE 'processed' END,"
                           " processed_at=? WHERE note_id=?", (self.recorded_at, self.note_id))
            self.c.execute("UPDATE queue SET state='done', done_at=?, leased_by=NULL, lease_until=NULL,"
                           " last_error=NULL WHERE note_id=?", (self.recorded_at, self.note_id))
