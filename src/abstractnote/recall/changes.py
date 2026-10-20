"""Since you last looked (``Notebook.changes``), the landing overview and the cue cards.

``changes`` is a system-time delta: everything the notebook recorded after a
cursor. The cursor encodes the high-water ``recorded_at`` second and the items
already returned at that second, so nothing is lost or repeated across calls.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

from .. import ids
from ..store.notes import clamp_limit, clamp_note, decode_cursor, encode_cursor
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


def changes(nb: Any, since: Optional[str] = None, limit: int = 50, as_received: Optional[str] = None
            ) -> Dict[str, Any]:
    """``{cursor, notes, entities, superseded_claims, closed_loops, failed_notes, insights}``.

    ``superseded_claims`` lists every claim that stopped being believed: ``kind`` ``superseded``
    (a later note replaced it; ``superseded_by`` names the new claim) or ``retracted`` (withdrawn
    by a note or by the person; ``superseded_by`` is null), with ``text`` in plain words.

    ``notes`` are the notes received after the cursor (``how: "captured"``) and the notes whose latest
    processing revision was recorded after it (``how: "understood"``: understood since, which after an
    import is most of what changed); each note appears once, at its latest event.

    ``since`` is a cursor from an earlier call or an ISO time. At most ``limit``
    items in total are returned, earliest first; the returned cursor continues
    exactly after them.

    ``as_received`` keeps only what derives from the archive as it stood on that date: notes and insights
    received by then, entities created and claims and tasks closed by the revisions those notes caused (or
    user actions recorded by then), failed notes received by then.
    """
    from ..store.archive import archive_for

    limit = clamp_limit(limit, 50, 500)
    ts, seen = _since(since)
    q = nb.store.q
    arch = archive_for(nb.store, as_received)

    def only(col: str, kind: str) -> Tuple[str, List[Any]]:
        if arch is None:
            return "", []
        frag, fp = arch.notes_in(col) if kind == "note" else arch.revisions_in(col)
        return f" AND {frag}", fp

    f_note, p_note = only("note_id", "note")
    f_qnote, p_qnote = only("q.note_id", "note")
    f_created, p_created = only("created_revision_id", "revision")
    f_closed, p_closed = only("c.closed_revision_id", "revision")
    f_task, p_task = only("closed_revision_id", "revision")
    found: List[Tuple[str, str, str, Dict[str, Any]]] = []  # (at, key, bucket, item)

    def add(bucket: str, kind: str, key: str, at: Optional[str], item: Dict[str, Any]) -> None:
        if not at or at < ts or (at == ts and f"{kind}:{key}" in seen):
            return
        found.append((at, f"{kind}:{key}", bucket, item))

    f_rev, p_rev = only("revision_id", "revision")
    for r in q("WITH u AS (SELECT note_id, MAX(recorded_at) AS understood_at FROM revisions WHERE note_id IS NOT NULL"
               " AND applied_index=1 AND abandoned=0" + f_rev + " GROUP BY note_id)"
               " SELECT n.*, u.understood_at, MAX(n.received_at, COALESCE(u.understood_at, '')) AS event_at"
               " FROM notes n LEFT JOIN u ON u.note_id=n.note_id WHERE n.insight_kind IS NULL AND n.archived_at IS NULL"
               " AND (n.received_at>=? OR u.understood_at>=?)" + f_note.replace(" note_id IN", " n.note_id IN") +
               " ORDER BY event_at, n.note_id LIMIT ?", (*p_rev, ts, ts, *p_note, limit + len(seen))):
        understood = bool(r["understood_at"]) and r["understood_at"] >= ts
        add("notes", "note", r["note_id"], r["event_at"],
            {**shapes.note_ref(r), "how": "understood" if understood else "captured"})
    for r in q("SELECT * FROM entities WHERE recorded_at>=?" + f_created + " ORDER BY recorded_at, entity_id LIMIT ?",
               (ts, *p_created, limit + len(seen))):
        add("entities", "entity", r["entity_id"], r["recorded_at"],
            {"entity_id": r["entity_id"], "name": r["name"], "display_name": r["display_name"] or r["name"],
             "kind": r["kind"], "first_seen": r["first_seen"]})
    for r in q("SELECT c.*, v.recorded_at AS closed_recorded_at, v.note_id AS closing_note_id FROM claims c"
               " JOIN revisions v ON v.revision_id=c.closed_revision_id WHERE c.status IN ('superseded','retracted')"
               " AND v.recorded_at>=?" + f_closed + " ORDER BY v.recorded_at, c.claim_id LIMIT ?",
               (ts, *p_closed, limit + len(seen))):
        add("superseded_claims", "claim", r["claim_id"], r["closed_recorded_at"],
            {"claim_id": r["claim_id"], "kind": "superseded" if r["status"] == "superseded" else "retracted",
             "text": claim_text(nb, r), "superseded_by": r["superseded_by"] if r["status"] == "superseded" else None,
             "note_id": r["closing_note_id"] or r["note_id"]})
    for r in q("SELECT * FROM tasks WHERE status='done' AND recorded_closed_at>=?" + f_task +
               " ORDER BY recorded_closed_at, task_id LIMIT ?", (ts, *p_task, limit + len(seen))):
        add("closed_loops", "task", r["task_id"], r["recorded_closed_at"],
            {"task_id": r["task_id"], "text": r["text"], "closed_at": r["closed_at"]})
    for r in q("SELECT q.note_id, q.last_error, q.failed_at, n.title FROM queue q JOIN notes n ON n.note_id=q.note_id"
               " WHERE q.state='failed' AND q.failed_at>=?" + f_qnote + " ORDER BY q.failed_at, q.note_id LIMIT ?",
               (ts, *p_qnote, limit + len(seen))):
        add("failed_notes", "failed", r["note_id"], r["failed_at"],
            {"note_id": r["note_id"], "title": r["title"] or "", "error": r["last_error"] or ""})
    for r in q("SELECT * FROM notes WHERE insight_kind IS NOT NULL AND received_at>=? AND archived_at IS NULL" +
               f_note + " ORDER BY received_at, note_id LIMIT ?", (ts, *p_note, limit + len(seen))):
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
    return {"cursor": encode_cursor("chg", high, sorted(keys)), **out}  # pinned keys only, with or without as_received


def claim_text(nb: Any, r: Any) -> str:
    """The claim in words a person reads: entity names and the predicate's plain words, never ids
    ("Dana participant LUT"; an entity without a name reads "someone")."""
    from ..process.vocabulary import predicate_words

    subject = nb.store._name(r["subject_id"]) or "someone"
    obj = (nb.store._name(r["object_id"]) or "something") if r["object_id"] else (r["object_literal"] or "something")
    return f"{subject} {predicate_words(r['predicate'])} {obj}"


def overview(nb: Any, days: int = 30, *, now: Optional[str] = None) -> Dict[str, Any]:
    """``{recent, entities, insights, resurfacing, activity}`` for the landing screen."""
    days, _ = clamp_note(days or 30, 30, 366)  # a clamp is logged (ADR 0007); ``activity`` has the days applied
    now = ids.normalize_time(now) if now else ids.utc_now()
    start = (ids.parse_time(now) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    q = nb.store.q
    recent = [shapes.note_ref(r) for r in q(
        "SELECT * FROM notes WHERE insight_kind IS NULL AND archived_at IS NULL ORDER BY occurred_at DESC,"
        " note_id DESC LIMIT 20")]
    entities = [{"entity_id": r["entity_id"], "name": r["name"], "display_name": r["display_name"] or r["name"],
                 "kind": r["kind"], "note_count": r["note_count"], "last_seen": r["last_seen"]} for r in q(
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
