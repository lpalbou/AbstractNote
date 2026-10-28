"""Deep changes reach the ideas that relied on them (ADR 0008 §4a, docs/data-model.md "Re-understanding").

A re-understanding (``note.reinterpret``) is the one place where two readings of the same note can be compared, so
it is where a change of meaning is measured. For every entity the two readings touched, :func:`measure` compares the
note's OLD reading with its NEW one and records four signals (each in [0, 1]) in ``idea_changes``:

- ``kind_change`` (K): 1 when the re-understanding retired, created, joined or separated the entity;
- ``summary_distance`` (d): the cosine distance between the summary of the entity the old reading wrote and the one
  the new reading wrote (the notebook's pinned embedder; null when either reading wrote none: ``summary_basis``
  ``none``, and the signal is 0), normalised between :attr:`PropagationTuning.SUMMARY_NOISE` and
  :attr:`PropagationTuning.SUMMARY_DIFFERENT`;
- ``claim_share`` (C): the statements on the entity the re-understanding added, closed, re-opened, repointed or
  changed in certainty or stance, over the statements it had at the note's date before plus those added;
- ``relation_share`` (R): the Jaccard distance between its relations ``(predicate, other entity)`` before and after.

The magnitude is ``max(K, s, C, R)``; the change is *deep* when any signal crosses its threshold. Only a deep change
spreads (:func:`spread`), one hop per re-understanding, over the statements between entities: the impact carried to
a proximal entity F is ``min(magnitude, carried) * DECAY * reliance(E -> F)`` where the reliance is F's statements
with E (weighted by recency) over all of F's relations, and nothing under the floor is marked (counted). The later
notes that used F are marked ``propagated`` (with the source, the impact and the hops) through the same marks as ADR
0008 §4; a summary-only change is seen only by the notes whose date falls in that summary's window. A re-reading
never passes on more than it was given (``carried`` = the impact of the mark that took it), each entity spreads at
most once per pass, and the impact shrinks geometrically, so cycles damp out (at most
``floor(log(FLOOR) / log(DECAY))`` hops). The model decides at each re-reading: an unchanged one writes no revision,
so nothing is measured and the spread stops there.

Numbers decide only what is proximal and when nothing needs a call (ADR 0004); they never steer an answer (the model
never sees an impact).
"""

from __future__ import annotations

import hashlib
import heapq
import json
import math
import time
from bisect import bisect_right
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .. import ids


@dataclass(frozen=True)
class PropagationTuning:
    """Every dial of propagation, in one place (each has a consumer and a test that goes red without it).

    Measured calibration (docs/data-model.md "Re-understanding", untracked/reports/R6.md):

    - notebook-1000 (2026-10-20, qwen3-embedding-0.6b, summaries written before W25): cosine distance between
      consecutive summaries of the SAME entity p10 0.053, p25 0.106, p50 0.245, p75 0.389, p90 0.485, p95 0.545;
      between DIFFERENT entities of one kind p10 0.510, p25 0.557, p50 0.614. 0.10 marks the paraphrase end, 0.55 about
      where a different idea begins, 0.40 flags the top quarter of rewrites (23.5 % same-entity, 97.5 % different).
    - H0 (R6, 2026-10-20, scratch copy of notebook-1000, untracked/reports/R6.md): noise proxy = summary rewrites
      that came with no new statement about the entity (400 pairs): p10 0.048, p25 0.111, p50 0.236, p90 0.453,
      p95 0.511 (all consecutive rewrites: p95 0.516); different entities of one kind (300 pairs): p10 0.518, p25
      0.561, p50 0.609. The pre-registered rule (design §5: keep 0.40 only if noise p95 < 0.40, else p95 + 0.05)
      gives DEEP_SUMMARY_DISTANCE = 0.56, and SUMMARY_DIFFERENT moves to the different-entity median 0.61 (it must
      stay above it). Thirty forced re-readings carrying those rewrites: at 0.40, 9 of 30 (30 %) would raise
      noise marks (67 notes); at 0.56, 3 of 30 (23 notes, 21 from one pre-W25 identity error). Structural noise is
      exact for a restated reading (C = R = 0 over 695 records); live (Qwen3.8-27B) a predicate respelled on
      re-reading (steps_remaining -> remaining_steps) gave C = 2/7 on a 6-statement entity, under 0.5.
    - DECAY / FLOOR: simulation on notebook-1000 (5,258 live entities, 8,694 edges): at 0.5 / 0.1 a random entity
      reaches p50 1 / p90 4 proximal ideas, the largest hub (degree 518) 336 at one hop; without a floor the whole
      notebook is reached in two hops.
    """

    DEEP_SUMMARY_DISTANCE: float = 0.56   # d at or above: deep (H0: noise-proxy p95 0.511 + 0.05; design 0.40)
    SUMMARY_NOISE: float = 0.10           # d at or below: a paraphrase (s = 0); noise-proxy p25 0.111
    SUMMARY_DIFFERENT: float = 0.61       # d at or above: another idea (s = 1); different-entity median 0.609
    DEEP_CLAIM_SHARE: float = 0.5         # C at or above: deep
    DEEP_RELATION_SHARE: float = 0.5      # R at or above: deep
    PROPAGATION_DECAY: float = 0.5        # impact kept per hop
    PROPAGATION_FLOOR: float = 0.1        # impact under it: not marked (counted below_floor)
    TIER_A_IMPACT: float = 0.25           # propagated marks at or above it are read with the direct ones
    MAX_PROPAGATION_HOPS: int = 3         # a guard: the floor binds first (0.5^3 * 1 = 0.125, 0.5^4 < 0.1)
    RECENCY_DAYS: float = 180.0           # hyperbolic recency of a relation last stated before the change
    ENDED_CLAIM_WEIGHT: float = 0.5       # an ended or superseded statement counts half in the reliance

    def __post_init__(self) -> None:
        def bad(why: str) -> None:
            raise ValueError(f"Propagation tuning refused: {why}.")

        if not 0.0 < self.SUMMARY_NOISE < self.DEEP_SUMMARY_DISTANCE < self.SUMMARY_DIFFERENT <= 1.0:
            bad("0 < SUMMARY_NOISE < DEEP_SUMMARY_DISTANCE < SUMMARY_DIFFERENT <= 1")
        for name in ("DEEP_CLAIM_SHARE", "DEEP_RELATION_SHARE"):
            if not 0.0 < getattr(self, name) <= 1.0:
                bad(f"{name} must be in (0, 1]")
        if not 0.0 < self.PROPAGATION_DECAY < 1.0:
            bad("PROPAGATION_DECAY must be in (0, 1)")
        if not 0.0 < self.PROPAGATION_FLOOR < 1.0:
            bad("PROPAGATION_FLOOR must be in (0, 1)")
        if not self.PROPAGATION_FLOOR <= self.TIER_A_IMPACT <= 1.0:
            bad("FLOOR <= TIER_A_IMPACT <= 1")
        if not 1 <= int(self.MAX_PROPAGATION_HOPS) <= 6:
            bad("MAX_PROPAGATION_HOPS must be between 1 and 6")
        if not self.RECENCY_DAYS > 0:
            bad("RECENCY_DAYS must be positive")
        if not 0.0 <= self.ENDED_CLAIM_WEIGHT <= 1.0:
            bad("ENDED_CLAIM_WEIGHT must be in [0, 1]")


TUNING = PropagationTuning()

#: Bounds of the block a propagated re-reading's linking packet shows (``process.packets.CHANGED_LABEL``).
CHANGED_SOURCES = 3       # source ideas shown per note (the rest counted)
CHANGED_STATEMENTS = 6    # changed statements shown per source idea (the rest counted)

_CLAIM_OPS_WITH_ID = ("claim.reopen", "claim.repoint", "claim.start_restore", "claim.start_earlier")


# --------------------------------------------------------------------------- small helpers


def _canon_map(c: Any) -> Dict[str, str]:
    """``{entity_id: canonical id}`` for every entity (one query; merges followed in memory)."""
    into = {r[0]: r[1] for r in c.execute("SELECT entity_id, merged_into FROM entities").fetchall()}
    out: Dict[str, str] = {}
    for e in into:
        cur, seen = e, set()
        while into.get(cur) and cur not in seen:
            seen.add(cur)
            cur = into[cur]
        out[e] = cur
    return out


def _members(canon: Dict[str, str], eid: str) -> List[str]:
    return sorted({e for e, k in canon.items() if k == eid} | {eid})


def _qs(n: int) -> str:
    return ",".join("?" for _ in range(n))


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na > 0 and nb > 0 else 0.0


def _days(later: str, earlier: str) -> float:
    return max(0.0, (ids.parse_time(later) - ids.parse_time(earlier)).total_seconds() / 86400.0)


def _name(c: Any, eid: str) -> str:
    r = c.execute("SELECT display_name, name FROM entities WHERE entity_id=?", (eid,)).fetchone()
    return (r[0] or r[1]) if r is not None else eid


def display_name(store: Any, eid: str) -> str:
    """The name a person reads (the surface form the notes wrote), for end-user words."""
    with store.lock:
        return _name(store.conn, eid)


def recency(change_at: str, last_seen: Optional[str], tuning: PropagationTuning = TUNING) -> float:
    """1 when the relation was stated on or after the change date, else ``1 / (1 + days / RECENCY_DAYS)``."""
    if not last_seen or last_seen >= change_at:
        return 1.0
    return 1.0 / (1.0 + _days(change_at, last_seen) / tuning.RECENCY_DAYS)


def _weight(row: Any, tuning: PropagationTuning) -> float:
    if row["status"] in ("retracted", "reinterpreted"):
        return 0.0
    if row["status"] == "superseded" or row["valid_until"]:
        return tuning.ENDED_CLAIM_WEIGHT
    return 1.0


# --------------------------------------------------------------------------- the relation graph


def neighbours(c: Any, canon: Dict[str, str], eid: str, tuning: PropagationTuning = TUNING
               ) -> Dict[str, Dict[str, Any]]:
    """``{F: {strength, last_seen}}``: the live entities a live statement ties to ``eid`` (either direction, merged
    members included; an ended statement weighs :attr:`ENDED_CLAIM_WEIGHT`, a withdrawn one nothing). Statements
    only: a co-mention is no reliance (a note that mentions both used both, so ADR 0008 §4 marks it directly)."""
    from ..store.notes import claim_live_sql

    mem = _members(canon, eid)
    q = _qs(len(mem))
    rows = c.execute(
        f"SELECT c.claim_id, c.subject_id, c.object_id, c.status, c.valid_until, c.observed_at,"
        f" (SELECT MAX(at) FROM claim_reinforcements r WHERE r.claim_id=c.claim_id) AS reinforced_at FROM claims c"
        f" WHERE c.object_id IS NOT NULL AND {claim_live_sql('c')} AND (c.subject_id IN ({q}) OR c.object_id IN ({q}))",
        [*mem, *mem]).fetchall()
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        s, o = canon.get(r["subject_id"], r["subject_id"]), canon.get(r["object_id"], r["object_id"])
        other = o if s == eid else s
        if other == eid:
            continue
        w = _weight(r, tuning)
        if w <= 0:
            continue
        d = out.setdefault(other, {"strength": 0.0, "last_seen": None})
        d["strength"] += w
        seen = max(x for x in (r["observed_at"], r["reinforced_at"]) if x)
        if d["last_seen"] is None or seen > d["last_seen"]:
            d["last_seen"] = seen
    if out:
        live = {r[0] for r in c.execute(f"SELECT entity_id FROM entities WHERE entity_id IN ({_qs(len(out))}) AND"
                                        f" retired_revision_id IS NULL", list(out)).fetchall()}
        out = {k: v for k, v in out.items() if k in live}
    return out


def relation_mass(c: Any, canon: Dict[str, str], eid: str, tuning: PropagationTuning = TUNING) -> float:
    """A(F): the whole relation strength of an entity (the denominator of the reliance on any one neighbour)."""
    return sum(v["strength"] for v in neighbours(c, canon, eid, tuning).values())


def reliance(c: Any, canon: Dict[str, str], source: str, target: str, change_at: str,
             tuning: PropagationTuning = TUNING, *, link: Optional[Dict[str, Any]] = None) -> float:
    """r(E -> F) = a(E, F) * recency / A(F): how much ``target``'s understanding rests on ``source`` (normalised by
    the TARGET: a leaf that is only about E relies on it fully, a hub with hundreds of relations barely relies on one
    neighbour)."""
    if link is None:
        link = neighbours(c, canon, source, tuning).get(target)
    if not link:
        return 0.0
    mass = relation_mass(c, canon, target, tuning)
    if mass <= 0:
        return 0.0
    return min(1.0, link["strength"] * recency(change_at, link["last_seen"], tuning) / mass)


# --------------------------------------------------------------------------- measuring a re-understanding


def _reading_of(c: Any, rid: str) -> Tuple[Any, List[Dict[str, Any]], str, Any]:
    rev = c.execute("SELECT * FROM revisions WHERE revision_id=?", (rid,)).fetchone()
    if rev is None:
        raise ValueError(f"The revision {rid} is not in this notebook.")
    ops = json.loads(rev["ops_json"] or "[]")
    if rev["action"] != "note.reinterpret" or not rev["note_id"]:
        raise ValueError(f"The revision {rid} is not a re-understanding of a note.")
    head = next((op for op in ops if op.get("op") == "note.reinterpret"), None)
    if head is not None:
        prev = head["payload"]["supersedes"]
    else:  # a re-understanding written without its first op: the old reading is the interpretation before it
        row = c.execute("SELECT revision_id FROM revisions WHERE note_id=? AND applied_index=1 AND abandoned=0 AND"
                        " (action IS NULL OR action='note.reinterpret') AND seq<? ORDER BY seq DESC LIMIT 1",
                        (rev["note_id"], rev["seq"])).fetchone()
        prev = row[0] if row else None
    note = c.execute("SELECT * FROM notes WHERE note_id=?", (rev["note_id"],)).fetchone()
    return rev, ops, prev, note


def measure(nb: Any, rid: str, tuning: PropagationTuning = TUNING) -> List[Dict[str, Any]]:
    """The change records of one applied re-understanding ``rid``: one per (canonical) entity its old or new reading
    touched, ``{entity_id, kind_change, summary_distance, summary_basis, claim_share, relation_share,
    support_claims, support_relations, magnitude, deep, summary_only}``. Reads and embeds; writes nothing but
    vectors (the summary texts, cached by key)."""
    s = nb.store
    c = s.conn
    with s.lock:
        rev, ops, prev, note = _reading_of(c, rid)
        t = note["occurred_at"]
        canon = _canon_map(c)
        cn = lambda e: canon.get(e, e)  # noqa: E731

        added: Set[str] = set()
        revised: Set[str] = set()
        other_ops: Set[str] = set()
        kind: Set[str] = set()
        touched: Set[str] = set()
        for op in ops:
            name, p = op.get("op"), op.get("payload") or {}
            for k in ("entity_id", "subject_id", "object_id", "from_id", "into_id"):
                if p.get(k):
                    touched.add(p[k])
            if name == "claim.add":
                added.add(p["claim_id"])
            elif name == "claim.revise":
                was = p.get("previous") or {}
                if (p.get("certainty"), p.get("stance") or "assert") != (was.get("certainty"),
                                                                       was.get("stance") or "assert"):
                    revised.add(p["claim_id"])
            elif name in _CLAIM_OPS_WITH_ID:
                other_ops.add(p["claim_id"])
            if name in ("entity.retire", "entity.create"):
                kind.add(p["entity_id"])
            elif name == "entity.merge":
                kind.update((p["from_id"], p["into_id"]))
            elif name == "entity.unmerge":
                kind.update((p["entity_id"], p["from_id"]))
        closed = {r[0] for r in c.execute("SELECT claim_id FROM claims WHERE closed_revision_id=?", (rid,)).fetchall()}
        reopened = {x for x in other_ops if any(o.get("op") == "claim.reopen" and (o.get("payload") or {}).get(
            "claim_id") == x for o in ops)}
        changed = added | closed | revised | other_ops
        if changed:
            for r in c.execute(f"SELECT subject_id, object_id FROM claims WHERE claim_id IN ({_qs(len(changed))})",
                               list(changed)).fetchall():
                touched.update(x for x in r if x)
        touched.update(r[0] for r in c.execute("SELECT entity_id FROM entity_notes_removed WHERE removed_revision_id=?",
                                               (rid,)).fetchall())
        entities = sorted({cn(e) for e in touched if e in canon})
        kinds = {cn(e) for e in kind if e in canon}

        rows: List[Dict[str, Any]] = []
        texts: List[str] = []
        for eid in entities:
            mem = _members(canon, eid)
            q = _qs(len(mem))
            claims = c.execute(f"SELECT * FROM claims WHERE subject_id IN ({q}) OR object_id IN ({q})",
                               [*mem, *mem]).fetchall()
            before = [r for r in claims if r["claim_id"] in closed or (
                r["status"] == "active" and r["observed_at"] <= t and r["claim_id"] not in added
                and r["claim_id"] not in reopened)]
            after = [r for r in claims if r["status"] == "active" and r["observed_at"] <= t]
            mine_added = [r for r in claims if r["claim_id"] in added]
            n_changed = sum(1 for r in claims if r["claim_id"] in changed)
            support = len(before) + len(mine_added)
            share = min(1.0, n_changed / support) if support else 0.0

            def rels(rs: Iterable[Any]) -> Set[Tuple[str, str, str]]:
                out = set()
                for r in rs:
                    if not r["object_id"]:
                        continue
                    sub, obj = cn(r["subject_id"]), cn(r["object_id"])
                    out.add((r["predicate"], obj, "out") if sub == eid else (r["predicate"], sub, "in"))
                return out

            nb_, na_ = rels(before), rels(after)
            union = nb_ | na_
            rel = (len(nb_ ^ na_) / len(union)) if union else 0.0
            old = c.execute(f"SELECT summary FROM entity_summaries WHERE revision_id=? AND entity_id IN ({q})"
                            f" ORDER BY at DESC LIMIT 1", [prev, *mem]).fetchone()
            new = c.execute(f"SELECT summary FROM entity_summaries WHERE revision_id=? AND entity_id IN ({q})"
                            f" ORDER BY at DESC LIMIT 1", [rid, *mem]).fetchone()
            pair = (old[0], new[0]) if old is not None and new is not None else None
            if pair:
                texts.extend(pair)
            rows.append({"entity_id": eid, "kind_change": 1.0 if eid in kinds else 0.0, "pair": pair,
                         "claim_share": round(share, 4), "relation_share": round(rel, 4),
                         "support_claims": support, "support_relations": len(union)})
    if texts:
        nb.require_embedder()  # never measure (or spread) without the notebook's embedder: it fails loudly
        keys = nb.cache.ensure(texts)
        vec = {k: nb.cache.get(k) for k in keys}
        by_text = dict(zip(texts, keys))
    for r in rows:
        pair = r.pop("pair")
        if pair:
            a, b = vec[by_text[pair[0]]], vec[by_text[pair[1]]]
            if a is None or b is None:
                raise RuntimeError("A summary was embedded but its vector is missing; the change cannot be measured.")
            d = 0.0 if pair[0] == pair[1] else max(0.0, min(1.0, 1.0 - _cosine(a, b)))
            r["summary_distance"], r["summary_basis"] = round(d, 4), "both"
            sig = (d - tuning.SUMMARY_NOISE) / (tuning.SUMMARY_DIFFERENT - tuning.SUMMARY_NOISE)
            s_norm = max(0.0, min(1.0, sig))
        else:
            r["summary_distance"], r["summary_basis"], s_norm = None, "none", 0.0
        r["summary_signal"] = round(s_norm, 4)
        r["magnitude"] = round(max(r["kind_change"], s_norm, r["claim_share"], r["relation_share"]), 4)
        structural = (r["kind_change"] >= 1.0 or r["claim_share"] >= tuning.DEEP_CLAIM_SHARE
                      or r["relation_share"] >= tuning.DEEP_RELATION_SHARE)
        summary_deep = r["summary_distance"] is not None and r["summary_distance"] >= tuning.DEEP_SUMMARY_DISTANCE
        r["deep"] = bool(structural or summary_deep)
        r["summary_only"] = bool(summary_deep and not structural)
    return rows


# --------------------------------------------------------------------------- spreading


def carried_of(c: Any, note_id: str) -> Tuple[float, int, Optional[str]]:
    """``(carried, hops, pass_id)`` of the re-understanding of ``note_id`` being applied: the marks a pass took for it
    (or, read by hand, the marks it had): a direct mark carries 1.0 at hop 0, a propagated one its impact and hops
    (the strongest). Without any mark (a person asked): 1.0 at hop 0."""
    rows = c.execute("SELECT reason_code, impact, hops, pass_id FROM reinterpret_marks WHERE note_id=? AND"
                     " consumed_at IS NULL", (note_id,)).fetchall()
    taken = [r for r in rows if r["pass_id"]]
    use = taken or rows
    if not use:
        return 1.0, 0, None
    pass_id = taken[0]["pass_id"] if taken else None
    best = max(use, key=lambda r: (1.0 if r["reason_code"] == "direct" else float(r["impact"]),
                                   -int(r["hops"] or 0)))
    if best["reason_code"] == "direct":
        return 1.0, 0, pass_id
    return float(best["impact"]), int(best["hops"] or 0), pass_id


def visible(c: Any, store: Any, entity_id: str, rid: str, record: Dict[str, Any], note_at: str) -> bool:
    """Is the change seen at the marked note's date (ADR 0008 §2)? A change of statements, relations or identity is
    dated at the re-read note's date, so every later note sees it; a summary-only change only while that summary is
    the entity's known-then one at the note's date (the note lies before the next note that described it)."""
    if not record.get("summary_only"):
        return True
    cur = store.current_summary_row(entity_id, at=note_at, conn=c)
    return cur is not None and cur["revision_id"] == rid


def _reason(c: Any, source: str, via: str, day: str) -> str:
    e, f = _name(c, source), _name(c, via)
    if source == via:
        return f"{e} is understood differently since a note of {day} was read again."
    return f"{e} is understood differently since a note of {day} was read again, and {f} relies on it."


def spread(nb: Any, c: Any, rid: str, records: List[Dict[str, Any]], *, carried: float, hops: int,
           pass_id: Optional[str], tuning: PropagationTuning = TUNING) -> Dict[str, Any]:
    """One hop from each deep change of ``rid`` (in the caller's transaction): mark the later notes that used a
    proximal entity (``propagated``), fill each record's ``spread`` and ``spread_json``. Returns the totals."""
    s = nb.store
    _rev, _ops, _prev, note = _reading_of(c, rid)
    key = (note["occurred_at"], note["received_at"], note["note_id"])
    t = note["occurred_at"]
    canon = _canon_map(c)
    touched = {r["entity_id"] for r in records}  # the re-read note's own entities: ADR 0008 §4 marks their users
    totals = {"marked": 0, "upgraded": 0, "below_floor": 0, "hop_cut": 0, "invisible": 0, "already_spread": 0,
              "reached": 0, "sources": []}
    marks: List[Dict[str, Any]] = []
    at_of: Dict[str, str] = {}
    for rec in sorted(records, key=lambda r: (-r["magnitude"], r["entity_id"])):
        rec["spread"] = False
        if not rec["deep"]:
            rec["spread_json"] = None
            continue
        eid = rec["entity_id"]
        if pass_id and c.execute("SELECT 1 FROM idea_changes WHERE pass_id=? AND entity_id=? AND spread=1 AND"
                                 " revision_id!=?", (pass_id, eid, rid)).fetchone() is not None:
            totals["already_spread"] += 1  # each entity spreads at most once per pass (the visited set)
            rec["spread_json"] = json.dumps({"skipped": "already spread in this pass"})
            continue
        m0 = min(rec["magnitude"], carried)  # the loop-breaker: a re-reading never passes on more than it was given
        info: Dict[str, Any] = {"m0": round(m0, 4), "capped_by": round(max(0.0, rec["magnitude"] - m0), 4),
                                "reached": [], "below_floor": 0, "hop_cut": 0, "invisible": 0}
        for f, link in sorted(neighbours(c, canon, eid, tuning).items()):
            if f in touched:
                continue
            r = reliance(c, canon, eid, f, t, tuning, link=link)
            p = m0 * tuning.PROPAGATION_DECAY * r
            if p < tuning.PROPAGATION_FLOOR:
                info["below_floor"] += 1
                continue
            if hops + 1 > tuning.MAX_PROPAGATION_HOPS:
                info["hop_cut"] += 1
                continue
            got: List[str] = []
            for nid in sorted(s.later_dependents(c, [f], after=key, exclude=[note["note_id"]])):
                if nid not in at_of:
                    at_of[nid] = c.execute("SELECT occurred_at FROM notes WHERE note_id=?", (nid,)).fetchone()[0]
                if not visible(c, s, eid, rid, rec, at_of[nid]):
                    info["invisible"] += 1
                    continue
                got.append(nid)
                marks.append({"note_id": nid, "reason": _reason(c, eid, f, t[:10]), "by_note_id": note["note_id"],
                              "by_revision_id": rid, "entities": [f], "from_at": t, "reason_code": "propagated",
                              "source_entity_id": eid, "source_revision_id": rid, "impact": round(p, 4),
                              "hops": hops + 1})
            info["reached"].append({"entity_id": f, "impact": round(p, 4), "reliance": round(r, 4), "notes": got})
        rec["spread"] = True
        rec["spread_json"] = json.dumps(info, sort_keys=True)
        totals["sources"].append(eid)
        for k in ("below_floor", "hop_cut", "invisible"):
            totals[k] += info[k]
        totals["reached"] += len(info["reached"])
    if marks:
        upgraded: List[str] = []
        new = s.mark_reinterpret(c, marks, ids.utc_now(), upgraded)
        totals["marked"], totals["upgraded"] = len(set(new)), len(set(upgraded))
    return totals


def propagate(nb: Any, rid: str, tuning: PropagationTuning = TUNING) -> Dict[str, Any]:
    """Measure an applied re-understanding and spread its deep changes (``Notebook._finish``): the change records
    and the marks are written in one transaction. Idempotent by the records' key (a re-understanding already
    measured returns the stored totals), so a crash between the apply and the spread is completed by
    ``Notebook.recover``."""
    s = nb.store
    have = s.q("SELECT * FROM idea_changes WHERE revision_id=?", (rid,))
    if have:
        return {"records": len(have), "deep": sum(1 for r in have if r["deep"]), "already_measured": True}
    rev = s.revision_row(rid)
    with s.lock:
        carried, hops, pass_id = carried_of(s.conn, rev["note_id"])
    records = measure(nb, rid, tuning)
    note = s.note_row(rev["note_id"])
    now = ids.utc_now()
    with s.tx() as c:
        if c.execute("SELECT 1 FROM idea_changes WHERE revision_id=? LIMIT 1", (rid,)).fetchone() is not None:
            return {"records": len(records), "already_measured": True}
        totals = spread(nb, c, rid, records, carried=carried, hops=hops, pass_id=pass_id, tuning=tuning)
        model = getattr(nb.cache, "model_id", None)
        for r in records:
            c.execute(
                "INSERT OR IGNORE INTO idea_changes(revision_id, entity_id, note_id, as_of, pass_id, kind_change,"
                " summary_distance, summary_basis, claim_share, relation_share, support_claims, support_relations,"
                " magnitude, deep, summary_only, carried, hops, embedder_model, spread, spread_json, recorded_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (rid, r["entity_id"], rev["note_id"], note["occurred_at"], pass_id, r["kind_change"],
                 r["summary_distance"], r["summary_basis"], r["claim_share"], r["relation_share"],
                 r["support_claims"], r["support_relations"], r["magnitude"], int(r["deep"]), int(r["summary_only"]),
                 carried, hops, model, int(r.get("spread") or 0), r.get("spread_json"), now))
    return {"records": len(records), "deep": sum(1 for r in records if r["deep"]), "carried": carried, "hops": hops,
            **{k: v for k, v in totals.items()}}


def changes_of(store: Any, revision_id: str) -> List[Dict[str, Any]]:
    """The stored change records of a re-understanding (``idea_changes``), spread decoded."""
    out = []
    for r in store.q("SELECT * FROM idea_changes WHERE revision_id=? ORDER BY magnitude DESC, entity_id",
                     (revision_id,)):
        d = dict(r)
        d["spread_json"] = json.loads(d["spread_json"]) if d.get("spread_json") else None
        d["deep"], d["summary_only"], d["spread"] = bool(d["deep"]), bool(d["summary_only"]), bool(d["spread"])
        out.append(d)
    return out


# --------------------------------------------------------------------------- the no-call check and the packet


def related_sources(store: Any, note_id: str) -> List[Tuple[str, str]]:
    """``[(source entity, source re-understanding)]`` of the note's waiting propagated marks (taken or not)."""
    out: List[Tuple[str, str]] = []
    for r in store.q("SELECT source_entity_id, source_revision_id FROM reinterpret_marks WHERE note_id=? AND"
                     " consumed_at IS NULL AND reason_code='propagated' AND source_entity_id IS NOT NULL ORDER BY"
                     " impact DESC, mark_id", (note_id,)):
        pair = (store.canonical(r[0]), r[1])
        if pair not in out:
            out.append(pair)
    return out


def related_state(store: Any, entity_id: str, as_of: str) -> Dict[str, Any]:
    """What a note dated ``as_of`` can know of an idea it relies on: the revision of its known-then summary and a hash
    of its statements known then (live, observed on or before ``as_of``). Recorded as ``related`` in the dependency
    record of a re-reading taken through a propagated mark; the no-call check compares it."""
    from ..store.notes import claim_live_sql

    cur = store.current_summary_row(entity_id, at=as_of)
    mem = store.merged_members(store.canonical(entity_id))
    q = _qs(len(mem))
    rows = store.q(f"SELECT c.subject_id, c.predicate, c.object_id, c.object_literal, c.certainty, c.stance, c.status,"
                   f" c.valid_until FROM claims c WHERE {claim_live_sql('c')} AND c.observed_at<=? AND (c.subject_id IN"
                   f" ({q}) OR c.object_id IN ({q}))", [as_of, *mem, *mem])
    keys = sorted("|".join(str(x) for x in (store.canonical(r[0]), r[1], store.canonical(r[2]) if r[2] else r[3],
                                            r[4], r[5], r[6], r[7])) for r in rows)
    return {"summary": cur["revision_id"] if cur is not None else None,
            "claims": hashlib.sha1("\n".join(keys).encode("utf-8")).hexdigest()[:16]}


def _statement(store: Any, r: Any) -> str:
    obj = display_name(store, r["object_id"]) if r["object_id"] else "“" + str(r["object_literal"] or "") + "”"
    return f"{display_name(store, r['subject_id'])} {r['predicate']} {obj} · {r['certainty']}"


def understood_differently(nb: Any, note_id: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """The "Understood differently since this note was read" block of a note taken through a propagated mark, and
    the ``related`` record for its digest: per source idea ``{entity_id, name, kind, summary_now, summary_before,
    no_longer: [statement], now: [statement], omitted}`` (bounded; the rest counted). Both empty without one."""
    s = nb.store
    row = s.note_row(note_id)
    pairs = related_sources(s, note_id)
    if row is None or not pairs:
        return [], {}
    as_of = row["occurred_at"]
    cur = s.current_interpretation(note_id)
    seen = {}
    if cur:
        ctx = s.reading_record(note_id, cur) or {}
        seen = ctx.get("summaries") or {}
    blocks: List[Dict[str, Any]] = []
    related: Dict[str, Any] = {}
    for eid, rid in pairs:
        related[eid] = related_state(s, eid, as_of)
        if len(blocks) >= CHANGED_SOURCES:
            continue
        e = s.entity_row(eid)
        now_row = s.current_summary_row(eid, at=as_of)
        before = None
        if seen.get(eid):
            b = s.q1("SELECT summary FROM entity_summaries WHERE revision_id=? AND entity_id=?", (seen[eid], eid))
            before = b[0] if b else None
        if before is None and rid:
            sup = s.q1("SELECT revision_id FROM revision_supersessions WHERE superseded_by=?", (rid,))
            if sup is not None:
                mem = s.merged_members(eid)
                b = s.q1(f"SELECT summary FROM entity_summaries WHERE revision_id=? AND entity_id IN ({_qs(len(mem))})"
                         f" LIMIT 1", [sup[0], *mem])
                before = b[0] if b else None
        mem = s.merged_members(eid)
        q = _qs(len(mem))
        closed = s.q(f"SELECT * FROM claims WHERE closed_revision_id=? AND (subject_id IN ({q}) OR object_id IN ({q}))"
                     f" ORDER BY observed_at", [rid, *mem, *mem]) if rid else []
        added = s.q(f"SELECT * FROM claims WHERE revision_id=? AND status='active' AND (subject_id IN ({q}) OR"
                    f" object_id IN ({q})) ORDER BY observed_at", [rid, *mem, *mem]) if rid else []
        lines_old = [_statement(s, r) for r in closed]
        lines_new = [_statement(s, r) for r in added]
        keep_old = lines_old[:CHANGED_STATEMENTS]
        keep_new = lines_new[:max(0, CHANGED_STATEMENTS - len(keep_old))]
        blocks.append({"entity_id": eid, "name": (e["display_name"] or e["name"]) if e is not None else eid,
                       "kind": e["kind"] if e is not None else "thing",
                       "summary_now": now_row["summary"] if now_row is not None else None,
                       "summary_before": before if before != (now_row["summary"] if now_row is not None else None)
                       else None, "no_longer": keep_old, "now": keep_new,
                       "omitted": len(lines_old) + len(lines_new) - len(keep_old) - len(keep_new)})
    if len(pairs) > CHANGED_SOURCES:
        blocks.append({"more_sources": len(pairs) - CHANGED_SOURCES})
    return blocks, related


# --------------------------------------------------------------------------- the bound shown before the run


_bound_cache: Dict[str, Tuple[Any, Dict[str, Any]]] = {}


def may_follow(nb: Any, tuning: PropagationTuning = TUNING) -> Dict[str, Any]:
    """``{may_follow, by_source: [{entity_id, name, notes}]}``: an upper bound of the notes that propagated marks may
    add when the waiting notes are understood again, readable before the run (ADR 0007, ADR 0008 §4a). A numeric
    dry run, no model: every idea a waiting note uses is assumed to change with magnitude 1 (recency taken as 1),
    the impact spreads over the statements to the fixed point (strongest path, decay, floor, hop guard), and the
    later notes that used a reached idea, after the waiting note in history order and not waiting already, are
    counted once. A bound over the ideas the waiting notes use now, never a promise."""
    s = nb.store
    stamp = (s.q1("SELECT COALESCE(MAX(seq), 0) FROM revisions")[0],
             tuple(s.q1("SELECT COALESCE(MAX(mark_id), 0), COUNT(consumed_at), COUNT(pass_id) FROM"
                        " reinterpret_marks")))
    hit = _bound_cache.get(str(nb.root))
    if hit is not None and hit[0] == stamp:
        return json.loads(json.dumps(hit[1]))
    t0 = time.time()
    pend = s.pending_marks()
    out: Dict[str, Any] = {"may_follow": 0, "by_source": []}
    if pend:
        from ..store.notes import claim_live_sql

        with s.lock:
            c = s.conn
            canon = _canon_map(c)
            live = {r[0] for r in c.execute("SELECT entity_id FROM entities WHERE merged_into IS NULL AND"
                                            " retired_revision_id IS NULL").fetchall()}
            adj: Dict[str, Dict[str, float]] = {}
            for r in c.execute(f"SELECT subject_id, object_id, status, valid_until FROM claims c WHERE object_id IS NOT"
                               f" NULL AND {claim_live_sql('c')}").fetchall():
                a, b = canon.get(r[0], r[0]), canon.get(r[1], r[1])
                w = _weight(r, tuning)
                if a == b or w <= 0 or a not in live or b not in live:
                    continue
                adj.setdefault(a, {})[b] = adj.get(a, {}).get(b, 0.0) + w
                adj.setdefault(b, {})[a] = adj.get(b, {}).get(a, 0.0) + w
            mass = {k: sum(v.values()) for k, v in adj.items()}
            users: Dict[str, List[Tuple[Tuple[str, str, str], str]]] = {}
            for r in c.execute("SELECT x.entity_id, n.note_id, n.occurred_at, n.received_at FROM (SELECT entity_id,"
                               " note_id FROM entity_notes UNION SELECT key AS entity_id, note_id FROM note_dependencies"
                               " WHERE kind='entity') x JOIN notes n ON n.note_id=x.note_id WHERE n.archived_at IS NULL"
                               " AND n.processed_at IS NOT NULL").fetchall():
                users.setdefault(canon.get(r[0], r[0]), []).append(((r[2], r[3], r[1]), r[1]))
            for v in users.values():
                v.sort()
            used: Dict[str, List[str]] = {}
            for p in pend:
                used[p["note_id"]] = sorted({canon.get(e, e) for e in s.used_by(c, p["note_id"])["entity"]})
        waiting = {p["note_id"] for p in pend}
        reach_cache: Dict[str, Dict[str, float]] = {}

        def reach(src: str) -> Dict[str, float]:
            if src in reach_cache:
                return reach_cache[src]
            best: Dict[str, Tuple[float, int]] = {src: (1.0, 0)}
            heap = [(-1.0, 0, src)]
            while heap:
                v, h, x = heapq.heappop(heap)
                v = -v
                if best.get(x, (0.0, 0))[0] > v:
                    continue
                for f, a in (adj.get(x) or {}).items():
                    p = v * tuning.PROPAGATION_DECAY * min(1.0, a / mass[f])
                    if p < tuning.PROPAGATION_FLOOR or h + 1 > tuning.MAX_PROPAGATION_HOPS:
                        continue
                    if p > best.get(f, (0.0, 0))[0]:
                        best[f] = (p, h + 1)
                        heapq.heappush(heap, (-p, h + 1, f))
            reach_cache[src] = {k: v for k, (v, _h) in best.items() if k != src}
            return reach_cache[src]

        found: Set[str] = set()
        by_src: Dict[str, Set[str]] = {}
        for p in pend:
            key = (p["occurred_at"], p["received_at"], p["note_id"])
            mine = set(used[p["note_id"]])
            for e in mine:
                for f in reach(e):
                    if f in mine:
                        continue
                    lst = users.get(f) or []
                    for _k, nid in lst[bisect_right(lst, (key, "￿")):]:
                        if nid in waiting:
                            continue
                        found.add(nid)
                        by_src.setdefault(e, set()).add(nid)
        top = sorted(by_src.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:5]
        out = {"may_follow": len(found),
               "by_source": [{"entity_id": e, "name": display_name(s, e), "notes": len(v)} for e, v in top]}
    out["computed_s"] = round(time.time() - t0, 3)
    _bound_cache[str(nb.root)] = (stamp, out)
    return json.loads(json.dumps(out))


__all__ = ["PropagationTuning", "TUNING", "carried_of", "changes_of", "may_follow", "measure",
           "display_name", "neighbours", "propagate", "recency", "related_sources", "related_state", "reliance", "spread",
           "understood_differently", "visible"]
