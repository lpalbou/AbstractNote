"""Proposed matches and disagreements for a person to look at (``GET /note/review``), and the confirm action.

Every item comes from stored records, never from reading text (ADR 0004):

- ``close_match`` (rule ``unsure_match``): the processor answered ``unsure`` for a mention, so it created a new
  entry and recorded an active ``skos:closeMatch`` claim from it to the candidate (``process/revise.py``). Shown
  while the claim is active and the two are still separate entries.
- ``merge`` (rule ``merged_by_model``): an ``entity.merge`` the processor made on its own, still in force and not
  confirmed by you. Merges you confirm here are never listed.
- ``contradiction`` (rule ``disputed_pair``): two active claims that a note marked as disagreeing
  (``claim.add`` with ``disputes``, the model's ``contradicts`` decision). Both are kept until one is withdrawn.

Actions: ``confirm`` (``POST /note/review/{id}/confirm``) records a user-action revision with action
``match.confirm``: for a close match it merges the new entry into the candidate and supersedes the close-match
claim; for a merge it records that you keep it (no op changes the graph). The other actions are the existing
endpoints: ``unmerge`` a merge, ``keep_apart`` (retract the close-match claim) and ``retract`` one side of a
disagreement.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .. import ids
from ..store.notes import _uj, clamp_limit

CLOSE_MATCH = "skos:closeMatch"  # the predicate process/revise.py records for an unsure match
CONFIRM_ACTION = "match.confirm"
SIDE_NOTES = 3


def review_id(kind: str, key: str) -> str:
    return "rq_" + ids.digest("review", kind, key)[:26]


def _entity_side(nb: Any, eid: str) -> Dict[str, Any]:
    s = nb.store
    row = s.entity_row(eid)
    members = s.merged_members(eid)
    q = ",".join("?" for _ in members)
    notes = [r[0] for r in s.q(f"SELECT en.note_id FROM entity_notes en JOIN notes n ON n.note_id=en.note_id WHERE"
                               f" en.entity_id IN ({q}) AND n.archived_at IS NULL GROUP BY en.note_id"
                               f" ORDER BY MAX(en.at) DESC LIMIT ?", [*members, SIDE_NOTES])]
    return {"type": "entity", "id": eid, "name": row["name"] if row else eid, "kind": row["kind"] if row else None,
            "note_ids": notes}


def _claim_side(nb: Any, row: Any) -> Dict[str, Any]:
    from .changes import claim_text

    return {"type": "claim", "id": row["claim_id"], "name": claim_text(nb, row), "kind": None,
            "note_ids": [row["note_id"]] if row["note_id"] else []}


def _evidence(first: Optional[str], sides: List[Dict[str, Any]]) -> List[str]:
    out: List[str] = []
    for nid in [first, *[n for side in sides for n in side["note_ids"]]]:
        if nid and nid not in out:
            out.append(nid)
    return out


def _act(action: str, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
    return {"action": action, "method": "POST", "path": path, "body": body}


def _confirmed(nb: Any) -> set:
    return {r[0] for r in nb.store.q("SELECT target FROM revisions WHERE action=? AND applied_index=1",
                                     (CONFIRM_ACTION,))}


def _close_matches(nb: Any) -> List[Dict[str, Any]]:
    s = nb.store
    out = []
    for r in s.q("SELECT * FROM claims WHERE predicate=? AND status='active' AND object_id IS NOT NULL"
                 " ORDER BY recorded_at DESC, claim_id", (CLOSE_MATCH,)):
        a, b = s.canonical(r["subject_id"]), s.canonical(r["object_id"])
        if a == b:
            continue  # joined since: nothing left to decide
        if any((s.entity_row(x) is not None and s.entity_row(x)["retired_revision_id"]) for x in (a, b)):
            continue  # ADR 0008: an entry a re-understanding retired (its reading now uses the other one)
        rid = review_id("close_match", r["claim_id"])
        sides = [_entity_side(nb, a), _entity_side(nb, b)]
        out.append({
            "review_id": rid, "kind": "close_match", "why": "unsure_match", "reason": None, "at": r["recorded_at"],
            "sides": sides, "evidence_note_ids": _evidence(r["note_id"], sides),
            "actions": [_act("confirm", f"/note/review/{rid}/confirm", {}),
                        _act("keep_apart", f"/note/claims/{r['claim_id']}/retract", {"reason": None})],
            "_target": {"claim_id": r["claim_id"], "from_id": a, "into_id": b},
        })
    return out


def _merges(nb: Any, confirmed: set) -> List[Dict[str, Any]]:
    s = nb.store
    out = []
    for m in s.q("SELECT m.*, r.recorded_at, r.note_id AS cause_note_id, r.action FROM merges m JOIN revisions r ON"
                 " r.revision_id=m.revision_id WHERE m.undone_at IS NULL AND r.applied_index=1"
                 " ORDER BY r.recorded_at DESC, m.from_id"):
        if m["action"] == CONFIRM_ACTION:
            continue  # a merge you made here needs no review
        row = s.entity_row(m["from_id"])
        if row is None or row["merged_into"] != m["into_id"]:
            continue  # merged elsewhere since
        rid = review_id("merge", f"{m['from_id']}|{m['into_id']}|{m['revision_id']}")
        if rid in confirmed:
            continue
        sides = [_entity_side(nb, m["from_id"]), _entity_side(nb, m["into_id"])]
        out.append({
            "review_id": rid, "kind": "merge", "why": "merged_by_model", "reason": m["reason"] or None,
            "at": m["recorded_at"], "sides": sides, "evidence_note_ids": _evidence(m["cause_note_id"], sides),
            "actions": [_act("confirm", f"/note/review/{rid}/confirm", {}),
                        _act("unmerge", f"/note/entities/{m['into_id']}/unmerge",
                             {"from_id": m["from_id"], "reason": ""})],
            "_target": {"from_id": m["from_id"], "into_id": m["into_id"], "revision_id": m["revision_id"]},
        })
    return out


def _contradictions(nb: Any) -> List[Dict[str, Any]]:
    s = nb.store
    out = []
    seen = set()
    for r in s.q("SELECT * FROM claims WHERE status='active' AND disputed_with_json!='[]'"
                 " ORDER BY recorded_at DESC, claim_id"):
        for other in _uj(r["disputed_with_json"], []) or []:
            pair = tuple(sorted((r["claim_id"], other)))
            if pair in seen:
                continue
            o = s.claim_row(other)
            if o is None or o["status"] != "active":
                continue
            seen.add(pair)
            first, second = sorted((r, o), key=lambda x: (x["observed_at"], x["claim_id"]))
            rid = review_id("contradiction", "|".join(pair))
            sides = [_claim_side(nb, first), _claim_side(nb, second)]
            out.append({
                "review_id": rid, "kind": "contradiction", "why": "disputed_pair", "reason": None,
                "at": max(r["recorded_at"], o["recorded_at"]), "sides": sides,
                "evidence_note_ids": _evidence(second["note_id"], sides),
                "actions": [_act("retract", f"/note/claims/{side['id']}/retract", {"reason": None}) for side in sides],
                "_target": {"claim_ids": list(pair)},
            })
    return out


def _all(nb: Any) -> List[Dict[str, Any]]:
    items = _close_matches(nb) + _merges(nb, _confirmed(nb)) + _contradictions(nb)
    items.sort(key=lambda it: (it["at"] or "", it["review_id"]), reverse=True)
    return items


def _public(item: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in item.items() if not k.startswith("_")}


def review(nb: Any, limit: int = 50) -> Dict[str, Any]:
    """``{items: [ReviewItem], next_cursor: null}``, newest first (docs/api.md, ``GET /note/review``)."""
    return {"items": [_public(it) for it in _all(nb)[: clamp_limit(limit, 50, 500)]], "next_cursor": None}


def review_item(nb: Any, rid: str) -> Optional[Dict[str, Any]]:
    for it in _all(nb):
        if it["review_id"] == rid:
            return it
    return None


def confirm(nb: Any, rid: str, account: Optional[str], *, client: str = "web",
            occurred_at: Optional[str] = None) -> Dict[str, Any]:
    """Confirm a proposed match: a user-action revision ``match.confirm`` targeting the review id."""
    item = review_item(nb, rid)
    if item is None:
        raise ValueError("That match is not waiting for review.")
    s = nb.store
    at = ids.normalize_time(occurred_at) if occurred_at else ids.utc_now()
    target = item["_target"]
    names = [side["name"] for side in item["sides"]]
    if item["kind"] == "close_match":
        ops = [{"op": "entity.merge", "payload": {"from_id": target["from_id"], "into_id": target["into_id"],
                                                  "reason": "You confirmed they are the same."}},
               {"op": "claim.supersede", "payload": {"claim_id": target["claim_id"], "by_claim_id": None,
                                                     "reason": "confirmed as the same", "valid_until": at}}]
        summary = f"Joined {names[0]} into {names[1]}: you confirmed they are the same."
    elif item["kind"] == "merge":
        if s.entity_row(target["from_id"])["merged_into"] != target["into_id"]:
            raise ValueError("That match is not waiting for review.")
        ops = []
        summary = f"You confirmed that {names[0]} and {names[1]} are the same."
    else:
        raise ValueError("A disagreement is settled by retracting one of its two statements.")
    out = nb._user_revision(CONFIRM_ACTION, rid, ops, account, summary, client=client, occurred_at=at)
    return {**out, "review_id": rid}
