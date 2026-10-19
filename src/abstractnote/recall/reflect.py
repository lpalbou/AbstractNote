"""Deterministic reflect candidates (docs/cognition.md, "note-reflect").

Each function returns ``{"items": [...]}``; every item names the ``rule`` that
produced it and carries ``note_ids`` and ``entity_ids`` so the reflect flow can
write an insight with provenance. Thresholds are explicit constants; the model
decides what deserves attention.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

from .. import ids
from ..store.notes import _uj, clamp_limit
from . import shapes

OPEN_TASK_AGE_DAYS = 14
QUESTION_AGE_DAYS = 14
TASK_MENTIONS_MIN = 2          # an open task whose note was revisited this many times
SILENT_DAYS = 60               # an entity or topic with no note for this long has gone quiet
RECENT_DAYS = 7                # "recent" notes for resurfacing


def _now(now: Optional[str]) -> str:
    return ids.normalize_time(now) if now else ids.utc_now()


def _minus(ts: str, days: int) -> str:
    return (ids.parse_time(ts) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _days(a: str, b: str) -> int:
    return int((ids.parse_time(b) - ids.parse_time(a)).total_seconds() // 86400)


def _since(since: Optional[str]) -> Optional[str]:
    return ids.optional_time(since, field="since")


def _entities_of_note(nb: Any, note_id: Optional[str]) -> List[str]:
    if not note_id:
        return []
    return sorted({nb.store.canonical(r[0]) for r in nb.store.q("SELECT entity_id FROM entity_notes WHERE note_id=?",
                                                                (note_id,))})


def open_loops(nb: Any, limit: int = 20, since: Optional[str] = None, *, now: Optional[str] = None
               ) -> Dict[str, Any]:
    limit = clamp_limit(limit, 20, 200)
    now = _now(now)
    since_n = _since(since)
    items: List[Dict[str, Any]] = []
    q = nb.store.q
    sql = "SELECT * FROM tasks WHERE status='open' AND opened_at<=?"
    params: List[Any] = [_minus(now, OPEN_TASK_AGE_DAYS)]
    if since_n:
        sql += " AND opened_at>=?"
        params.append(since_n)
    for r in q(sql + " ORDER BY opened_at, task_id", params):
        mentions = 0
        if r["note_id"]:
            mentions = q("SELECT COUNT(*) FROM note_links WHERE dst_note_id=? AND predicate='ex:revisits'",
                         (r["note_id"],))[0][0]
        rule = "task_mentioned_repeatedly" if mentions >= TASK_MENTIONS_MIN else f"open_task_older_than_{OPEN_TASK_AGE_DAYS}_days"
        items.append({"rule": rule, "kind": "task", "task_id": r["task_id"], "text": r["text"],
                      "opened_at": r["opened_at"], "age_days": _days(r["opened_at"], now), "mentions": mentions + 1,
                      "note_ids": [r["note_id"]] if r["note_id"] else [], "entity_ids": _entities_of_note(nb, r["note_id"])})
    sql = ("SELECT * FROM notes n WHERE n.kind='question' AND n.archived_at IS NULL AND n.occurred_at<=? AND NOT"
           " EXISTS (SELECT 1 FROM note_links l WHERE l.dst_note_id=n.note_id AND l.predicate IN"
           " ('ex:revisits','dcterms:references'))")
    params = [_minus(now, QUESTION_AGE_DAYS)]
    if since_n:
        sql += " AND n.occurred_at>=?"
        params.append(since_n)
    for r in q(sql + " ORDER BY n.occurred_at, n.note_id", params):
        items.append({"rule": "question_without_followup", "kind": "question", "text": r["title"] or
                      shapes.excerpt(r["text"], 160), "opened_at": r["occurred_at"],
                      "age_days": _days(r["occurred_at"], now), "note_ids": [r["note_id"]],
                      "entity_ids": _entities_of_note(nb, r["note_id"])})
    items.sort(key=lambda it: (0 if it["rule"] == "task_mentioned_repeatedly" else 1, it["opened_at"]))
    return {"items": items[:limit]}


def contradictions(nb: Any, limit: int = 20, since: Optional[str] = None) -> Dict[str, Any]:
    """Claim pairs a note marked as disagreeing (rule ``disputed_pair``: the linking's ``contradicts``
    decision, stored as ``disputed_with``). Nothing else is a candidate: processing records no
    single-valued predicates, and a subject with several current objects of one predicate (several
    mentions, several parts) is ordinary, so it is never offered as a contradiction."""
    limit = clamp_limit(limit, 20, 200)
    since_n = _since(since)
    q = nb.store.q
    items: List[Dict[str, Any]] = []
    seen = set()
    sql = "SELECT * FROM claims WHERE status='active' AND disputed_with_json!='[]'"
    params: List[Any] = []
    if since_n:
        sql += " AND observed_at>=?"
        params.append(since_n)
    for r in q(sql + " ORDER BY observed_at, claim_id", params):
        for other in _uj(r["disputed_with_json"], []) or []:
            pair = tuple(sorted((r["claim_id"], other)))
            if pair in seen:
                continue
            o = nb.store.claim_row(other)
            if o is None or o["status"] != "active":
                continue
            seen.add(pair)
            claims = [nb.store.claim_dict(x) for x in (r, o)]
            items.append({"rule": "disputed_pair", "claims": claims,
                          "text": " / ".join(_claim_line(c) for c in claims),
                          "note_ids": sorted({c["note_id"] for c in claims if c["note_id"]}),
                          "entity_ids": sorted({nb.store.canonical(c["subject_id"]) for c in claims})})
    return {"items": items[:limit]}


def _claim_line(c: Dict[str, Any]) -> str:
    obj = c.get("object_name") or c.get("object_literal") or c.get("object_id")
    return f"{c.get('subject_name') or c['subject_id']} {c['predicate']} {obj} ({c['observed_at'][:10]})"


def resurface(nb: Any, limit: int = 20, since: Optional[str] = None, *, now: Optional[str] = None
              ) -> Dict[str, Any]:
    limit = clamp_limit(limit, 20, 200)
    now = _now(now)
    recent_from = _since(since) or _minus(now, RECENT_DAYS)
    quiet_before = _minus(now, SILENT_DAYS)
    q = nb.store.q
    recent_entities = {nb.store.canonical(r[0]) for r in q("SELECT DISTINCT entity_id FROM entity_notes WHERE at>=?",
                                                           (recent_from,))}
    items: List[Dict[str, Any]] = []
    if recent_entities:
        marks = ",".join("?" for _ in recent_entities)
        rel = q(f"SELECT subject_id, object_id FROM relations WHERE subject_id IN ({marks}) OR object_id IN ({marks})",
                [*recent_entities, *recent_entities])
        neighbours: Dict[str, set] = {}
        for r in rel:
            for a, b in ((r[0], r[1]), (r[1], r[0])):
                if a in recent_entities and b not in recent_entities:
                    neighbours.setdefault(b, set()).add(a)
        for eid, via in sorted(neighbours.items()):
            e = nb.store.entity_row(eid)
            if e is None or e["merged_into"] or not e["last_seen"] or e["last_seen"] >= quiet_before:
                continue
            last_notes = [r[0] for r in q("SELECT note_id FROM entity_notes WHERE entity_id=? ORDER BY at DESC LIMIT 3",
                                          (eid,))]
            items.append({"rule": "silent_but_related", "kind": "entity", "entity_id": eid, "text": e["name"],
                          "last_seen": e["last_seen"], "silent_days": _days(e["last_seen"], now),
                          "related_to": sorted(via), "note_ids": last_notes, "entity_ids": [eid, *sorted(via)]})
    for r in q("SELECT DISTINCT t.topic FROM note_topics t JOIN notes n ON n.note_id=t.note_id WHERE n.occurred_at>=?",
               (recent_from,)):
        prev = q("SELECT MAX(n.occurred_at), GROUP_CONCAT(n.note_id) FROM note_topics t JOIN notes n ON"
                 " n.note_id=t.note_id WHERE t.topic=? AND n.occurred_at<?", (r[0], recent_from))[0]
        if prev[0] and prev[0] < _minus(recent_from, SILENT_DAYS):
            items.append({"rule": "topic_back_after_silence", "kind": "topic", "topic": r[0], "text": r[0],
                          "last_seen": prev[0], "silent_days": _days(prev[0], recent_from),
                          "note_ids": (prev[1] or "").split(",")[-3:], "entity_ids": []})
    items.sort(key=lambda it: -it["silent_days"])
    return {"items": items[:limit]}


def anniversary_rows(nb: Any, limit: int = 20, since: Optional[str] = None, *, now: Optional[str] = None
                     ) -> List[Tuple[Any, str]]:
    """Notes from today's month and day in earlier years, then today's day of month in the past 12 months."""
    now = _now(now)
    since_n = _since(since)
    md, d, ym = now[5:10], now[8:10], now[:7]
    out: List[Tuple[Any, str]] = []
    base = "SELECT * FROM notes WHERE insight_kind IS NULL AND archived_at IS NULL"
    params: List[Any] = []
    if since_n:
        base += " AND occurred_at>=?"
        params.append(since_n)
    for r in nb.store.q(base + " AND substr(occurred_at,6,5)=? AND substr(occurred_at,1,4)<? ORDER BY occurred_at DESC",
                        [*params, md, now[:4]]):
        out.append((r, "same_day_previous_year"))
    year_ago = _minus(now, 366)
    for r in nb.store.q(base + " AND substr(occurred_at,9,2)=? AND substr(occurred_at,1,7)<? AND occurred_at>=?"
                        " AND substr(occurred_at,6,5)!=? ORDER BY occurred_at DESC", [*params, d, ym, year_ago, md]):
        out.append((r, "same_day_previous_month"))
    return out[: clamp_limit(limit, 20, 200)]


def anniversaries(nb: Any, limit: int = 20, since: Optional[str] = None, *, now: Optional[str] = None
                  ) -> Dict[str, Any]:
    now_n = _now(now)
    items = []
    for r, rule in anniversary_rows(nb, limit, since, now=now_n):
        ref = shapes.note_ref(r)
        items.append({"rule": rule, "kind": "note", **ref, "text": ref["title"] or ref["excerpt"],
                      "years_ago": int(now_n[:4]) - int(r["occurred_at"][:4]),
                      "note_ids": [r["note_id"]], "entity_ids": _entities_of_note(nb, r["note_id"])})
    return {"items": items}
