"""Then versus now of an entity (or a relationship): what the notebook knew of it at the start, what it knows
now, and the turning points between them, each with the note that caused it.

Everything is in history order, the order of dated knowledge (ADR 0008 §2, §5), never the order notes were
received or applied, nor the order summaries were written:

- ``earliest`` is the earliest dated knowledge: the day of the first note that mentions the entity (or of its
  earliest statement), with the summary that describes that day when one does (a summary dated that day), else
  the statements observed that day (``earliest_claims``), else the first note's own one line (``text``). An
  archive from 2022 processed after the 2026 notes therefore moves ``earliest`` to 2022 even when the 2022 note
  wrote no summary.
- ``latest`` is the current summary: the last one in dated order (``store.notes.SUMMARY_ORDER``), the one the
  entity card shows; ``current_beliefs`` are the statements valid now.
- ``turning_points`` come from the statements' validity windows, never from summary rewrites: a belief replaced
  by another (``supersede``, at the END of its window, which is the next belief's start, with the belief that
  replaced it), a belief whose window ended without a replacement (``ended``), a statement withdrawn
  (``retract``), a dispute, a merge. A statement closed with the kind ``reinterpreted`` (its note understood
  again, ADR 0008 §1; the kind is stored in ``claims.status``) is not a change in what was true and is not a
  turning point; a closure kind the view does not know fails loudly.

Deterministic; cached per entity revision count (``then_now_cache``; a retrospective insertion drops the card)."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from ..store.notes import REINTERPRETED, claim_reinterpreted_sql

_NOT_REINTERPRETED = f"NOT {claim_reinterpreted_sql('c')}"

#: The stored closure kind (``claims.status``) of a closed statement -> its turning point; ``None`` = not a change
#: in what was true (a re-understanding withdrew it). A kind not listed here fails loudly, never read as a change.
_CLOSURE_KINDS = {"superseded": "supersede", "retracted": "retract", REINTERPRETED: None}


def _claim_line(c: Dict[str, Any]) -> str:
    obj = c.get("object_name") or c.get("object_literal") or c.get("object_id")
    return f"{c.get('subject_name') or c['subject_id']} {c['predicate']} {obj}"


def _day(ts: Optional[str]) -> str:
    return str(ts or "")[:10]


def then_now(nb: Any, entity_id: str) -> Dict[str, Any]:
    """``{entity_id, first_seen, earliest, latest, earliest_claims, current_beliefs, turning_points,
    revision_count}`` (module doc; docs/api.md)."""
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

    summaries = s.summary_rows(members)  # dated knowledge order
    first_note = s.q1(f"SELECT en.at, en.note_id FROM entity_notes en WHERE en.entity_id IN ({q})"
                      " ORDER BY en.at, en.note_id LIMIT 1", members)
    first_claim = s.q1(f"SELECT MIN(c.observed_at) FROM claims c WHERE (c.subject_id IN ({q}) OR c.object_id IN"
                       f" ({q})) AND c.status!='retracted' AND {_NOT_REINTERPRETED}", [*members, *members])
    dates = [x for x in (first_note[0] if first_note else None, first_claim[0] if first_claim else None) if x]
    first_at = min(dates) if dates else (summaries[0]["at"] if summaries else None)
    earliest_claims = s.q(f"SELECT * FROM claims c WHERE (c.subject_id IN ({q}) OR c.object_id IN ({q})) AND"
                          f" c.status!='retracted' AND {_NOT_REINTERPRETED} AND substr(c.observed_at, 1, 10)=?"
                          " ORDER BY c.observed_at, c.seq LIMIT 5", [*members, *members, _day(first_at)]) if first_at else []
    now = _now()
    current = [c for c in s.beliefs_at(now, entity_id=eid)]

    def summary_point(r: Any) -> Dict[str, Any]:
        return {"at": r["at"], "summary": r["summary"], "note_id": r["note_id"], "revision_id": r["revision_id"],
                "recorded_at": r["recorded_at"]}

    earliest: Optional[Dict[str, Any]] = None
    if first_at:
        then = summaries[0] if summaries and _day(summaries[0]["at"]) <= _day(first_at) else None
        if then is not None:
            earliest = summary_point(then)
            earliest["at"] = first_at
        else:
            note_id = first_note[1] if first_note and first_note[0] == first_at else None
            if note_id is None and earliest_claims:
                note_id = earliest_claims[0]["note_id"]
            earliest = {"at": first_at, "summary": None, "note_id": note_id, "revision_id": None, "recorded_at": None}
            if not earliest_claims and note_id:
                n = s.note_row(note_id)
                text = (n["summary"] or n["one_line"] or n["title"]) if n is not None else None
                if text:
                    earliest["text"] = text

    turning: List[Dict[str, Any]] = []
    for r in s.q(f"SELECT c.*, v.at AS closed_at, v.note_id AS closing_note FROM claims c LEFT JOIN revisions v ON"
                 f" v.revision_id=c.closed_revision_id WHERE (c.subject_id IN ({q}) OR c.object_id IN ({q}))"
                 " AND (c.status!='active' OR c.valid_until IS NOT NULL)", [*members, *members]):
        # the stored closure kind decides (_CLOSURE_KINDS): a reinterpreted closure is no turning point
        d = s.claim_dict(r)
        if r["status"] == "active":
            if not r["valid_until"] or r["valid_until"] > now:
                continue  # still true (an end in the future is not a turning point yet)
            # the window the note gave ended (it stopped being true) without anything replacing it
            turning.append({"at": r["valid_until"], "kind": "ended", "text": _claim_line(d), "claim_id": r["claim_id"],
                            "valid_from": r["valid_from"], "valid_until": r["valid_until"], "superseded_by": None,
                            "note_id": r["note_id"], "revision_id": r["revision_id"]})
            continue
        if r["status"] not in _CLOSURE_KINDS:
            raise RuntimeError(f"Then and now does not know the closure kind {r['status']!r} of {r['claim_id']}.")
        kind = _CLOSURE_KINDS[r["status"]]
        if kind is None:
            continue
        text = _claim_line(d)
        point: Dict[str, Any] = {"kind": kind, "claim_id": r["claim_id"], "valid_from": r["valid_from"],
                                 "valid_until": r["valid_until"], "superseded_by": r["superseded_by"],
                                 "reason": r["closed_reason"], "note_id": r["closing_note"],
                                 "revision_id": r["closed_revision_id"]}
        if kind == "supersede":
            # history time: the belief ends where its window ends (the next belief's start), never when the
            # closing revision was applied; the belief that replaced it starts there
            point["at"] = r["valid_until"] or r["closed_at"]
            nxt = s.claim_row(r["superseded_by"]) if r["superseded_by"] else None
            if nxt is not None:
                after = s.claim_dict(nxt)
                point["replaced_by"] = {"claim_id": nxt["claim_id"], "text": _claim_line(after),
                                        "valid_from": nxt["valid_from"], "note_id": nxt["note_id"]}
                text = f"{text} → {_claim_line(after)}"
                if nxt["note_id"] and nxt["observed_at"] == point["at"]:
                    point["note_id"] = nxt["note_id"]  # the note that brought the change
        else:
            point["at"] = r["closed_at"]
        point["text"] = text
        turning.append(point)
    for r in s.q(f"SELECT * FROM claims c WHERE (c.subject_id IN ({q}) OR c.object_id IN ({q})) AND"
                 f" c.disputed_with_json!='[]' AND c.certainty='disputed' AND {_NOT_REINTERPRETED}", [*members, *members]):
        turning.append({"at": r["observed_at"], "kind": "dispute", "text": _claim_line(s.claim_dict(r)),
                        "claim_id": r["claim_id"], "note_id": r["note_id"], "revision_id": r["revision_id"]})
    for r in s.q(f"SELECT * FROM merges WHERE from_id IN ({q}) OR into_id IN ({q})", [*members, *members]):
        turning.append({"at": r["at"], "kind": "merge", "text": f"{r['from_id']} is {r['into_id']}",
                        "note_id": (s.revision_row(r["revision_id"]) or {"note_id": None})["note_id"],
                        "revision_id": r["revision_id"]})
    turning.sort(key=lambda t: (t["at"] or "", t["kind"], t.get("claim_id") or ""))
    payload = {
        "entity_id": eid,
        "first_seen": first_at,
        "earliest": earliest,
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
