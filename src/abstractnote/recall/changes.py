"""Since you last looked (``Notebook.changes``), the landing overview and the cue cards.

``changes`` is a system-time delta: everything the notebook recorded after a
cursor. The cursor encodes the high-water ``recorded_at`` second and the items
already returned at that second, so nothing is lost or repeated across calls.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

from .. import ids
from ..store.notes import clamp_limit, decode_cursor, encode_cursor
from . import shapes


def _since(since: Optional[str]) -> Tuple[str, set]:
    if not since:
        return "0000-01-01T00:00:00Z", set()
    s = str(since).strip()
    if s[:1].isdigit():
        return ids.normalize_time(s, field="since"), set()
    cur = decode_cursor(s)
    if not cur or len(cur) != 3 or cur[0] != "chg":
        raise ValueError("The cursor is not one this notebook issued.")
    return str(cur[1]), set(cur[2])


def changes(nb: Any, since: Optional[str] = None, limit: int = 50) -> Dict[str, Any]:
    """``{cursor, notes, entities, superseded_claims, closed_loops, failed_notes, insights}``.

    ``superseded_claims`` lists every claim that stopped being believed: ``kind`` ``superseded``
    (a later note replaced it; ``superseded_by`` names the new claim) or ``retracted`` (withdrawn
    by a note or by the person; ``superseded_by`` is null), with ``text`` in plain words.

    ``since`` is a cursor from an earlier call or an ISO time. At most ``limit``
    items in total are returned, earliest first; the returned cursor continues
    exactly after them.
    """
    limit = clamp_limit(limit, 50, 500)
    ts, seen = _since(since)
    q = nb.store.q
    found: List[Tuple[str, str, str, Dict[str, Any]]] = []  # (at, key, bucket, item)

    def add(bucket: str, kind: str, key: str, at: Optional[str], item: Dict[str, Any]) -> None:
        if not at or at < ts or (at == ts and f"{kind}:{key}" in seen):
            return
        found.append((at, f"{kind}:{key}", bucket, item))

    for r in q("SELECT * FROM notes WHERE insight_kind IS NULL AND received_at>=? AND archived_at IS NULL"
               " ORDER BY received_at, note_id LIMIT ?", (ts, limit + len(seen))):
        add("notes", "note", r["note_id"], r["received_at"], shapes.note_ref(r))
    for r in q("SELECT * FROM entities WHERE recorded_at>=? ORDER BY recorded_at, entity_id LIMIT ?",
               (ts, limit + len(seen))):
        add("entities", "entity", r["entity_id"], r["recorded_at"],
            {"entity_id": r["entity_id"], "name": r["name"], "kind": r["kind"], "first_seen": r["first_seen"]})
    for r in q("SELECT c.*, v.recorded_at AS closed_recorded_at, v.note_id AS closing_note_id FROM claims c"
               " JOIN revisions v ON v.revision_id=c.closed_revision_id WHERE c.status IN ('superseded','retracted')"
               " AND v.recorded_at>=? ORDER BY v.recorded_at, c.claim_id LIMIT ?", (ts, limit + len(seen))):
        add("superseded_claims", "claim", r["claim_id"], r["closed_recorded_at"],
            {"claim_id": r["claim_id"], "kind": "superseded" if r["status"] == "superseded" else "retracted",
             "text": claim_text(nb, r), "superseded_by": r["superseded_by"] if r["status"] == "superseded" else None,
             "note_id": r["closing_note_id"] or r["note_id"]})
    for r in q("SELECT * FROM tasks WHERE status='done' AND recorded_closed_at>=? ORDER BY recorded_closed_at,"
               " task_id LIMIT ?", (ts, limit + len(seen))):
        add("closed_loops", "task", r["task_id"], r["recorded_closed_at"],
            {"task_id": r["task_id"], "text": r["text"], "closed_at": r["closed_at"]})
    for r in q("SELECT q.note_id, q.last_error, q.failed_at, n.title FROM queue q JOIN notes n ON n.note_id=q.note_id"
               " WHERE q.state='failed' AND q.failed_at>=? ORDER BY q.failed_at, q.note_id LIMIT ?",
               (ts, limit + len(seen))):
        add("failed_notes", "failed", r["note_id"], r["failed_at"],
            {"note_id": r["note_id"], "title": r["title"] or "", "error": r["last_error"] or ""})
    for r in q("SELECT * FROM notes WHERE insight_kind IS NOT NULL AND received_at>=? AND archived_at IS NULL"
               " ORDER BY received_at, note_id LIMIT ?", (ts, limit + len(seen))):
        add("insights", "insight", r["note_id"], r["received_at"], shapes.insight(r))

    found.sort(key=lambda x: (x[0], x[1]))
    taken = found[:limit]
    out: Dict[str, Any] = {"notes": [], "entities": [], "superseded_claims": [], "closed_loops": [],
                           "failed_notes": [], "insights": []}
    for _at, _key, bucket, item in taken:
        out[bucket].append(item)
    if taken:
        high = taken[-1][0]
        keys = {k for at, k, _, _ in taken if at == high}
        if high == ts:
            keys |= seen
    else:
        high, keys = ts, set(seen)
    return {"cursor": encode_cursor("chg", high, sorted(keys)), **out}


def claim_text(nb: Any, r: Any) -> str:
    """The claim in words a person reads: entity names and the predicate's plain words, never ids
    ("Dana participant LUT"; an entity without a name reads "someone")."""
    from ..process.vocabulary import predicate_words

    subject = nb.store._name(r["subject_id"]) or "someone"
    obj = (nb.store._name(r["object_id"]) or "something") if r["object_id"] else (r["object_literal"] or "something")
    return f"{subject} {predicate_words(r['predicate'])} {obj}"


def overview(nb: Any, days: int = 30, *, now: Optional[str] = None) -> Dict[str, Any]:
    """``{recent, entities, insights, resurfacing, activity}`` for the landing screen."""
    days = max(1, min(366, int(days or 30)))
    now = ids.normalize_time(now) if now else ids.utc_now()
    start = (ids.parse_time(now) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    q = nb.store.q
    recent = [shapes.note_ref(r) for r in q(
        "SELECT * FROM notes WHERE insight_kind IS NULL AND archived_at IS NULL ORDER BY occurred_at DESC,"
        " note_id DESC LIMIT 20")]
    entities = [{"entity_id": r["entity_id"], "name": r["name"], "kind": r["kind"], "note_count": r["note_count"],
                 "last_seen": r["last_seen"]} for r in q(
        "SELECT * FROM entities WHERE merged_into IS NULL AND last_seen>=? ORDER BY note_count DESC, last_seen DESC,"
        " entity_id LIMIT 20", (start,))]
    insights = [shapes.insight(r) for r in q(
        "SELECT * FROM notes WHERE insight_kind IS NOT NULL AND acked_at IS NULL AND archived_at IS NULL"
        " ORDER BY received_at DESC, note_id DESC LIMIT 10")]
    from .reflect import anniversary_rows

    resurfacing = [shapes.note_ref(r) for r, _rule in anniversary_rows(nb, limit=10, now=now)]
    counts: Dict[str, Dict[str, int]] = {}
    for r in q("SELECT substr(occurred_at,1,10) AS d, COUNT(*) AS n FROM notes WHERE occurred_at>=? AND"
               " insight_kind IS NULL GROUP BY d", (start,)):
        counts.setdefault(r["d"], {"notes": 0, "revisions": 0})["notes"] = r["n"]
    for r in q("SELECT substr(at,1,10) AS d, COUNT(*) AS n FROM revisions WHERE at>=? AND applied_index=1"
               " GROUP BY d", (start,)):
        counts.setdefault(r["d"], {"notes": 0, "revisions": 0})["revisions"] = r["n"]
    activity = []
    day = ids.parse_time(start)
    end = ids.parse_time(now)
    while day.date() <= end.date():
        key = day.strftime("%Y-%m-%d")
        c = counts.get(key, {"notes": 0, "revisions": 0})
        activity.append({"day": key, "notes": c["notes"], "revisions": c["revisions"]})
        day += timedelta(days=1)
    return {"recent": recent, "entities": entities, "insights": insights, "resurfacing": resurfacing,
            "activity": activity}


def cues(nb: Any, limit: int = 3) -> Dict[str, Any]:
    """Cue cards: unacknowledged reflect insights (not dossiers), each with the subject a reply links to.

    ``{items: [{cue_id, text, kind, subject: {kind, id, title}, evidence_note_ids, entity_ids}]}``; ``cue_id`` is
    the insight note id; ``evidence_note_ids`` and ``entity_ids`` are the insight's own (the notes it rests on and
    the entities it is about), so a client shows why it surfaced without a second read.
    """
    limit = clamp_limit(limit, 3, 50)
    items = []
    for r in nb.store.q("SELECT * FROM notes WHERE insight_kind IS NOT NULL AND insight_kind!='dossier' AND"
                        " acked_at IS NULL AND archived_at IS NULL ORDER BY received_at DESC, note_id DESC LIMIT ?",
                        (limit,)):
        ins = shapes.insight(r)
        subject = {"kind": "cue", "id": r["note_id"], "title": ins["title"]}
        for eid in ins["entity_ids"]:
            e = nb.store.entity_row(nb.store.canonical(eid))
            if e is not None:
                subject = {"kind": "entity", "id": e["entity_id"], "title": e["name"]}
                break
        else:
            for nid in ins["evidence_note_ids"]:
                n = nb.store.note_row(nid)
                if n is not None:
                    subject = {"kind": "note", "id": nid, "title": n["title"] or shapes.excerpt(n["text"], 80)}
                    break
        items.append({"cue_id": r["note_id"], "text": ins["body"] or ins["title"], "kind": r["insight_kind"],
                      "subject": subject, "evidence_note_ids": ins["evidence_note_ids"],
                      "entity_ids": ins["entity_ids"]})
    return {"items": items}
