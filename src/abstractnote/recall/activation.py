"""The ripple is an activation with a threshold (AbstractMemory ADR 0001 rules 7-8; ADR 0008 §4 as amended).

A revision that understands a note (for the first time, or again) touches entities. Every later note that USED one
of them (ADR 0008 §4: ``NoteStore.later_dependents``, forward only) is a *candidate*, written as a pending row of
``gate_decisions`` in the projection transaction. This module decides each candidate, right after the projection:

1. **Measure** what the revision changed about each touched entity E (:func:`measure_first` for a first reading,
   :func:`recall.propagation.measure` for a re-reading): K (retired, joined, separated, or its name taken by a new
   idea), s (summary distance standardised between the reader's rewrite noise and "another idea"), C (share of its
   statements added, closed or changed), R (share of its relations replaced). The **strength** of the change is
   ``min(1, (W_K K + W_S s + W_C C + W_R R) / (1 + supporting notes) ** α)``: the entity's mass (the notes that
   already support it) divides it, so a thin change on a hub is irrelevant, unless it is a kind change (K weighs 8).
2. **Potential** of a candidate N = Σ over the entities X it used (reached through E) of
   ``factor * min(strength, cap) * reliance(N, X) * recency(N, X) * conductance(X -> N)`` where ``reliance`` is the
   share of N's own statements that involve X (a statement with X as subject 1, as object 0.5, a bare mention 0.1:
   claims matter much more than mentions), ``recency`` is ``1 / (1 + notes between / RECENCY_NOTES)`` (the notes that
   used X between the change and N: the later N, the more its reading rests on them), ``conductance`` the edge's
   learnt multiplier (rule 8). For a re-reading taken through a mark, ``factor`` is the decay and ``cap`` the mark's
   gated impact (a reconsidered memory never hands on more than it received). The potential **accumulates** over
   every open row of N (several small changes from several entities and several notes add up) until N is read
   again.
3. **Gate** ``g = 1 / (1 + e^-k(potential - θ))``; N is marked (``direct``, impact g) when ``g >= FLOOR``. Every
   decision is stored with its potential, gate and sources (``gate_decisions``), so thresholds are re-tuned from the
   record (:func:`gate_state` ``would_mark_at``) without re-running anything.

Termination (rule 7): a note at most once per pass (the marks' pass exclusion), the loop gain
``DECAY * max reliance * k / 4 < 1`` (refused otherwise by :class:`PropagationTuning`), and the floor. Edges learn
(rule 8): when a marked note is read again, :func:`close_window` raises the conductance of the edges that marked it
by ``CONDUCTANCE_STEP`` if the re-reading changed something, and lowers it if it did not, within
``[CONDUCTANCE_MIN, 1]``. Numbers choose what is reconsidered; the reader decides what it means (ADR 0004).
"""

from __future__ import annotations

import json
import math
from bisect import bisect_left
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .. import ids
from .propagation import (TUNING, PropagationTuning, _canon_map, _cosine, _members, _qs, display_name,  # noqa: F401
                          visible)

#: The signal that dominates a source's strength, in end-user words (the Processing page: "X changed its kind").
SIGNAL_WORDS = {"kind": "changed its kind", "name": "shares its name with a new entry",
                "identity": "shares its exact name with a new entry",
                "summary": "is described differently", "claims": "has new or changed statements",
                "relations": "has new or changed relations", "task": "opened a task a later note closed"}


# --------------------------------------------------------------------------- the gate and its loop gain


def logistic(x: float, tuning: PropagationTuning = TUNING) -> float:
    """The gate: ``1 / (1 + e^-k(x - θ))`` (monotone, smooth; a configuration, not a model)."""
    z = -tuning.GATE_K * (x - tuning.GATE_THETA)
    if z > 60:
        return 0.0
    return 1.0 / (1.0 + math.exp(z))


def threshold(tuning: PropagationTuning = TUNING, theta: Optional[float] = None) -> float:
    """The potential at which the gate reaches the floor (a candidate at or above it is marked)."""
    t = tuning.GATE_THETA if theta is None else theta
    f = tuning.PROPAGATION_FLOOR
    return t - math.log((1.0 - f) / f) / tuning.GATE_K


def loop_gain(tuning: PropagationTuning = TUNING) -> float:
    """``DECAY * max reliance (1) * max gate slope (k / 4)``: what one hop can hand on per unit received (< 1)."""
    return tuning.PROPAGATION_DECAY * 1.0 * tuning.GATE_K / 4.0


def strength(rec: Dict[str, Any], mass: float, tuning: PropagationTuning = TUNING) -> Tuple[float, float, str]:
    """``(raw, strength, dominant signal)`` of a change record: the weighted sum of its standardised signals divided
    by ``mass ** α`` (mass = 1 + the notes that already supported the entity), clamped to [0, 1]."""
    parts = {"kind": tuning.WEIGHT_KIND * float(rec.get("kind_change") or 0.0),
             "summary": tuning.WEIGHT_SUMMARY * float(rec.get("summary_signal") or 0.0),
             "claims": tuning.WEIGHT_CLAIMS * float(rec.get("claim_share") or 0.0),
             "relations": tuning.WEIGHT_RELATIONS * float(rec.get("relation_share") or 0.0)}
    raw = sum(parts.values())
    top = max(parts, key=lambda k: parts[k]) if raw > 0 else "none"
    return raw, min(1.0, raw / max(1.0, mass) ** tuning.MASS_ALPHA), top


# --------------------------------------------------------------------------- what the measures read


def _seq_of(c: Any, rid: str) -> int:
    r = c.execute("SELECT seq FROM revisions WHERE revision_id=?", (rid,)).fetchone()
    if r is None:
        raise ValueError(f"The revision {rid} is not in this notebook.")
    return int(r[0])


def support_notes(c: Any, canon: Dict[str, str], eid: str, seq: int, exclude: Sequence[str] = ()) -> int:
    """The mass of an entity before revision ``seq``: the distinct notes (other than ``exclude``) that mentioned it
    or made a statement on it (merged members included; a superseded reading's mentions count, it was read then)."""
    mem = _members(canon, eid)
    q = _qs(len(mem))
    rows = c.execute(
        f"SELECT x.note_id FROM (SELECT note_id, revision_id FROM entity_notes WHERE entity_id IN ({q}) UNION"
        f" SELECT note_id, revision_id FROM entity_notes_removed WHERE entity_id IN ({q}) UNION SELECT cl.note_id,"
        f" cl.revision_id FROM claims cl WHERE cl.note_id IS NOT NULL AND (cl.subject_id IN ({q}) OR cl.object_id IN"
        f" ({q}))) x JOIN notes n ON n.note_id=x.note_id WHERE n.archived_at IS NULL AND n.received_at<=(SELECT"
        f" COALESCE(MAX(recorded_at), '9') FROM revisions WHERE seq<=?)", [*mem, *mem, *mem, *mem, seq]).fetchall()
    skip = set(exclude)
    return len({r[0] for r in rows if r[0] not in skip})


def name_signal(ops: Sequence[Dict[str, Any]], eid: str, canon: Dict[str, str]) -> bool:
    """Did the revision give ``eid`` a name another entry answers to: it created it, aliased it, or renamed it (the
    exact-key F2 match of ``later_dependents`` then reached the users of that other entry: its identity is in
    question, a kind-class change)."""
    for op in ops:
        p = op.get("payload") or {}
        if op.get("op") in ("entity.create", "entity.alias", "entity.rename") and \
                canon.get(p.get("entity_id"), p.get("entity_id")) == eid:
            return True
    return False


#: The predicate of a close match kept for review (process/revise.py ``CLOSE_MATCH``): a statement from a new entry to
#: an existing one whose name or alias it matches exactly; review only, never meaning, so no change counts it.
CLOSE_MATCH = "skos:closeMatch"


def identity_events(c: Any, rid: str, canon: Dict[str, str]) -> Dict[str, List[str]]:
    """``{existing entity: [new entries]}``: the exact-name or exact-alias close matches revision ``rid`` recorded
    from an entry it CREATED to one that already existed (lead ruling 2026-10-21 on the Mnemosyne run: an
    "identity in question" change event on the EXISTING entity, K-class, decided per later note by the reader)."""
    rev = c.execute("SELECT ops_json FROM revisions WHERE revision_id=?", (rid,)).fetchone()
    ops = json.loads(rev[0] or "[]") if rev is not None else []
    created = {op["payload"]["entity_id"] for op in ops if op.get("op") == "entity.create"}
    out: Dict[str, List[str]] = {}
    for op in ops:
        p = op.get("payload") or {}
        if op.get("op") != "claim.add" or p.get("predicate") != CLOSE_MATCH or not p.get("object_id"):
            continue
        e, x = p["subject_id"], p["object_id"]
        if e not in created or x in created:
            continue
        e, x = canon.get(e, e), canon.get(x, x)
        if e != x and e not in out.setdefault(x, []):
            out[x].append(e)
    return out


def with_identity(records: Dict[str, Dict[str, Any]], events: Dict[str, List[str]]) -> Dict[str, Dict[str, Any]]:
    """Add the identity events to change records: the existing entity's record gets ``identity_with`` (the new
    entries), ``kind_base`` (its own K) and ``kind_change`` 1 (the change record says its identity is in question)."""
    for x, es in events.items():
        r = records.setdefault(x, {"entity_id": x, "kind_change": 0.0, "summary_distance": None, "summary_basis": "none",
                                   "summary_signal": 0.0, "claim_share": 0.0, "relation_share": 0.0,
                                   "support_claims": 0, "support_relations": 0, "magnitude": 0.0, "deep": False,
                                   "summary_only": False})
        r["kind_base"] = float(r.get("kind_base", r.get("kind_change") or 0.0))
        r["identity_with"] = sorted(set(r.get("identity_with") or []) | set(es))
        r["kind_change"] = 1.0
        r["magnitude"] = 1.0
        r["deep"] = True
        r["summary_only"] = False
    return records


def measure_first(nb: Any, rid: str, entities: Iterable[str], tuning: PropagationTuning = TUNING
                  ) -> Dict[str, Dict[str, Any]]:
    """The change records of a FIRST reading ``rid`` for ``entities`` (canonical ids), as of the revision itself
    (its sequence: statements and summaries recorded after it are not looked at, so a later replay reads the same
    numbers): ``{entity_id: {kind_change, summary_distance, summary_basis, summary_signal, claim_share,
    relation_share, support_claims, support_relations, summary_only}}``. Reads and embeds; writes only vectors."""
    s = nb.store
    c = s.conn
    with s.lock:
        seq = _seq_of(c, rid)
        rev = c.execute("SELECT * FROM revisions WHERE revision_id=?", (rid,)).fetchone()
        ops = json.loads(rev["ops_json"] or "[]")
        canon = _canon_map(c)
        cn = lambda e: canon.get(e, e)  # noqa: E731
        kind: Set[str] = set()
        revised: Set[str] = set()
        for op in ops:
            name, p = op.get("op"), op.get("payload") or {}
            if name in ("entity.retire",):
                kind.add(cn(p["entity_id"]))
            elif name == "entity.merge":
                kind.update((cn(p["from_id"]), cn(p["into_id"])))
            elif name == "entity.unmerge":
                kind.update((cn(p["entity_id"]), cn(p["from_id"])))
            elif name in ("claim.revise", "claim.start_earlier", "claim.insert_before") and p.get("claim_id"):
                revised.add(p["claim_id"])
        rows: Dict[str, Dict[str, Any]] = {}
        texts: List[str] = []
        for eid in sorted(set(entities)):
            mem = _members(canon, eid)
            q = _qs(len(mem))
            claims = c.execute(
                f"SELECT cl.*, v.seq AS rseq, (SELECT v2.seq FROM revisions v2 WHERE v2.revision_id="
                f"cl.closed_revision_id) AS cseq FROM claims cl JOIN revisions v ON v.revision_id=cl.revision_id WHERE"
                f" (cl.subject_id IN ({q}) OR cl.object_id IN ({q})) AND v.seq<=? AND cl.predicate!=?",
                [*mem, *mem, seq, CLOSE_MATCH]).fetchall()

            def live_at(r: Any) -> bool:  # said something at the revision (a later closure had not happened yet)
                if r["status"] in ("retracted", "reinterpreted") and (r["cseq"] is None or r["cseq"] <= seq):
                    return False
                return True

            before = [r for r in claims if r["rseq"] < seq and (live_at(r) or r["cseq"] == seq)]
            added = [r for r in claims if r["rseq"] == seq]
            closed = [r for r in before if r["cseq"] == seq]
            changed = {r["claim_id"] for r in added} | {r["claim_id"] for r in closed} | \
                {r["claim_id"] for r in before if r["claim_id"] in revised}
            support = len(before) + len(added)
            share = min(1.0, len(changed) / support) if support else 0.0

            def rels(rs: Iterable[Any]) -> Set[Tuple[str, str, str]]:
                out = set()
                for r in rs:
                    if r["object_id"]:
                        sub, obj = cn(r["subject_id"]), cn(r["object_id"])
                        out.add((r["predicate"], obj, "out") if sub == eid else (r["predicate"], sub, "in"))
                return out

            closed_ids = {r["claim_id"] for r in closed}
            nb_, na_ = rels(before), rels([r for r in before if r["claim_id"] not in closed_ids] + added)
            union = nb_ | na_
            rel = (len(nb_ ^ na_) / len(union)) if union else 0.0
            new = c.execute(f"SELECT summary FROM entity_summaries WHERE revision_id=? AND entity_id IN ({q}) LIMIT 1",
                            [rid, *mem]).fetchone()
            old = c.execute(f"SELECT es.summary FROM entity_summaries es JOIN revisions v ON v.revision_id=es.revision_id"
                            f" WHERE es.entity_id IN ({q}) AND v.seq<? ORDER BY v.seq DESC LIMIT 1",
                            [*mem, seq]).fetchone()
            pair = (old[0], new[0]) if old is not None and new is not None else None
            if pair:
                texts.extend(pair)
            rows[eid] = {"entity_id": eid, "kind_change": 1.0 if eid in kind else 0.0, "pair": pair,
                         "claim_share": round(share, 4), "relation_share": round(rel, 4),
                         "support_claims": support, "support_relations": len(union)}
    vec: Dict[str, Any] = {}
    by_text: Dict[str, str] = {}
    if texts:
        nb.require_embedder()  # never measure without the notebook's embedder: it fails loudly
        keys = nb.cache.ensure(texts)
        vec = {k: nb.cache.get(k) for k in keys}
        by_text = dict(zip(texts, keys))
    for r in rows.values():
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
        r["summary_only"] = bool(s_norm > 0 and r["kind_change"] == 0 and r["claim_share"] == 0
                                 and r["relation_share"] == 0)
    with s.lock:
        return with_identity(rows, identity_events(s.conn, rid, _canon_map(s.conn)))


class _Reader:
    """Cached reads for one decision batch: the notes' statements and mentions, the ideas' users in history order,
    the conductances."""

    def __init__(self, c: Any, canon: Dict[str, str], tuning: PropagationTuning) -> None:
        self.c, self.canon, self.t = c, canon, tuning
        self._stmts: Dict[str, Tuple[List[Tuple[str, Optional[str]]], Set[str]]] = {}
        self._users: Dict[str, List[Tuple[str, str, str]]] = {}
        self._key: Dict[str, Tuple[str, str, str]] = {}

    def key(self, note_id: str) -> Tuple[str, str, str]:
        if note_id not in self._key:
            r = self.c.execute("SELECT occurred_at, received_at FROM notes WHERE note_id=?", (note_id,)).fetchone()
            self._key[note_id] = (r[0], r[1], note_id)
        return self._key[note_id]

    def note(self, note_id: str) -> Tuple[List[Tuple[str, Optional[str]]], Set[str]]:
        """``([(subject, object)] of its own live statements and the statements it repeated, {mentioned})``."""
        if note_id not in self._stmts:
            from ..store.notes import claim_live_sql

            cn = self.canon
            st = [(cn.get(r[0], r[0]), cn.get(r[1], r[1]) if r[1] else None) for r in self.c.execute(
                f"SELECT c.subject_id, c.object_id FROM claims c WHERE c.note_id=? AND {claim_live_sql('c')} UNION ALL"
                f" SELECT c.subject_id, c.object_id FROM claim_reinforcements r JOIN claims c ON c.claim_id=r.claim_id"
                f" WHERE r.note_id=? AND (c.note_id IS NULL OR c.note_id!=?) AND {claim_live_sql('c')}",
                (note_id, note_id, note_id)).fetchall()]
            ms = {cn.get(r[0], r[0]) for r in self.c.execute("SELECT entity_id FROM entity_notes WHERE note_id=?",
                                                              (note_id,)).fetchall()}
            self._stmts[note_id] = (st, ms)
        return self._stmts[note_id]

    def reliance(self, note_id: str, eid: str) -> float:
        """How much the note's reading rests on ``eid``: its statements with it (subject 1, object
        STATEMENT_OBJECT_WEIGHT) and a bare mention (MENTION_WEIGHT), over the same weights of everything it said."""
        st, ms = self.note(note_id)
        m = self.t.MENTION_WEIGHT
        num = sum(1.0 if s == eid else (self.t.STATEMENT_OBJECT_WEIGHT if o == eid else 0.0) for s, o in st)
        num += m if eid in ms else 0.0
        den = len(st) + m * len(ms)
        return min(1.0, num / den) if den > 0 else 0.0

    def has_statement(self, note_id: str, eid: str) -> bool:
        st, _ms = self.note(note_id)
        return any(s == eid or o == eid for s, o in st)

    def users(self, eid: str) -> List[Tuple[str, str, str]]:
        if eid not in self._users:
            mem = _members(self.canon, eid)
            q = _qs(len(mem))
            rows = self.c.execute(
                f"SELECT DISTINCT n.occurred_at, n.received_at, n.note_id FROM (SELECT note_id FROM entity_notes WHERE"
                f" entity_id IN ({q}) UNION SELECT note_id FROM note_dependencies WHERE kind='entity' AND key IN ({q}))"
                f" x JOIN notes n ON n.note_id=x.note_id WHERE n.archived_at IS NULL AND n.processed_at IS NOT NULL",
                [*mem, *mem]).fetchall()
            self._users[eid] = sorted((r[0], r[1], r[2]) for r in rows)
        return self._users[eid]

    def recency(self, eid: str, after: Tuple[str, str, str], note_id: str) -> Tuple[float, int]:
        """``(1 / (1 + between / RECENCY_NOTES), between)``: the notes that used ``eid`` strictly between the change
        and the note, in history order (the activity axis of AbstractMemory's attention decay)."""
        us = self.users(eid)
        lo, hi = bisect_left(us, after), bisect_left(us, self.key(note_id))
        between = max(0, hi - lo - (1 if lo < len(us) and us[lo] == after else 0))
        return 1.0 / (1.0 + between / self.t.RECENCY_NOTES), between

    def conductance(self, eid: str, note_id: str) -> float:
        r = self.c.execute("SELECT conductance FROM edge_conductance WHERE entity_id=? AND note_id=?",
                           (eid, note_id)).fetchone()
        return float(r[0]) if r is not None else 1.0


# --------------------------------------------------------------------------- deciding the candidates


def _open_potential(c: Any, note_id: str, exclude_id: Optional[int] = None) -> float:
    r = c.execute("SELECT COALESCE(SUM(contribution), 0) FROM gate_decisions WHERE note_id=? AND closed_at IS NULL"
                  " AND decided_at IS NOT NULL AND decision_id IS NOT ?", (note_id, exclude_id)).fetchone()
    return float(r[0] or 0.0)


def contributions(rd: _Reader, row: Dict[str, Any], records: Dict[str, Dict[str, Any]], masses: Dict[str, float],
                  *, ops: Sequence[Dict[str, Any]], rid: str, after: Tuple[str, str, str], factor: float, cap: float,
                  store: Any, note_at: str, tuning: PropagationTuning = TUNING) -> Tuple[float, List[Dict[str, Any]]]:
    """``(contribution, sources)`` of one candidate row ``{note_id, via: {used: [touched]}, task_match}``."""
    t = tuning
    nid = row["note_id"]
    out: List[Dict[str, Any]] = []
    for x, sources in sorted((row.get("via") or {}).items()):
        ident = list((records.get(x) or {}).get("identity_with") or [])
        if ident:  # identity in question (an exact-name close match from a new entry): K-class on X, X's mass
            mass = masses.get(x, 1.0)
            raw = t.WEIGHT_KIND
            st = raw / max(1.0, mass) ** t.MASS_ALPHA  # not clamped: a thin idea's identity weighs on every reference
            rel = rd.reliance(nid, x)
            if rd.has_statement(nid, x):
                rel = max(rel, t.IDENTITY_RELIANCE_FLOOR)
            rec_f, between = rd.recency(x, after, nid)
            cond = rd.conductance(x, nid)
            val = factor * min(st, cap) * rel * rec_f * cond
            out.append({"entity_id": ident[0], "via": x, "signal": "identity", "identity_with": ident,
                        "raw": round(raw, 4), "mass": mass, "strength": round(st, 4), "reliance": round(rel, 4),
                        "recency": round(rec_f, 4), "between": between, "conductance": round(cond, 4),
                        "change": {"kind_change": 1.0, "identity": True}, "contribution": round(val, 6)})
        for e in sorted(set(sources)):
            if e in ident:
                continue  # counted once, on X's identity event
            src: Dict[str, Any] = {"entity_id": e, "via": x}
            if x != e:  # reached by name: only a new name on E puts X's identity in question
                sig = 1.0 if name_signal(ops, e, rd.canon) else 0.0
                mass = masses.get(x, 1.0)
                raw = t.WEIGHT_KIND * sig
                st = min(1.0, raw / max(1.0, mass) ** t.MASS_ALPHA)
                src.update({"signal": "name" if sig else "none", "raw": round(raw, 4), "mass": mass})
            else:
                rec = records.get(e) or {}
                if ident:  # its identity is counted above; here only its own change
                    rec = {**rec, "kind_change": rec.get("kind_base", 0.0)}
                mass = masses.get(e, 1.0)
                raw, st, top = strength(rec, mass, t)
                only_summary = float(rec.get("summary_signal") or 0) > 0 and not any(
                    float(rec.get(k) or 0) for k in ("kind_change", "claim_share", "relation_share"))
                if only_summary and st > 0 and not visible(rd.c, store, e, rid, {"summary_only": True}, note_at):
                    src["invisible"] = True
                    st = 0.0
                src.update({"signal": top, "raw": round(raw, 4), "mass": mass,
                            "change": {k: rec.get(k) for k in ("kind_change", "summary_distance", "claim_share",
                                                                "relation_share")}})
            rel = rd.reliance(nid, x)
            rec_f, between = rd.recency(x, after, nid)
            cond = rd.conductance(x, nid)
            val = factor * min(st, cap) * rel * rec_f * cond
            src.update({"strength": round(st, 4), "reliance": round(rel, 4), "recency": round(rec_f, 4),
                        "between": between, "conductance": round(cond, 4), "contribution": round(val, 6)})
            out.append(src)
    if row.get("task_match"):
        val = factor * min(t.TASK_MATCH, cap)
        out.append({"entity_id": None, "via": None, "signal": "task", "strength": t.TASK_MATCH,
                    "contribution": round(val, 6)})
    return sum(s["contribution"] for s in out), out


def decide(nb: Any, c: Any, rid: str, records: Dict[str, Dict[str, Any]], *, factor: float, cap: float, hops: int,
           pass_id: Optional[str], tuning: PropagationTuning = TUNING, write: bool = True,
           rows: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Decide the pending direct candidates of revision ``rid`` (in the caller's transaction): contribution,
    accumulated potential, gate, mark. ``records`` = the revision's change records by entity. With ``write=False``
    (the offline replay) nothing is written and ``rows`` (candidate dicts) replace the stored pending rows; the
    open potential of earlier rows is then not added. Returns ``{candidates, marked, decisions: [...]}``."""
    s = nb.store
    canon = _canon_map(c)
    rev = c.execute("SELECT * FROM revisions WHERE revision_id=?", (rid,)).fetchone()
    ops = json.loads(rev["ops_json"] or "[]")
    note = c.execute("SELECT * FROM notes WHERE note_id=?", (rev["note_id"],)).fetchone()
    after = (note["occurred_at"], note["received_at"], note["note_id"])
    seq = int(rev["seq"])
    if rows is None:
        rows = [{"decision_id": r["decision_id"], "note_id": r["note_id"], "via": json.loads(r["via_json"] or "{}"),
                 "task_match": int(r["task_match"]), "reason": r["reason"], "from_at": r["from_at"]}
                for r in c.execute("SELECT * FROM gate_decisions WHERE revision_id=? AND reason_code='direct' AND"
                                   " decided_at IS NULL ORDER BY note_id", (rid,)).fetchall()]
    rd = _Reader(c, canon, tuning)
    masses: Dict[str, float] = {}
    for row in rows:
        for x, es in (row.get("via") or {}).items():
            for e in [x, *es, *((records.get(x) or {}).get("identity_with") or [])]:
                if e not in masses:
                    masses[e] = 1.0 + support_notes(c, canon, e, seq, exclude=[note["note_id"]])
    now = ids.utc_now()
    marks: List[Dict[str, Any]] = []
    out: List[Dict[str, Any]] = []
    for row in rows:
        at = c.execute("SELECT occurred_at FROM notes WHERE note_id=?", (row["note_id"],)).fetchone()[0]
        contrib, sources = contributions(rd, row, records, masses, ops=ops, rid=rid, after=after, factor=factor,
                                         cap=cap, store=s, note_at=at, tuning=tuning)
        potential = contrib + (_open_potential(c, row["note_id"], row.get("decision_id")) if write else 0.0)
        g = logistic(potential, tuning)
        marked = g >= tuning.PROPAGATION_FLOOR
        d = {"note_id": row["note_id"], "contribution": round(contrib, 6), "potential": round(potential, 6),
             "gated": round(g, 6), "marked": marked, "sources": sources}
        out.append(d)
        if not write:
            continue
        c.execute("UPDATE gate_decisions SET contribution=?, potential=?, gated=?, marked=?, sources_json=?,"
                  " decided_at=?, pass_id=?, hops=? WHERE decision_id=?",
                  (d["contribution"], d["potential"], d["gated"], int(marked), json.dumps(sources, sort_keys=True),
                   now, pass_id, hops, row["decision_id"]))
        if marked:
            used = sorted({x for x in (row.get("via") or {})})
            marks.append({"note_id": row["note_id"], "reason": row["reason"], "by_note_id": note["note_id"],
                          "by_revision_id": rid, "entities": used, "from_at": row["from_at"], "reason_code": "direct",
                          "impact": round(g, 4), "hops": hops})
    new: List[str] = []
    if marks:
        new = s.mark_reinterpret(c, marks, now)
    return {"candidates": len(rows), "marked": len(marks), "newly_marked": sorted(set(new)), "decisions": out,
            "marks": marks}


def _by_entity(store: Any, c: Any, marks: Sequence[Dict[str, Any]], new: Sequence[str]) -> List[Dict[str, Any]]:
    counts: Dict[str, int] = {}
    for m in marks:
        if m["note_id"] in new:
            for e in m.get("entities") or []:
                counts[e] = counts.get(e, 0) + 1
    return [{"entity_id": e, "name": store._name(e), "notes": n}
            for e, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:10]]


def record_changes(c: Any, rid: str, note: Any, records: Dict[str, Dict[str, Any]], masses: Dict[str, float],
                   model: Optional[str], tuning: PropagationTuning = TUNING) -> None:
    """The change records of a first reading in ``idea_changes`` (AbstractMemory ADR 0001 rule 1), with the mass and
    the strength the gate used."""
    now = ids.utc_now()
    for eid, r in records.items():
        mass = masses.get(eid, 1.0)
        _raw, st, _top = strength(r, mass, tuning)
        c.execute(
            "INSERT OR IGNORE INTO idea_changes(revision_id, entity_id, note_id, as_of, pass_id, kind_change,"
            " summary_distance, summary_basis, claim_share, relation_share, support_claims, support_relations,"
            " magnitude, deep, summary_only, carried, hops, embedder_model, spread, spread_json, recorded_at,"
            " support_notes, strength) VALUES (?,?,?,?,NULL,?,?,?,?,?,?,?,?,?,?,1.0,0,?,0,NULL,?,?,?)",
            (rid, eid, note["note_id"], note["occurred_at"], r["kind_change"], r["summary_distance"],
             r["summary_basis"], r["claim_share"], r["relation_share"], r["support_claims"], r["support_relations"],
             r["magnitude"], int(r["deep"]), int(r["summary_only"]), model, now, int(mass - 1), round(st, 4)))


def gate_first_reading(nb: Any, rid: str, tuning: PropagationTuning = TUNING) -> Dict[str, Any]:
    """Decide the candidates of a first reading ``rid`` (``Notebook._finish``): measure what it changed about the
    entities its candidates used, then gate each candidate. Idempotent (decided rows are not decided again), so
    ``Notebook.recover`` completes a stop between the projection and the gate."""
    s = nb.store
    with s.lock:
        pend = s.conn.execute("SELECT via_json FROM gate_decisions WHERE revision_id=? AND decided_at IS NULL",
                              (rid,)).fetchall()
    if not pend:
        return {"candidates": 0, "marked": 0}
    ents: Set[str] = set()
    for r in pend:
        for x, es in json.loads(r[0] or "{}").items():
            ents.update(es)
    records = measure_first(nb, rid, ents, tuning)
    with s.tx() as c:
        rev = c.execute("SELECT * FROM revisions WHERE revision_id=?", (rid,)).fetchone()
        note = c.execute("SELECT * FROM notes WHERE note_id=?", (rev["note_id"],)).fetchone()
        res = decide(nb, c, rid, records, factor=1.0, cap=math.inf, hops=0, pass_id=None, tuning=tuning)
        canon = _canon_map(c)
        masses = {e: 1.0 + support_notes(c, canon, e, int(rev["seq"]), exclude=[note["note_id"]]) for e in records}
        record_changes(c, rid, note, records, masses, getattr(nb.cache, "model_id", None), tuning)
        by_entity = _by_entity(s, c, res["marks"], res["newly_marked"])
    return {"candidates": res["candidates"], "marked": res["marked"],
            "reinterpret_marked": {"count": len(res["newly_marked"]), "notes": res["newly_marked"],
                                   "by_entity": by_entity}}


# --------------------------------------------------------------------------- learning (rule 8) and the record


def close_window(c: Any, note_id: str, outcome: str, at: str, tuning: PropagationTuning = TUNING) -> int:
    """The note was read again (``outcome`` reinterpreted | unchanged): its open gate rows close (its accumulated
    potential is spent), and every edge (used entity -> note) of a row that marked it learns: + CONDUCTANCE_STEP when
    the re-reading changed something, - when it did not, within [CONDUCTANCE_MIN, 1] (never silenced). Returns the
    rows closed."""
    rows = c.execute("SELECT decision_id, marked, sources_json FROM gate_decisions WHERE note_id=? AND closed_at IS"
                     " NULL AND decided_at IS NOT NULL", (note_id,)).fetchall()
    edges: Set[str] = set()
    for r in rows:
        if r["marked"]:
            for src in json.loads(r["sources_json"] or "[]"):
                if src.get("via") and float(src.get("contribution") or 0) > 0:
                    edges.add(src["via"])
    changed = outcome == "reinterpreted"
    for e in sorted(edges):
        cur = c.execute("SELECT conductance FROM edge_conductance WHERE entity_id=? AND note_id=?",
                        (e, note_id)).fetchone()
        before = float(cur[0]) if cur is not None else 1.0
        after = min(1.0, before + tuning.CONDUCTANCE_STEP) if changed else \
            max(tuning.CONDUCTANCE_MIN, before - tuning.CONDUCTANCE_STEP)
        c.execute("INSERT INTO edge_conductance(entity_id, note_id, conductance, changed, unchanged, last_outcome,"
                  " updated_at) VALUES (?,?,?,?,?,?,?) ON CONFLICT(entity_id, note_id) DO UPDATE SET"
                  " conductance=excluded.conductance, changed=changed+excluded.changed,"
                  " unchanged=unchanged+excluded.unchanged, last_outcome=excluded.last_outcome,"
                  " updated_at=excluded.updated_at", (e, note_id, round(after, 4), int(changed), int(not changed),
                                                      outcome, at))
    if rows:
        c.execute(f"UPDATE gate_decisions SET closed_at=?, outcome=? WHERE decision_id IN ({_qs(len(rows))})",
                  [at, outcome, *[r["decision_id"] for r in rows]])
    return len(rows)


def clear_identity(c: Any, store: Any, new_id: str, existing_id: str, at: str,
                   tuning: PropagationTuning = TUNING) -> Dict[str, Any]:
    """A person kept the two entries apart (the close match retracted, "keep apart" in /review): the identity event
    of ``existing_id`` raised by ``new_id`` is cleared. Its share leaves every open row (the source stays on record,
    ``cleared_at``), and a note it marked whose remaining potential no longer reaches the floor is not read for it:
    its waiting mark from those readings is consumed ``cleared``. Returns ``{rows, cleared_marks}``."""
    new_id, existing_id = store.canonical(new_id, c), store.canonical(existing_id, c)
    rows = c.execute("SELECT * FROM gate_decisions WHERE closed_at IS NULL AND decided_at IS NOT NULL AND"
                     " sources_json LIKE '%\"identity\"%'").fetchall()
    touched: Dict[str, Set[str]] = {}
    n = 0
    for r in rows:
        srcs = json.loads(r["sources_json"] or "[]")
        cut = 0.0
        for src in srcs:
            if src.get("signal") == "identity" and not src.get("cleared_at") and src.get("via") == existing_id and \
                    new_id in (src.get("identity_with") or [src.get("entity_id")]):
                src["cleared_at"] = at
                cut += float(src.get("contribution") or 0.0)
        if not cut:
            continue
        n += 1
        c.execute("UPDATE gate_decisions SET contribution=?, sources_json=? WHERE decision_id=?",
                  (round(max(0.0, float(r["contribution"] or 0.0) - cut), 6), json.dumps(srcs, sort_keys=True),
                   r["decision_id"]))
        touched.setdefault(r["note_id"], set()).add(r["revision_id"])
    cleared = 0
    for nid, revs in sorted(touched.items()):
        if logistic(_open_potential(c, nid), tuning) >= tuning.PROPAGATION_FLOOR:
            continue  # other changes still reach it
        q = _qs(len(revs))
        cur = c.execute(f"UPDATE reinterpret_marks SET consumed_at=?, outcome='cleared', pass_id=COALESCE(pass_id,"
                        f" 'cleared'), taken_at=COALESCE(taken_at, ?) WHERE note_id=? AND consumed_at IS NULL AND"
                        f" pass_id IS NULL AND reason_code='direct' AND by_revision_id IN ({q})",
                        [at, at, nid, *sorted(revs)])
        cleared += int(cur.rowcount or 0)
        c.execute("UPDATE gate_decisions SET marked=0 WHERE note_id=? AND closed_at IS NULL AND revision_id IN"
                  f" ({q})", [nid, *sorted(revs)])
    return {"rows": n, "cleared_marks": cleared}


def identity_blocks(store: Any, note_id: str) -> List[Dict[str, Any]]:
    """For the re-reading packet: the identity events that marked this note (not cleared), as both cards:
    ``[{entity_id, name, kind, summary, same_name: [{entity_id, name, kind, summary}]}]``."""
    seen: Dict[str, Set[str]] = {}
    for r in store.q("SELECT sources_json FROM gate_decisions WHERE note_id=? AND closed_at IS NULL AND marked=1 AND"
                     " sources_json LIKE '%\"identity\"%' ORDER BY decision_id", (note_id,)):
        for src in json.loads(r[0] or "[]"):
            if src.get("signal") == "identity" and not src.get("cleared_at") and float(src.get("contribution") or 0):
                seen.setdefault(src["via"], set()).update(src.get("identity_with") or [src.get("entity_id")])

    def card(eid: str) -> Dict[str, Any]:
        e = store.entity_row(eid)
        cur = store.current_summary_row(eid)
        return {"entity_id": eid, "name": (e["display_name"] or e["name"]) if e is not None else eid,
                "kind": e["kind"] if e is not None else "thing", "summary": cur["summary"] if cur is not None else None}

    out = []
    for x, es in sorted(seen.items()):
        live = [e for e in sorted(es) if store.canonical(e) != store.canonical(x)]  # joined since: nothing to decide
        if live:
            out.append({**card(x), "same_name": [card(e) for e in live]})
    return out


def gate_state(nb: Any, tuning: PropagationTuning = TUNING) -> Dict[str, Any]:
    """What the gate decided and would decide (readable before the stage runs), over the open rows (the notes not
    read again since their candidate rows): ``candidates`` (notes), ``marked`` (notes the gate marked), ``sources``
    (how many touched ideas weigh on the marked notes) and ``top_sources`` [{entity_id, name, signal, words, change,
    mass, strength, notes, contribution}] (the 5 that weigh most on them: what waits and why),
    ``candidate_sources`` (the same over every candidate, marked or not: for tuning), ``would_mark_at`` {"0.8": n,
    "1.2": n} (how many notes would be marked with θ scaled by 0.8 and 1.2, from the stored potentials), ``theta``,
    ``floor``, ``threshold`` (the potential that marks)."""
    s = nb.store
    rows = s.q("SELECT note_id, potential, marked, sources_json FROM gate_decisions WHERE closed_at IS NULL AND"
               " decided_at IS NOT NULL ORDER BY decision_id")
    pot: Dict[str, float] = {}
    marked: Set[str] = set()
    for r in rows:
        pot[r["note_id"]] = max(pot.get(r["note_id"], 0.0), float(r["potential"] or 0.0))
        if r["marked"]:
            marked.add(r["note_id"])

    def rank(only_marked: bool) -> List[Dict[str, Any]]:
        top: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            if only_marked and r["note_id"] not in marked:
                continue
            for src in json.loads(r["sources_json"] or "[]"):
                e = src.get("entity_id")
                if not e or float(src.get("contribution") or 0) <= 0:
                    continue
                d = top.setdefault(e, {"entity_id": e, "signal": src.get("signal"), "change": src.get("change"),
                                       "mass": src.get("mass"), "strength": src.get("strength"), "notes": set(),
                                       "contribution": 0.0})
                d["notes"].add(r["note_id"])
                d["contribution"] += float(src.get("contribution") or 0)
        order = sorted(top.values(), key=lambda d: (-d["contribution"], d["entity_id"]))
        return [{**{k: v for k, v in d.items() if k != "notes"}, "name": display_name(s, d["entity_id"]),
                 "words": SIGNAL_WORDS.get(d["signal"] or "", ""), "notes": len(d["notes"]),
                 "contribution": round(d["contribution"], 4)} for d in order]

    on_marked = rank(True)

    def would(scale: float) -> int:
        x = threshold(tuning, tuning.GATE_THETA * scale)
        return sum(1 for v in pot.values() if v >= x)

    return {"candidates": len(pot), "marked": len(marked), "sources": len(on_marked), "top_sources": on_marked[:5],
            "candidate_sources": rank(False)[:5], "would_mark_at": {"0.8": would(0.8), "1.2": would(1.2)},
            "theta": tuning.GATE_THETA, "floor": tuning.PROPAGATION_FLOOR, "threshold": round(threshold(tuning), 4)}


def decisions_of(store: Any, note_id: str) -> List[Dict[str, Any]]:
    """The gate's decisions about one note (newest first), sources decoded."""
    out = []
    for r in store.q("SELECT * FROM gate_decisions WHERE note_id=? ORDER BY decision_id DESC", (note_id,)):
        d = dict(r)
        d["via"] = json.loads(d.pop("via_json") or "{}")
        d["sources"] = json.loads(d.pop("sources_json") or "[]")
        d["marked"] = None if d["marked"] is None else bool(d["marked"])
        out.append(d)
    return out


__all__ = ["CLOSE_MATCH", "SIGNAL_WORDS", "clear_identity", "close_window", "identity_blocks", "identity_events",
           "with_identity", "contributions", "decide", "decisions_of", "gate_first_reading",
           "gate_state", "logistic", "loop_gain", "measure_first", "name_signal", "record_changes", "strength",
           "support_notes", "threshold"]
