"""What the notebook has accumulated (``GET /note/stats``, ``Notebook.stats``; backlog 0030).

Every number is counted by SQLite from the stored records; no model is involved. History time is used for
activity (a note's ``occurred_at``, a revision's ``at``, an entity's ``created_at``, a statement's
``observed_at``, a supersession's ``valid_until``), so an import of old material fills the weeks it describes.
Weeks are named by their Monday (``YYYY-MM-DD``), months ``YYYY-MM``; the weekly list covers every week from
the earliest to the latest dated record, empty weeks included.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from .. import ids
from .reflect import SILENT_DAYS

TOP_ENTITIES = 20
TOP_PREDICATES = 15
QUIET_LIMIT = 20
QUIET_MIN_NOTES = 2   # an entity counts as "active before" from two notes on
_WEEK = "date({col}, '-6 days', 'weekday 1')"  # the Monday of the week holding {col}

_NOTES = "FROM notes WHERE insight_kind IS NULL AND archived_at IS NULL"


def _map(rows: Any) -> Dict[str, int]:
    return {str(r[0]): int(r[1]) for r in rows}


def _weeks(q: Any) -> List[Dict[str, Any]]:
    series = {
        "notes": f"SELECT {_WEEK.format(col='occurred_at')} AS w, COUNT(*) {_NOTES} GROUP BY w",
        "revisions": f"SELECT {_WEEK.format(col='at')} AS w, COUNT(*) FROM revisions WHERE applied_index=1"
                     " GROUP BY w",
        "new_entities": f"SELECT {_WEEK.format(col='created_at')} AS w, COUNT(*) FROM entities"
                        " WHERE merged_into IS NULL GROUP BY w",
        "claims_added": f"SELECT {_WEEK.format(col='observed_at')} AS w, COUNT(*) FROM claims GROUP BY w",
        "claims_superseded": f"SELECT {_WEEK.format(col='valid_until')} AS w, COUNT(*) FROM claims"
                             " WHERE status='superseded' AND valid_until IS NOT NULL GROUP BY w",
    }
    counts: Dict[str, Dict[str, int]] = {}
    for name, sql in series.items():
        for w, n in q(sql):
            if w:
                counts.setdefault(w, {})[name] = int(n)
    if not counts:
        return []
    first, last = date.fromisoformat(min(counts)), date.fromisoformat(max(counts))
    out = []
    d = first
    while d <= last:
        key = d.isoformat()
        out.append({"week": key, **{name: counts.get(key, {}).get(name, 0) for name in series}})
        d += timedelta(days=7)
    return out


def _growth(q: Any) -> List[Dict[str, Any]]:
    ents = _map(q("SELECT substr(COALESCE(first_seen, created_at), 1, 7) AS m, COUNT(*) FROM entities"
                  " WHERE merged_into IS NULL GROUP BY m"))
    claims = _map(q("SELECT substr(observed_at, 1, 7) AS m, COUNT(*) FROM claims WHERE status!='retracted'"
                    " GROUP BY m"))
    months = sorted(set(ents) | set(claims))
    if not months:
        return []
    out, e_total, c_total = [], 0, 0
    y, m = map(int, months[0].split("-"))
    end = months[-1]
    while True:
        key = f"{y:04d}-{m:02d}"
        e_total += ents.get(key, 0)
        c_total += claims.get(key, 0)
        out.append({"month": key, "entities": e_total, "claims": c_total, "new_entities": ents.get(key, 0),
                    "new_claims": claims.get(key, 0)})
        if key >= end:
            return out
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def stats(nb: Any, *, now: Optional[str] = None) -> Dict[str, Any]:
    """``{generated_at, span, totals, activity_weekly, entities_by_kind, top_entities, claims_by_predicate, tasks,
    insights_by_kind, communities, processing, growth_monthly, quiet_entities}`` (docs/api.md)."""
    from ..process.vocabulary import predicate_words

    now_n = ids.normalize_time(now, field="now") if now else ids.utc_now()
    q, q1 = nb.store.q, nb.store.q1

    def n(sql: str, params: Any = ()) -> int:
        return int(q1(sql, params)[0] or 0)

    span = q1(f"SELECT MIN(occurred_at), MAX(occurred_at) {_NOTES}")
    totals = {
        "notes": n(f"SELECT COUNT(*) {_NOTES}"),
        "notes_archived": n("SELECT COUNT(*) FROM notes WHERE insight_kind IS NULL AND archived_at IS NOT NULL"),
        "insights": n("SELECT COUNT(*) FROM notes WHERE insight_kind IS NOT NULL AND archived_at IS NULL"),
        "attachments": n("SELECT COUNT(*) FROM attachments"),
        "attachment_bytes": n("SELECT COALESCE(SUM(size), 0) FROM attachments"),
        "entities": n("SELECT COUNT(*) FROM entities WHERE merged_into IS NULL"),
        "entities_merged": n("SELECT COUNT(*) FROM entities WHERE merged_into IS NOT NULL"),
        "claims": n("SELECT COUNT(*) FROM claims"),
        "claims_active": n("SELECT COUNT(*) FROM claims WHERE status='active'"),
        "revisions": n("SELECT COUNT(*) FROM revisions WHERE applied_index=1"),
        "topics": n("SELECT COUNT(*) FROM topics"),
        "notes_by_kind": _map(q(f"SELECT COALESCE(kind, 'unfiled') AS k, COUNT(*) {_NOTES} GROUP BY k"
                                " ORDER BY 2 DESC, 1")),
        # a live capture by its client (web, watch, iphone, cli, agent); an import by its importer
        "notes_by_source": _map(q(
            "SELECT CASE WHEN json_extract(source_json, '$.importer') IS NOT NULL THEN 'importer:' ||"
            f" json_extract(source_json, '$.importer') ELSE COALESCE(client, 'unknown') END AS s, COUNT(*) {_NOTES}"
            " GROUP BY s ORDER BY 2 DESC, 1")),
        "attachments_by_mime": _map(q("SELECT mime, COUNT(*) FROM attachments GROUP BY mime ORDER BY 2 DESC, 1")),
    }
    top = [{"entity_id": r["entity_id"], "name": r["name"], "display_name": r["display_name"] or r["name"],
            "kind": r["kind"], "note_count": int(r["note_count"] or 0), "first_seen": r["first_seen"],
            "last_seen": r["last_seen"]}
           for r in q("SELECT * FROM entities WHERE merged_into IS NULL AND note_count>0"
                      " ORDER BY note_count DESC, last_seen DESC, entity_id LIMIT ?", (TOP_ENTITIES,))]
    predicates = [{"predicate": r[0], "words": predicate_words(r[0]), "count": int(r[1]), "active": int(r[2])}
                  for r in q("SELECT predicate, COUNT(*), SUM(status='active') FROM claims GROUP BY predicate"
                             " ORDER BY 2 DESC, 1 LIMIT ?", (TOP_PREDICATES,))]
    tasks = _map(q("SELECT status, COUNT(*) FROM tasks GROUP BY status"))
    status = _map(q(f"SELECT status, COUNT(*) {_NOTES} GROUP BY status"))
    quiet = [{"entity_id": r["entity_id"], "name": r["name"], "kind": r["kind"], "note_count": int(r["note_count"]),
              "last_seen": r["last_seen"], "days_silent": int(r["days"])}
             for r in q("SELECT entity_id, name, kind, note_count, last_seen,"
                        " CAST(julianday(?) - julianday(last_seen) AS INTEGER) AS days FROM entities"
                        " WHERE merged_into IS NULL AND note_count>=? AND last_seen IS NOT NULL"
                        " AND julianday(?) - julianday(last_seen) >= ?"
                        " ORDER BY note_count DESC, last_seen DESC, entity_id LIMIT ?",
                        (now_n, QUIET_MIN_NOTES, now_n, SILENT_DAYS, QUIET_LIMIT))]
    return {
        "generated_at": now_n,
        "span": {"first_note_at": span[0], "last_note_at": span[1]},
        "totals": totals,
        "activity_weekly": _weeks(q),
        "entities_by_kind": _map(q("SELECT kind, COUNT(*) FROM entities WHERE merged_into IS NULL GROUP BY kind"
                                   " ORDER BY 2 DESC, 1")),
        "top_entities": top,
        "claims_by_predicate": predicates,
        "tasks": {"open": tasks.get("open", 0), "closed": tasks.get("done", 0) + tasks.get("dropped", 0),
                  "done": tasks.get("done", 0), "dropped": tasks.get("dropped", 0)},
        "insights_by_kind": _map(q("SELECT insight_kind, COUNT(*) FROM notes WHERE insight_kind IS NOT NULL"
                                   " AND archived_at IS NULL GROUP BY insight_kind ORDER BY 2 DESC, 1")),
        "communities": n("SELECT COUNT(*) FROM communities"),
        "processing": {"understood": status.get("processed", 0),
                       "queued": status.get("queued", 0) + status.get("received", 0),
                       "processing": status.get("processing", 0), "failed": status.get("failed", 0),
                       "duplicate": status.get("duplicate", 0)},
        "growth_monthly": _growth(q),
        "quiet_entities": quiet,
        # ADR 0007: the three lists above are selections (most notes first); what they leave out is counted
        "omitted": {
            "top_entities": max(0, n("SELECT COUNT(*) FROM entities WHERE merged_into IS NULL AND note_count>0")
                                - len(top)),
            "claims_by_predicate": max(0, n("SELECT COUNT(DISTINCT predicate) FROM claims") - len(predicates)),
            "quiet_entities": max(0, int(q("SELECT COUNT(*) FROM entities WHERE merged_into IS NULL AND note_count>=?"
                                           " AND last_seen IS NOT NULL AND julianday(?) - julianday(last_seen) >= ?",
                                           (QUIET_MIN_NOTES, now_n, SILENT_DAYS))[0][0] or 0) - len(quiet)),
        },
    }
