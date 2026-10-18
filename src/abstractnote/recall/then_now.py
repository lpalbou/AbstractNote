"""Then versus now: the earliest and latest understanding of an entity (or a relationship) and the
turning points between them, each with the note that caused it. Deterministic; cached per entity
revision count (``then_now_cache``)."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from ..store.notes import _uj


def _claim_line(c: Dict[str, Any]) -> str:
    obj = c.get("object_name") or c.get("object_literal") or c.get("object_id")
    return f"{c.get('subject_name') or c['subject_id']} {c['predicate']} {obj}"


def then_now(nb: Any, entity_id: str) -> Dict[str, Any]:
    s = nb.store
    if s.entity_row(entity_id) is None:
        raise ValueError("That entity is not in this notebook.")
    eid = s.canonical(entity_id)
    members = s.merged_members(eid)
    q = ",".join("?" for _ in members)
    count = int(s.q1(f"SELECT COALESCE(SUM(revision_count), 0) FROM entities WHERE entity_id IN ({q})", members)[0])
    key = "entity:" + eid
    cached = s.q1("SELECT revision_count, payload_json FROM then_now_cache WHERE key=?", (key,))
    if cached is not None and int(cached[0]) == count:
        return json.loads(cached[1])

    summaries = s.q(f"SELECT * FROM entity_summaries WHERE entity_id IN ({q}) ORDER BY at, revision_id", members)
    earliest_claims = s.q(f"SELECT * FROM claims WHERE subject_id IN ({q}) OR object_id IN ({q})"
                          f" ORDER BY observed_at, seq LIMIT 5", [*members, *members])
    current = [c for c in s.beliefs_at(_now(), entity_id=eid)]

    def summary_point(r: Any) -> Dict[str, Any]:
        return {"at": r["at"], "summary": r["summary"], "note_id": r["note_id"], "revision_id": r["revision_id"]}

    turning: List[Dict[str, Any]] = []
    for r in summaries[1:]:
        turning.append({"at": r["at"], "kind": "summary", "text": r["summary"], "note_id": r["note_id"],
                        "revision_id": r["revision_id"]})
    for r in s.q(f"SELECT c.*, v.at AS closed_at, v.note_id AS closing_note FROM claims c JOIN revisions v ON"
                 f" v.revision_id=c.closed_revision_id WHERE (c.subject_id IN ({q}) OR c.object_id IN ({q}))",
                 [*members, *members]):
        d = s.claim_dict(r)
        turning.append({"at": r["closed_at"], "kind": "retract" if r["status"] == "retracted" else "supersede",
                        "text": _claim_line(d) + (f" (replaced: {r['closed_reason']})" if r["closed_reason"] else ""),
                        "claim_id": r["claim_id"], "superseded_by": r["superseded_by"],
                        "note_id": r["closing_note"], "revision_id": r["closed_revision_id"]})
    for r in s.q(f"SELECT * FROM claims WHERE (subject_id IN ({q}) OR object_id IN ({q})) AND disputed_with_json!='[]'"
                 f" AND certainty='disputed'", [*members, *members]):
        turning.append({"at": r["observed_at"], "kind": "dispute", "text": _claim_line(s.claim_dict(r)),
                        "claim_id": r["claim_id"], "note_id": r["note_id"], "revision_id": r["revision_id"]})
    for r in s.q(f"SELECT * FROM merges WHERE from_id IN ({q}) OR into_id IN ({q})", [*members, *members]):
        turning.append({"at": r["at"], "kind": "merge", "text": f"{r['from_id']} is {r['into_id']}",
                        "note_id": (s.revision_row(r["revision_id"]) or {"note_id": None})["note_id"],
                        "revision_id": r["revision_id"]})
    turning.sort(key=lambda t: (t["at"] or "", t["kind"]))
    payload = {
        "entity_id": eid,
        "earliest": summary_point(summaries[0]) if summaries else None,
        "latest": summary_point(summaries[-1]) if summaries else None,
        "earliest_claims": [s.claim_dict(r) for r in earliest_claims],
        "current_beliefs": current,
        "turning_points": turning,
        "revision_count": count,
    }
    with s.lock:
        s.conn.execute("INSERT OR REPLACE INTO then_now_cache(key, revision_count, payload_json) VALUES (?,?,?)",
                       (key, count, json.dumps(payload, sort_keys=True)))
    return payload


def _now() -> str:
    from .. import ids

    return ids.utc_now()


def relation_then_now(claims: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Then versus now of a link, from its claims in history order (as built by ``expand.relation``)."""
    if not claims:
        return {"earliest": [], "latest": [], "turning_points": []}
    first_at = claims[0]["observed_at"]
    earliest = [c for c in claims if c["observed_at"] == first_at]
    latest = [c for c in claims if c["status"] == "active" and c["valid_until"] is None]
    turning = [{"at": c["closed"]["at"], "kind": c["closed"]["kind"], "claim_id": c["claim_id"],
                "superseded_by": c["superseded_by"], "note_id": c["closed"]["note_id"],
                "reason": c["closed"]["reason"]} for c in claims if c.get("closed")]
    turning += [{"at": c["observed_at"], "kind": "dispute", "claim_id": c["claim_id"], "note_id": c["note_id"],
                 "disputed_with": c["disputed_with"]} for c in claims if c["certainty"] == "disputed"]
    turning.sort(key=lambda t: (t["at"] or "", t["kind"]))
    return {"earliest": [_brief(c) for c in earliest], "latest": [_brief(c) for c in latest],
            "turning_points": turning}


def _brief(c: Dict[str, Any]) -> Dict[str, Any]:
    return {"claim_id": c["claim_id"], "text": _claim_line(c), "observed_at": c["observed_at"],
            "note_id": c["note_id"], "certainty": c["certainty"]}
