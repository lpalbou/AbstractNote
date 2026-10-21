"""ADR 0008 §4a (lane R6): a deep change of an idea reaches the later notes of the ideas that relied on it.

Scripted models and the hashing embedder only (no network). Each test names the mechanism it guards and the mutant
that turns it red (the mutation list with results is in untracked/reports/R6.md)."""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import pytest

from abstractnote import ids
from abstractnote.process.merge import merge_extractions
from abstractnote.recall import consolidation as cons
from abstractnote.recall import propagation as prop
from abstractnote.store.notebook import Notebook
from abstractnote.tools.context import ToolContext
from abstractnote.tools.process import note_apply_revision, note_context, note_resolve_candidates

T = prop.TUNING


def day(d: int, month: int = 3) -> str:
    return f"2026-{month:02d}-{d:02d}T09:00:00Z"


# ----------------------------------------------------------------------------- scripted readings


def mention(ref: str, name: str, kind: str) -> Dict[str, Any]:
    return {"ref": ref, "name": name, "kind": kind, "aliases": [], "role": ""}


def stmt(s: str, p: str, o: str, *, literal: bool = True, certainty: str = "stated", evidence: str = "") -> Dict[str, Any]:
    return {"subject": s, "predicate": p, "object": o, "object_is_literal": literal, "certainty": certainty,
            "stance": "assert", "evidence": evidence or f"{s} {p} {o}", "valid_from": None, "valid_until": None}


def ml(ref: str, decision: str, candidate: Optional[int] = None, *, name: str = "", kind: Optional[str] = None,
       summary: Optional[str] = None) -> Dict[str, Any]:
    return {"ref": ref, "decision": decision, "candidate": candidate, "merge_candidates": [], "name": name,
            "kind": kind, "aliases": [], "summary": summary, "reason": "test"}


def cand(cands: Dict[str, Any], ref: str, eid: str) -> Optional[int]:
    for m in cands["mentions"]:
        if m["ref"] == ref:
            for c in m["candidates"]:
                if c["entity_id"] == eid:
                    return c["index"]
    return None


class Clock:
    def __init__(self) -> None:
        self.t = ids.parse_time("2026-10-20T12:00:00Z")

    def next(self) -> str:
        from datetime import timedelta

        self.t = self.t + timedelta(minutes=1)
        return self.t.strftime("%Y-%m-%dT%H:%M:%SZ")


class World:
    """A notebook with names: ``read`` understands a note (or re-reads one) from ``mentions = [(ref, name, kind,
    reuse_entity_id_or_None, summary)]`` and ``claims``."""

    def __init__(self, nb: Notebook) -> None:
        self.nb = nb
        self.clock = Clock()
        self.at: Dict[str, str] = {}

    def eid(self, name: str) -> str:
        r = self.nb.store.q1("SELECT entity_id FROM entities WHERE display_name=? AND merged_into IS NULL", (name,))
        assert r is not None, name
        return r[0]

    def read(self, text: str, at: str, mentions: Sequence[Tuple], claims: Sequence[Dict[str, Any]] = (), *,
             note_id: Optional[str] = None, reinterpret: bool = False) -> Dict[str, Any]:
        ext = {"summary": text or "A note.", "mentions": [mention(m[0], m[1], m[2]) for m in mentions],
               "claims": list(claims), "tasks": [], "time_refs": [], "topics": [], "document_date": None}
        ctx = ToolContext(nb=self.nb)
        if note_id is None:
            note_id = self.nb.capture(text, occurred_at=at, now=self.clock.next()).note_id
        self.at[note_id] = at
        packet = note_context(ctx, note_id)
        _, names = merge_extractions([ext])
        cands = note_resolve_candidates(ctx, note_id, names)
        ms = []
        for ref, name, kind, use, summary in mentions:
            if use:
                i = cand(cands, ref, use)
                assert i is not None, f"{use} is not a candidate of {ref}"
                ms.append(ml(ref, "reuse", i, summary=summary))
            else:
                ms.append(ml(ref, "create", name=name, kind=kind, summary=summary or f"{name}."))
        out = note_apply_revision(ctx, note_id, ext, {"mentions": ms, "claims": [], "tasks": [],
                                                     "revision_summary": "test"}, reinterpret=reinterpret)
        return {"note_id": note_id, "out": out, "packet": packet}


@pytest.fixture
def nb(tmp_path):
    notebook = Notebook.open(tmp_path / "nb")
    yield notebook
    notebook.close()


def marks_of(nb: Notebook, note_id: str) -> List[Dict[str, Any]]:
    return [dict(r) for r in nb.store.q("SELECT * FROM reinterpret_marks WHERE note_id=? ORDER BY mark_id", (note_id,))]


def rho(change_at: str, last_seen: str) -> float:
    days = (ids.parse_time(change_at) - ids.parse_time(last_seen)).total_seconds() / 86400
    return 1.0 if days <= 0 else 1 / (1 + days / T.RECENCY_DAYS)


# ----------------------------------------------------------------------------- P1: foundation through a neighbour


def zorn_world(nb: Notebook) -> Tuple[World, Dict[str, str]]:
    """Day 12 ties the budget board to Zorn; day 15 and day 22 use only the board; day 20 says Zorn is a company;
    the day-5 archive says Zorn is a codename (it marks day 12 and day 20 directly: they used Zorn)."""
    w = World(nb)
    n: Dict[str, str] = {}
    n["d12"] = w.read("The budget board is the board of Zorn.", day(12),
                      [("m1", "the budget board", "org", None, None), ("m2", "Zorn", "org", None, None)],
                      [stmt("m1", "ex:part_of", "m2", literal=False)])["note_id"]
    board, zorn = w.eid("the budget board"), w.eid("Zorn")
    n["d15"] = w.read("The budget board meets on Fridays.", day(15), [("m1", "the budget board", "org", board, None)],
                      [stmt("m1", "ex:meets_on", "Fridays")])["note_id"]
    n["d20"] = w.read("Zorn is a company we work with.", day(20), [("m1", "Zorn", "org", zorn, None)],
                      [stmt("m1", "ex:is_a", "company")])["note_id"]
    n["d22"] = w.read("The budget board approved the budget.", day(22), [("m1", "the budget board", "org", board, None)],
                      [stmt("m1", "ex:approved", "the budget")])["note_id"]
    n["d05"] = w.read("Zorn is the codename of the LUT project.", day(5), [("m1", "Zorn", "org", zorn, None)],
                      [stmt("m1", "ex:codename_of", "the LUT project")])["note_id"]
    n["board"], n["zorn"] = board, zorn
    return w, n


def reread_d20(w: World, n: Dict[str, str], *, reinterpret: bool = True) -> Dict[str, Any]:
    return w.read("", day(20), [("m1", "Zorn", "org", n["zorn"], None)], [stmt("m1", "ex:kind", "project")],
                  note_id=n["d20"], reinterpret=reinterpret)


def test_a_deep_change_spreads_one_hop_to_the_later_notes_of_a_proximal_idea(nb):
    """P1: the day-20 re-reading replaces half of Zorn's statements (C = 2/4, deep); the board relies on Zorn
    (its one relation), so day 22 (after day 20, used only the board) is marked ``propagated`` from Zorn with the
    gate of DECAY * min(strength, carried) * reliance(Zorn -> board) * reliance(day 22, board) (R7: the activation
    gate, AbstractMemory ADR 0001 rule 7; ``carried`` = the gate of day 20's own mark) and 1 hop; day 15 (before day
    20) is not (forward only); day 12 keeps its direct mark. MUTATIONS: drop the Notebook._propagate call;
    later_dependents from a date instead of after the note (forward only); reliance without recency; a direct mark
    carrying 1.0 instead of its gate."""
    from abstractnote.recall import activation as act

    w, n = zorn_world(nb)
    assert {m["note_id"] for m in nb.store.reinterpret_marks()} == {n["d12"], n["d20"]}
    g20 = marks_of(nb, n["d20"])[0]["impact"]
    assert 0 < g20 < 1
    r = reread_d20(w, n)
    rid = r["out"]["revision_id"]
    rec = {x["entity_id"]: x for x in nb.idea_changes(rid)}[n["zorn"]]
    assert rec["claim_share"] == 0.5 and rec["deep"] and not rec["summary_only"] and rec["spread"]
    assert rec["carried"] == g20 and rec["support_notes"] == 2  # day 12 and day 5 supported Zorn before
    expected = round(act.logistic(T.PROPAGATION_DECAY * min(rec["strength"], g20) * rho(day(20), day(12))), 4)
    [mark] = [m for m in marks_of(nb, n["d22"]) if m["consumed_at"] is None]
    assert mark["reason_code"] == "propagated" and mark["source_entity_id"] == n["zorn"]
    assert mark["source_revision_id"] == rid and mark["hops"] == 1 and mark["impact"] == expected
    assert "Zorn is understood differently" in mark["reason"] and "relies on it" in mark["reason"]
    assert marks_of(nb, n["d15"]) == []
    assert [m["reason_code"] for m in marks_of(nb, n["d12"])] == ["direct"]
    reached = rec["spread_json"]["reached"]
    assert [x["entity_id"] for x in reached] == [n["board"]] and n["d22"] in reached[0]["notes"]
    assert r["out"]["propagation"]["marked"] == 1


def test_a_shallow_change_records_but_does_not_spread(nb):
    """P4: a re-reading that only changes a statement's evidence (C = 0, R = 0, no summary pair) writes a revision and
    a change record with deep = 0, and marks nobody. MUTATION: deep always true; drop the deep check in spread."""
    w, n = zorn_world(nb)
    r = w.read("", day(20), [("m1", "Zorn", "org", n["zorn"], None)],
               [stmt("m1", "ex:is_a", "company", evidence="Zorn, a company we work with")], note_id=n["d20"],
               reinterpret=True)
    rec = {x["entity_id"]: x for x in nb.idea_changes(r["out"]["revision_id"])}[n["zorn"]]
    assert rec["claim_share"] == 0 and rec["relation_share"] == 0 and rec["summary_basis"] == "none"
    assert not rec["deep"] and not rec["spread"] and rec["magnitude"] == 0
    assert marks_of(nb, n["d22"]) == [] and r["out"]["propagation"]["marked"] == 0


def test_an_unchanged_re_reading_stops_the_spread(nb):
    """ADR 0008 §4a: the model's identical answer writes no revision, so nothing is measured and nothing spreads.
    MUTATION: (guarded by R2's unchanged branch) a change record written for an unchanged reading."""
    w, n = zorn_world(nb)
    r = w.read("", day(20), [("m1", "Zorn", "org", n["zorn"], None)], [stmt("m1", "ex:is_a", "company")],
               note_id=n["d20"], reinterpret=True)
    assert r["out"]["unchanged"] is True
    assert nb.store.q1("SELECT COUNT(*) FROM idea_changes WHERE note_id=?", (n["d20"],))[0] == 0
    assert marks_of(nb, n["d22"]) == []


def test_a_certainty_change_counts_as_a_replaced_statement(nb):
    """C's key carries certainty (F1-style): the only statement of an entity read again as ``possible`` gives
    C = 1, deep. MUTATION: the claim key without certainty (revise not counted)."""
    w = World(nb)
    a = w.read("We go with 40k for the Helix budget.", day(20), [("m1", "Helix budget", "thing", None, None)],
               [stmt("m1", "ex:amount", "40k")])
    hb = w.eid("Helix budget")
    r = w.read("", day(20), [("m1", "Helix budget", "thing", hb, None)],
               [stmt("m1", "ex:amount", "40k", certainty="possible")], note_id=a["note_id"], reinterpret=True)
    rec = {x["entity_id"]: x for x in nb.idea_changes(r["out"]["revision_id"])}[hb]
    assert r["out"]["claims_revised"] and rec["claim_share"] == 1.0 and rec["deep"]


def test_a_retired_entity_is_a_kind_change(nb):
    """K: the re-reading retires the entity its old reading made (it now reuses the earlier one): K = 1 on it.
    MUTATION: drop K (entity.retire not counted)."""
    w = World(nb)
    a = w.read("Reviewed the plan with Priya.", day(20), [("m1", "the plan", "artifact", None, None)],
               [stmt("m1", "ex:reviewed_with", "Priya")])
    dup = w.eid("the plan")
    w.read("The Helix plan is the three-step plan.", day(5), [("m1", "Helix plan", "artifact", None, None)],
           [stmt("m1", "ex:steps", "three")])
    helix = w.eid("Helix plan")
    nb.mark_reinterpret([a["note_id"]], "asked")
    r = w.read("", day(20), [("m1", "Helix plan", "artifact", helix, None)],
               [stmt("m1", "ex:reviewed_with", "Priya")], note_id=a["note_id"], reinterpret=True)
    assert dup in r["out"]["entities_retired"]
    rec = {x["entity_id"]: x for x in nb.idea_changes(r["out"]["revision_id"])}
    assert rec[dup]["kind_change"] == 1.0 and rec[dup]["deep"] and rec[dup]["magnitude"] == 1.0


# ----------------------------------------------------------------------------- S, visibility


def summary_world(nb: Notebook) -> Tuple[World, Dict[str, str]]:
    """Zorn is described by day 20 and again by day 25; the board (tied to Zorn by day 12) is used by day 22
    (between) and day 28 (after day 25)."""
    w = World(nb)
    n: Dict[str, str] = {}
    n["d12"] = w.read("The Zorn board is the budget board of Zorn.", day(12),
                      [("m1", "the Zorn board", "org", None, None), ("m2", "Zorn", "org", None, None)],
                      [stmt("m1", "ex:part_of", "m2", literal=False)])["note_id"]
    board, zorn = w.eid("the Zorn board"), w.eid("Zorn")
    n["d20"] = w.read("Zorn is a company we work with.", day(20),
                      [("m1", "Zorn", "org", zorn, "Zorn is a company we work with.")],
                      [stmt("m1", "ex:is_a", "company")])["note_id"]
    n["d22"] = w.read("The Zorn board approved the budget.", day(22), [("m1", "the Zorn board", "org", board, None)],
                      [stmt("m1", "ex:approved", "the budget")])["note_id"]
    n["d25"] = w.read("Zorn moved to Berlin.", day(25),
                      [("m1", "Zorn", "org", zorn, "Zorn is a company we work with, now in Berlin.")],
                      [stmt("m1", "ex:located_in", "Berlin")])["note_id"]
    n["d28"] = w.read("The Zorn board met again.", day(28), [("m1", "the Zorn board", "org", board, None)],
                      [stmt("m1", "ex:met", "again")])["note_id"]
    n["board"], n["zorn"] = board, zorn
    return w, n


def test_identical_summaries_are_no_change_and_disjoint_ones_are_deep_and_seen_only_in_their_window(nb):
    """S and visibility: a re-reading that rewrites Zorn's summary with disjoint words (and only an evidence change)
    is deep through S alone (summary_only): day 22, whose known-then summary of Zorn is the new one, is marked;
    day 28, which knows Zorn through day 25's summary, is not (counted invisible). A paraphrase-free rewrite (the
    same text) gives d = 0. MUTATIONS: drop S (summary_only never deep); visibility always true."""
    w, n = summary_world(nb)
    nb.mark_reinterpret([n["d20"]], "asked")
    same = w.read("", day(20), [("m1", "Zorn", "org", n["zorn"], "Zorn is a company we work with.")],
                  [stmt("m1", "ex:is_a", "company", evidence="a company")], note_id=n["d20"], reinterpret=True)
    rec = {x["entity_id"]: x for x in nb.idea_changes(same["out"]["revision_id"])}[n["zorn"]]
    assert rec["summary_basis"] == "both" and rec["summary_distance"] == 0.0 and not rec["deep"]
    nb.mark_reinterpret([n["d20"]], "asked again")
    r = w.read("", day(20), [("m1", "Zorn", "org", n["zorn"], "Mira's secret codename, never a firm: LUT optics.")],
               [stmt("m1", "ex:is_a", "company", evidence="the company")], note_id=n["d20"], reinterpret=True)
    rec = {x["entity_id"]: x for x in nb.idea_changes(r["out"]["revision_id"])}[n["zorn"]]
    assert rec["summary_distance"] >= T.DEEP_SUMMARY_DISTANCE and rec["deep"] and rec["summary_only"]
    assert [m["reason_code"] for m in marks_of(nb, n["d22"])] == ["propagated"]
    assert marks_of(nb, n["d28"]) == [] and rec["spread_json"]["invisible"] >= 1


def test_a_statement_change_is_seen_by_every_later_note(nb):
    """Visibility: the same world, but the re-reading replaces Zorn's statement (C deep): day 22 and day 28 are both
    marked. MUTATION: the summary-only window applied to every change."""
    w, n = summary_world(nb)
    nb.mark_reinterpret([n["d20"]], "asked")
    w.read("", day(20), [("m1", "Zorn", "org", n["zorn"], None)], [stmt("m1", "ex:kind", "codename")],
           note_id=n["d20"], reinterpret=True)
    assert [m["reason_code"] for m in marks_of(nb, n["d22"])] == ["propagated"]
    assert [m["reason_code"] for m in marks_of(nb, n["d28"])] == ["propagated"]


# ----------------------------------------------------------------------------- P3: hubs and the floor


def hub_world(nb: Notebook) -> Tuple[World, Dict[str, str]]:
    """Laurent works with six people (day 2) and Quill employs him (day 3); day 20 uses only Quill, day 21 only
    Laurent, day 25 only Laurent, day 26 only Ana (one of the six)."""
    w = World(nb)
    n: Dict[str, str] = {}
    people = ["Ana", "Ben", "Cleo", "Dov", "Eli", "Fay"]
    ments = [("m0", "Laurent", "person", None, None)] + [(f"m{i + 1}", p, "person", None, None)
                                                          for i, p in enumerate(people)]
    w.read("Laurent works with Ana, Ben, Cleo, Dov, Eli and Fay.", day(2), ments,
           [stmt("m0", "ex:works_with", f"m{i + 1}", literal=False) for i in range(6)])
    laurent = w.eid("Laurent")
    w.read("Quill employs Laurent.", day(3), [("m1", "Quill", "org", None, None),
                                             ("m2", "Laurent", "person", laurent, None)],
           [stmt("m1", "ex:employs", "m2", literal=False)])
    quill = w.eid("Quill")
    n["q20"] = w.read("Quill is a lab.", day(20), [("m1", "Quill", "org", quill, None)],
                      [stmt("m1", "ex:is_a", "lab")])["note_id"]
    n["l21"] = w.read("Laurent is an engineer.", day(21),
                      [("m1", "Laurent", "person", laurent, "Laurent is an engineer at Quill.")],
                      [stmt("m1", "ex:role", "engineer")])["note_id"]
    n["l25"] = w.read("Laurent is tired.", day(25), [("m1", "Laurent", "person", laurent, None)],
                      [stmt("m1", "ex:state", "tired")])["note_id"]
    n["a26"] = w.read("Ana shipped the release.", day(26), [("m1", "Ana", "person", w.eid("Ana"), None)],
                      [stmt("m1", "ex:shipped", "the release")])["note_id"]
    n["laurent"], n["quill"], n["ana"] = laurent, quill, w.eid("Ana")
    return w, n


def test_the_floor_stops_a_hub_and_counts_what_it_cuts(nb):
    """P3: Quill changes deeply, but Laurent has 7 relations, so his reliance on Quill is 1/7 * recency and the
    impact 0.5 * 0.5 * r < FLOOR: none of Laurent's later notes is marked, and the cut is counted. MUTATIONS: floor 0;
    below_floor not counted; source normalisation."""
    w, n = hub_world(nb)
    nb.mark_reinterpret([n["q20"]], "asked")
    r = w.read("", day(20), [("m1", "Quill", "org", n["quill"], None)], [stmt("m1", "ex:is_a", "university")],
               note_id=n["q20"], reinterpret=True)
    rec = {x["entity_id"]: x for x in nb.idea_changes(r["out"]["revision_id"])}[n["quill"]]
    assert rec["deep"] and rec["spread_json"]["below_floor"] == 1 and rec["spread_json"]["reached"] == []
    assert marks_of(nb, n["l21"]) == [] and marks_of(nb, n["l25"]) == []
    assert 0.5 * T.PROPAGATION_DECAY * rho(day(20), day(3)) / 7 < T.PROPAGATION_FLOOR


def test_a_hub_reaches_the_ideas_that_rely_on_it(nb):
    """Target normalisation: Laurent's summary is rewritten (deep through S); Ana's only relation is with him
    (reliance = recency), so her later note is marked; with source normalisation (1/7) it would not be.
    MUTATION: reliance divided by the source's mass."""
    w, n = hub_world(nb)
    nb.mark_reinterpret([n["l21"]], "asked")
    w.read("", day(21), [("m1", "Laurent", "person", n["laurent"], "A fictional pilot from an old novel, never real.")],
           [stmt("m1", "ex:role", "engineer", evidence="an engineer")], note_id=n["l21"], reinterpret=True)
    [m] = marks_of(nb, n["a26"])
    assert m["reason_code"] == "propagated" and m["source_entity_id"] == n["laurent"]


def test_tuning_out_of_band_is_refused():
    """One home for the dials: a value outside its band raises, never clamps. MUTATION: drop __post_init__."""
    for bad in ({"DEEP_SUMMARY_DISTANCE": 0.05}, {"PROPAGATION_DECAY": 1.0}, {"PROPAGATION_FLOOR": 0.0},
                {"TIER_A_IMPACT": 0.05}, {"MAX_PROPAGATION_HOPS": 0}, {"DEEP_CLAIM_SHARE": 0.0}):
        with pytest.raises(ValueError):
            prop.PropagationTuning(**bad)
    assert prop.PropagationTuning() == prop.TUNING


# ----------------------------------------------------------------------------- marks: dominance and tiers


def test_a_direct_mark_dominates_and_a_stronger_propagated_mark_replaces_a_weaker_one(nb):
    """Max, never a sum: a weaker propagated mark gives way to a stronger one, any propagated mark to a direct one,
    and a direct mark is never replaced. MUTATION: plain INSERT OR IGNORE (no upgrade)."""
    w = World(nb)
    nid = w.read("A note.", day(10), [("m1", "Thing", "thing", None, None)])["note_id"]
    s = nb.store

    def put(**m: Any) -> None:
        with s.tx() as c:
            s.mark_reinterpret(c, [{"note_id": nid, "reason": "r", **m}], ids.utc_now())

    put(reason_code="propagated", impact=0.2, hops=1, source_entity_id="e1")
    put(reason_code="propagated", impact=0.4, hops=2, source_entity_id="e2")
    put(reason_code="propagated", impact=0.3, hops=1, source_entity_id="e3")
    [m] = marks_of(nb, nid)
    assert (m["impact"], m["source_entity_id"], m["hops"]) == (0.4, "e2", 2)
    put()
    put(reason_code="propagated", impact=0.9, hops=1, source_entity_id="e4")
    [m] = marks_of(nb, nid)
    assert m["reason_code"] == "direct" and m["impact"] == 1.0 and m["source_entity_id"] is None


def test_tiers_keep_history_order_inside_each_tier(nb):
    """Two tiers, each one forward sweep: direct and strong propagated marks first (in history order), then the
    weak ones (in history order). MUTATION: order by impact only; one tier."""
    w = World(nb)
    notes = {d: w.read(f"Note {d}.", day(d), [("m1", f"Thing {d}", "thing", None, None)])["note_id"]
             for d in (10, 12, 25, 30)}
    s = nb.store
    with s.tx() as c:
        s.mark_reinterpret(c, [{"note_id": notes[30], "reason": "direct"},
                               {"note_id": notes[25], "reason": "p", "reason_code": "propagated", "impact": 0.5},
                               {"note_id": notes[10], "reason": "p", "reason_code": "propagated", "impact": 0.15},
                               {"note_id": notes[12], "reason": "p", "reason_code": "propagated", "impact": 0.2}],
                           ids.utc_now())
    order = [p["note_id"] for p in s.pending_marks()]
    assert order == [notes[25], notes[30], notes[10], notes[12]]
    assert [p["tier"] for p in s.pending_marks()] == ["A", "A", "B", "B"]


# ----------------------------------------------------------------------------- P2: chains and cycles in the stage


def stage_pass(w: World, script: Dict[str, Callable[[], Dict[str, Any]]], bound: int = 40) -> List[str]:
    """One consolidation pass of the reinterpret stage with scripted re-readings; returns the notes read (in order)."""
    nb = w.nb
    cons.begin(nb)
    read: List[str] = []
    for _ in range(bound):
        got = cons.reinterpret_next(nb, 1)
        if not got["items"]:
            break
        nid = got["items"][0]["note_id"]
        read.append(nid)
        script[nid]()
    else:
        raise AssertionError("the pass did not end within its step bound")
    cons.end(nb, "completed")
    return read


def deep_reread(w: World, nid: str, name: str, eid: str, value: str) -> Callable[[], Dict[str, Any]]:
    return lambda: w.read("", w.at[nid], [("m1", name, "project", eid, f"{value} codename secret programme.")],
                          [stmt("m1", "ex:status", value)], note_id=nid)


def test_the_carried_cap_damps_a_chain_and_a_second_pass_makes_no_call(nb):
    """P2 (chain a - b - c): the archive marks Ya directly; Ya's re-reading spreads Alpha to Beta (Yb, hop 1, the gate
    of DECAY * min(strength, carried) * reliance); Yb's re-reading starts from min(strength, Yb's gate), so Gamma's
    note gets a potential whose gate stays under the floor: the chain dies at hop 2 (R7: with the activation gate a
    hop hands on its gate, AbstractMemory ADR 0001 rules 3 and 7). Each note is read once, and the second pass has
    nothing to read. MUTATIONS: m0 = m (no carried cap: Yc is marked); the hop count not carried."""
    from abstractnote.recall import activation as act

    w = World(nb)
    x = w.read("Alpha depends on Beta.", day(10), [("m1", "Alpha", "project", None, None),
                                                   ("m2", "Beta", "project", None, None)],
               [stmt("m1", "ex:depends_on", "m2", literal=False)])["note_id"]
    a, b = w.eid("Alpha"), w.eid("Beta")
    w.read("Beta depends on Gamma.", day(11), [("m1", "Beta", "project", b, None),
                                               ("m2", "Gamma", "project", None, None)],
           [stmt("m1", "ex:depends_on", "m2", literal=False)])
    g = w.eid("Gamma")
    ya = w.read("Alpha is late.", day(20), [("m1", "Alpha", "project", a, "Alpha is a project.")],
                [stmt("m1", "ex:status", "late")])["note_id"]
    yb = w.read("Beta is late.", day(21), [("m1", "Beta", "project", b, "Beta is a project.")],
                [stmt("m1", "ex:status", "late")])["note_id"]
    yc = w.read("Gamma is late.", day(22), [("m1", "Gamma", "project", g, "Gamma is a project.")],
                [stmt("m1", "ex:status", "late")])["note_id"]
    w.read("Alpha was renamed.", day(1), [("m1", "Alpha", "project", a, None)],
           [stmt("m1", "ex:renamed", "yes"), stmt("m1", "ex:owner", "Mira"), stmt("m1", "ex:phase", "two")])
    assert {m["note_id"] for m in nb.store.reinterpret_marks()} == {x, ya}
    script = {x: lambda: w.read("", day(10), [("m1", "Alpha", "project", a, None), ("m2", "Beta", "project", b, None)],
                                [stmt("m1", "ex:depends_on", "m2", literal=False)], note_id=x),
              ya: deep_reread(w, ya, "Alpha", a, "cancelled"), yb: deep_reread(w, yb, "Beta", b, "cancelled"),
              yc: deep_reread(w, yc, "Gamma", g, "cancelled")}
    g_ya = marks_of(nb, ya)[0]["impact"]
    read = stage_pass(w, script)
    assert sorted(read) == sorted({x, ya, yb}) and len(read) == len(set(read))
    rev_ya = nb.store.q1("SELECT revision_id FROM revisions WHERE note_id=? AND action='note.reinterpret'", (ya,))[0]
    alpha = {r["entity_id"]: r for r in nb.idea_changes(rev_ya)}[a]
    assert alpha["carried"] == g_ya and alpha["strength"] > g_ya  # the cap binds
    p1 = round(act.logistic(T.PROPAGATION_DECAY * min(alpha["strength"], g_ya) * rho(day(20), day(10)) / 2), 4)
    mb = [m for m in marks_of(nb, yb) if m["reason_code"] == "propagated"]
    assert [(m["impact"], m["hops"]) for m in mb] == [(p1, 1)] and p1 >= T.PROPAGATION_FLOOR
    assert marks_of(nb, yc) == []
    rev_yb = nb.store.q1("SELECT revision_id FROM revisions WHERE note_id=? AND action='note.reinterpret'", (yb,))[0]
    beta = {r["entity_id"]: r for r in nb.idea_changes(rev_yb)}[b]
    assert beta["carried"] == p1 and beta["hops"] == 1 and beta["spread_json"]["below_floor"] >= 1
    [dec] = [d for d in act.decisions_of(nb.store, yc) if d["reason_code"] == "propagated"]
    assert not dec["marked"] and dec["gated"] < T.PROPAGATION_FLOOR and dec["hops"] == 2
    assert nb.store.pending_marks() == []
    assert stage_pass(w, script) == []  # the second pass: nothing marked, no re-reading, no call


def test_a_cycle_dies_and_each_idea_spreads_once_per_pass(nb):
    """P2 (cycle a - b - c - a): every re-reading changes its idea deeply; the pass ends, each note is read at most
    once, no idea spreads twice in a pass (the later re-reading of Alpha records ``already spread``), and the next
    pass has nothing to read. MUTATION: drop the visited set (Alpha spreads again from Za)."""
    w = World(nb)
    w.read("Alpha depends on Beta.", day(10), [("m1", "Alpha", "project", None, None),
                                               ("m2", "Beta", "project", None, None)],
           [stmt("m1", "ex:depends_on", "m2", literal=False)])
    a, b = w.eid("Alpha"), w.eid("Beta")
    w.read("Beta depends on Gamma.", day(11), [("m1", "Beta", "project", b, None),
                                               ("m2", "Gamma", "project", None, None)],
           [stmt("m1", "ex:depends_on", "m2", literal=False)])
    g = w.eid("Gamma")
    w.read("Gamma depends on Alpha.", day(12), [("m1", "Gamma", "project", g, None),
                                                ("m2", "Alpha", "project", a, None)],
           [stmt("m1", "ex:depends_on", "m2", literal=False)])
    who = {"Alpha": a, "Beta": b, "Gamma": g}
    notes: Dict[str, Tuple[str, str]] = {}
    for d, name in ((20, "Alpha"), (21, "Beta"), (22, "Gamma"), (27, "Alpha"), (28, "Beta"), (29, "Gamma")):
        notes[w.read(f"{name} is late.", day(d), [("m1", name, "project", who[name], f"{name} is a project.")],
                     [stmt("m1", "ex:status", "late")])["note_id"]] = (name, who[name])
    w.read("Alpha was renamed.", day(1), [("m1", "Alpha", "project", a, None)], [stmt("m1", "ex:renamed", "yes")])
    script: Dict[str, Callable[[], Dict[str, Any]]] = {}
    for nid in [r[0] for r in nb.store.q("SELECT note_id FROM notes")]:
        if nid in notes:
            script[nid] = deep_reread(w, nid, notes[nid][0], notes[nid][1], "cancelled")
        else:  # the relation notes and the archive read the same again
            script[nid] = (lambda nid=nid: w.read("", w.at[nid], [(f"m{i + 1}", nm, "project", who[nm], None)
                                                                  for i, nm in enumerate(
                [r[0] for r in nb.store.q("SELECT e.display_name FROM entity_notes x JOIN entities e ON"
                                          " e.entity_id=x.entity_id WHERE x.note_id=? ORDER BY e.display_name",
                                          (nid,))])], [], note_id=nid))
    read = stage_pass(w, script)
    assert len(read) == len(set(read)) and read
    pid = cons.last(nb)["consolidation_id"]
    rows = nb.store.q("SELECT entity_id, COUNT(*) FROM idea_changes WHERE pass_id=? AND spread=1 GROUP BY entity_id",
                      (pid,))
    assert rows and all(r[1] == 1 for r in rows)
    assert nb.store.q1("SELECT COUNT(*) FROM idea_changes WHERE pass_id=? AND spread_json LIKE '%already spread%'",
                       (pid,))[0] >= 1
    assert nb.store.pending_marks() == [] and stage_pass(w, script) == []
    assert max(r[0] for r in nb.store.q("SELECT hops FROM reinterpret_marks")) <= T.MAX_PROPAGATION_HOPS


# ----------------------------------------------------------------------------- the no-call check and the packet


def test_the_packet_shows_what_changed_and_the_no_call_check_reads_related(nb):
    """A note taken through a propagated mark is shown the "Understood differently" block (Zorn's statements its
    re-reading replaced) and its digest records Zorn's state as known then (``related``). A second propagated mark
    from Zorn with nothing new is settled without a call; once Zorn changes again at an earlier date it is not.
    A propagated mark on a reading that never recorded Zorn is read. MUTATIONS: drop the block; ignore ``related``
    in digest_unchanged."""
    from abstractnote.process.packets import CHANGED_LABEL, link_batches

    w, n = zorn_world(nb)
    reread_d20(w, n)
    assert cons.digest_unchanged(nb, n["d22"]) is False  # its reading never recorded Zorn: read it
    ctx = note_context(ToolContext(nb=nb), n["d22"])
    [block] = ctx["note"]["understood_differently"]
    assert block["entity_id"] == n["zorn"] and any("ex:kind" in x for x in block["now"])
    assert any("ex:is_a" in x for x in block["no_longer"])
    ext = {"summary": "x", "mentions": [mention("m1", "the budget board", "org")], "claims": [], "tasks": []}
    [batch] = link_batches(ctx["note"], ext, [{"ref": "m1", "candidates": []}], [])
    assert batch["changed"].startswith(CHANGED_LABEL) and "Zorn — org" in batch["changed"]
    assert "0.2" not in batch["changed"]  # the model never sees an impact
    w.read("", day(22), [("m1", "the budget board", "org", n["board"], None)],
           [stmt("m1", "ex:approved", "the budget plan")], note_id=n["d22"], reinterpret=True)
    rid = nb.store.current_interpretation(n["d22"])
    related = json.loads(nb.store.revision_row(rid)["context_json"])["related"]
    assert related == {n["zorn"]: prop.related_state(nb.store, n["zorn"], day(22))}
    with nb.store.tx() as c:
        nb.store.mark_reinterpret(c, [{"note_id": n["d22"], "reason": "again", "reason_code": "propagated",
                                       "impact": 0.3, "source_entity_id": n["zorn"], "hops": 1}], ids.utc_now())
    assert cons.digest_unchanged(nb, n["d22"]) is True
    w.read("Zorn signed with Quill.", day(18), [("m1", "Zorn", "org", n["zorn"], None)],
           [stmt("m1", "ex:signed_with", "Quill")])
    assert cons.digest_unchanged(nb, n["d22"]) is False
    plain = World(nb).read("Unrelated words here.", day(23), [("m1", "Wren", "thing", None, None)])["note_id"]
    assert note_context(ToolContext(nb=nb), plain)["note"].get("understood_differently") is None


def test_an_unchanged_propagated_re_reading_keeps_related_so_a_later_wave_needs_no_call(nb):
    """INT2 seam (R6 x R2): a re-reading taken through a propagated mark that comes out ``unchanged`` writes no
    revision; what it was read against (``related``: Zorn as known on day 22) is appended to
    ``reading_confirmations`` for the current reading, whose own ``context_json`` stays exactly as written (ADR 0003):
    a later propagated mark from the same, unchanged Zorn is settled with no call, and one after Zorn changes again
    (at an earlier date) is not. MUTATION: drop the keep_related write / record only ``related`` / read the
    revision's context_json instead of reading_record in digest_unchanged."""
    w, n = zorn_world(nb)
    reread_d20(w, n)
    assert [m["reason_code"] for m in marks_of(nb, n["d22"]) if m["consumed_at"] is None] == ["propagated"]
    rid = nb.store.current_interpretation(n["d22"])
    written = nb.store.revision_row(rid)["context_json"]
    assert not json.loads(written).get("related")
    r = w.read("", day(22), [("m1", "the budget board", "org", n["board"], None)],
               [stmt("m1", "ex:approved", "the budget")], note_id=n["d22"], reinterpret=True)
    assert r["out"]["unchanged"] is True and r["out"]["revision_id"] is None
    assert nb.store.current_interpretation(n["d22"]) == rid  # no revision of its own
    assert nb.store.revision_row(rid)["context_json"] == written  # the revision log is not touched
    [conf] = nb.store.q("SELECT note_id, revision_id FROM reading_confirmations")
    assert tuple(conf) == (n["d22"], rid)
    stored = nb.store.reading_record(n["d22"])
    assert stored["related"] == {n["zorn"]: prop.related_state(nb.store, n["zorn"], day(22))}
    assert stored["looked_up"] is True
    with nb.store.tx() as c:
        nb.store.mark_reinterpret(c, [{"note_id": n["d22"], "reason": "again", "reason_code": "propagated",
                                       "impact": 0.3, "source_entity_id": n["zorn"], "hops": 1}], ids.utc_now())
    assert cons.digest_unchanged(nb, n["d22"]) is True
    w.read("Zorn signed with Quill.", day(18), [("m1", "Zorn", "org", n["zorn"], None)],
           [stmt("m1", "ex:signed_with", "Quill")])
    assert cons.digest_unchanged(nb, n["d22"]) is False
    nb.rebuild()  # the confirmation is truth: a rebuild keeps it
    assert nb.store.q1("SELECT COUNT(*) FROM reading_confirmations")[0] == 1
    assert nb.store.reading_record(n["d22"])["related"] == stored["related"]


#: What a revision IS; only the bookkeeping of its application (applied_index, applied_kg, result_json, abandoned,
#: error) may move after it was written.
REVISION_CONTENT = ("revision_id", "note_id", "attempt", "action", "target", "at", "recorded_at", "run_id", "ops_json",
                    "summary", "source_json", "warnings_json", "context_json")


def test_the_revision_log_is_append_only_on_every_path(nb):
    """ADR 0003 (INT2): no code path changes a written revision. The store refuses it (trigger
    ``revisions_append_only``), and a scenario through every re-understanding path (deep re-reading with its
    spread, unchanged re-reading, the no-call check, a consolidation-style pass, recover, rebuild) leaves every
    revision as it was written. MUTATION: drop the trigger (the direct UPDATE goes through) / write the confirmation
    into revisions.context_json again (the trigger aborts the re-reading)."""
    import sqlite3

    w, n = zorn_world(nb)
    snap = lambda: {r["revision_id"]: tuple(r[k] for k in REVISION_CONTENT)  # noqa: E731
                    for r in nb.store.q(f"SELECT {', '.join(REVISION_CONTENT)} FROM revisions")}
    before = snap()
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        with nb.store.tx() as c:
            c.execute("UPDATE revisions SET context_json='{}' WHERE revision_id=?", (next(iter(before)),))
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        with nb.store.tx() as c:
            c.execute("UPDATE revisions SET summary='edited' WHERE applied_index=1")
    reread_d20(w, n)
    w.read("", day(22), [("m1", "the budget board", "org", n["board"], None)],
           [stmt("m1", "ex:approved", "the budget")], note_id=n["d22"], reinterpret=True)
    cons.digest_unchanged(nb, n["d22"])
    nb.recover()
    nb.rebuild()
    after = snap()
    assert {k: after[k] for k in before} == before and len(after) == len(before) + 1  # day 20's re-reading added


def test_schema_3_to_4_adds_the_change_records_and_the_refile_rows_in_order(tmp_path):
    """INT2 (R6 x W27): a schema-3 notebook (no ``idea_changes``, no propagated mark columns, no ``refile_runs``)
    opens as schema 4 with R6's tables and flag and W27's repair table, and both are usable. MUTATION: drop
    refile_runs from the DDL or _flag_propagation_record."""
    import sqlite3

    from abstractnote.store.schema import PROPAGATION_RECORD_META

    path = tmp_path / "old"
    nbk = Notebook.open(path)
    try:
        World(nbk).read("The budget board meets on Fridays.", day(15), [("m1", "the budget board", "org", None, None)],
                        [stmt("m1", "ex:meets_on", "Fridays")])
        db = nbk.store.path
    finally:
        nbk.close()
    conn = sqlite3.connect(db)
    with conn:
        conn.execute("DROP TABLE idea_changes")
        conn.execute("DROP TABLE refile_runs")
        conn.execute("DELETE FROM meta WHERE key=?", (PROPAGATION_RECORD_META,))
        conn.execute("UPDATE meta SET value='3' WHERE key='schema_version'")
    conn.close()
    nbk = Notebook.open(path)
    try:
        s = nbk.store
        assert s.meta_get("schema_version") == 5  # R7: schema 5 adds gate_decisions and edge_conductance
        flag = s.meta_get(PROPAGATION_RECORD_META)
        assert flag["schema"] == 4 and flag["since_seq"] >= 1
        assert s.q1("SELECT COUNT(*) FROM idea_changes")[0] == 0
        assert s.q1("SELECT COUNT(*) FROM refile_runs")[0] == 0
        assert s.q1("SELECT COUNT(*) FROM reading_confirmations")[0] == 0
        cols = {r[1] for r in s.q("PRAGMA table_info(reinterpret_marks)")}
        assert {"reason_code", "source_entity_id", "impact", "hops"} <= cols
    finally:
        nbk.close()


# ----------------------------------------------------------------------------- budget, bound, rebuild, recovery


def test_a_budget_stop_leaves_the_propagated_marks_and_says_so(nb):
    """P5: the run ends with the budget spent before the stage: left_out.reinterpret = the waiting notes,
    left_out.propagated = those waiting only because of a propagated mark, with their source, and the notice says
    so; the marks wait for the next run. MUTATION: drop the propagated count in _left_out."""
    w, n = zorn_world(nb)
    reread_d20(w, n)
    waiting = nb.store.pending_marks()
    assert {p["note_id"] for p in waiting} == {n["d12"], n["d22"]}
    cons.begin(nb)
    last = cons.end(nb, "completed", {"budget_spent": True, "not_reached": {"reinterpret": len(waiting)}})
    lo = last["left_out"]
    assert last["status"] == "completed_with_gaps" and lo["reinterpret"] == 2 and lo["propagated"] == 1
    assert lo["propagated_from"] == [{"entity_id": n["zorn"], "name": "Zorn", "notes": 1}]
    assert "2 notes could not be understood again this time, of which 1 follows from ideas understood differently" \
        in last["notice"]
    assert len(nb.store.pending_marks()) == 2


def test_the_bound_before_the_run_covers_what_follows(nb):
    """The count that may follow is readable before the run and is an upper bound of the propagated marks the
    re-readings raise (P1). MUTATION: may_follow returns 0; the forward filter dropped (it counts day 15)."""
    w, n = zorn_world(nb)
    state = nb.reinterpret_state()
    bound = state["propagated"]["may_follow"]
    assert bound >= 1 and {x["entity_id"] for x in state["propagated"]["may_follow_from"]} >= {n["zorn"]}
    reread_d20(w, n)
    realised = {m["note_id"] for m in nb.store.reinterpret_marks() if m["reason_code"] == "propagated"}
    assert 1 <= len(realised) <= bound
    assert bound == 1  # day 22 only: day 15 is before day 20, and day 12 uses both ideas itself
    view = cons.state(nb)["reinterpret"]["propagated"]
    assert view["pending"] == 1 and view["by_source"][0]["entity_id"] == n["zorn"]


def test_rebuild_copies_the_change_records_and_spreads_nothing(nb):
    """The records are truth: a rebuild copies them, replays the re-understanding without measuring or marking
    again. MUTATION: drop idea_changes from the rebuild copy."""
    w, n = zorn_world(nb)
    reread_d20(w, n)
    snap = lambda: ([tuple(r) for r in nb.store.q("SELECT revision_id, entity_id, deep, spread, spread_json FROM"  # noqa: E731
                                                  " idea_changes ORDER BY 1, 2")],
                    [tuple(r) for r in nb.store.q("SELECT note_id, reason_code, impact FROM reinterpret_marks ORDER BY"
                                                  " mark_id")])
    before = snap()
    assert before[0]
    nb.rebuild()
    assert snap() == before


def test_a_stop_between_the_apply_and_the_spread_is_completed_by_recover(nb, monkeypatch):
    """A crash after the index projection and before the spread: recover finishes the revision, measures it and
    raises the marks once. MUTATION: _propagate only on a first apply (not on recover)."""
    w, n = zorn_world(nb)
    real = Notebook._propagate

    def boom(self: Notebook, rid: str) -> None:
        raise RuntimeError("stopped")

    monkeypatch.setattr(Notebook, "_propagate", boom)
    with pytest.raises(RuntimeError):
        reread_d20(w, n)
    assert nb.store.q1("SELECT COUNT(*) FROM idea_changes WHERE note_id=?", (n["d20"],))[0] == 0
    assert marks_of(nb, n["d22"]) == []
    monkeypatch.setattr(Notebook, "_propagate", real)
    assert nb.recover()
    assert nb.store.q1("SELECT COUNT(*) FROM idea_changes WHERE deep=1")[0] >= 1
    assert [m["reason_code"] for m in marks_of(nb, n["d22"])] == ["propagated"]
    nb.recover()
    assert len(marks_of(nb, n["d22"])) == 1


def test_no_spread_without_the_embedder(nb):
    """Never measure or spread without the notebook's embedder: it fails loudly. MUTATION: drop require_embedder in
    Notebook._propagate."""
    w, n = zorn_world(nb)
    r = reread_d20(w, n)
    rid = r["out"]["revision_id"]
    with nb.store.tx() as c:
        c.execute("DELETE FROM idea_changes")
        row = json.loads(nb.store.revision_row(rid)["result_json"])
        row.pop("propagation")
        c.execute("UPDATE revisions SET result_json=? WHERE revision_id=?", (json.dumps(row), rid))
    keep = nb.cache.embedder
    nb.cache.embedder = None
    try:
        with pytest.raises(ValueError):
            nb._propagate(rid)
    finally:
        nb.cache.embedder = keep


def test_the_run_view_and_the_gaps_say_how_many_follow_from_which_idea(nb):
    """The Processing page's words come from the run view: during the reinterpret stage ``propagated`` = {notes,
    names, sources} of the pass. MUTATION: drop the propagated count of the run view."""
    w, n = zorn_world(nb)
    reread_d20(w, n)
    cons.begin(nb)
    cons.progress(nb, "reinterpret", 0, 2)
    view = cons.run_view(nb)
    assert view["propagated"] == {"notes": 1, "names": ["Zorn"], "sources": 1}
    cons.end(nb, "completed")


# ----------------------------------------------------------------------------- the real consolidate flow


def test_the_flow_reads_the_proximal_note_in_the_same_pass_with_the_block_and_a_second_pass_calls_nothing(tmp_path):
    """P1 through capture, drain and note-consolidate (scripted model): the archive marks day 12 and day 20; the
    stage reads them (tier A), day 20's re-reading changes Zorn deeply, day 22 is marked and read in the same pass
    (tier B) with the "Understood differently" block in its linking prompt; the second consolidation makes no note
    call. MUTATIONS: drop the propagated block from link_batches (no label in the prompt); drop the spread."""
    from abstractnote.flows.runner import NoteRunner, ScriptedLLM
    from abstractnote.process.packets import CHANGED_LABEL
    from abstractnote.tools import bind

    texts = {"d12": "The Zorn board is the budget board of Zorn.", "d15": "The Zorn board meets on Fridays.",
             "d20": "Zorn is a company we work with.", "d22": "The Zorn board approved the budget.",
             "d05": "Zorn is the codename of the LUT project."}
    seen: Dict[str, int] = {}
    prompts: List[str] = []

    def ext(ms: Sequence[Tuple[str, str]], cl: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        return {"summary": "A note.", "mentions": [mention(f"m{i + 1}", n, k) for i, (n, k) in enumerate(ms)],
                "claims": list(cl), "tasks": [], "time_refs": [], "topics": [], "document_date": None}

    def extraction(prompt: str) -> Dict[str, Any]:
        key = next(k for k, t in texts.items() if t in prompt)
        seen[key] = seen.get(key, 0) + 1
        again = seen[key] > 1
        board, zorn = ("the Zorn board", "org"), ("Zorn", "org")
        return {"d12": ext([board, zorn], [stmt("m1", "ex:part_of", "m2", literal=False)]),
                "d15": ext([board], [stmt("m1", "ex:meets_on", "Fridays")]),
                "d20": ext([zorn], [stmt("m1", "ex:kind", "project") if again else stmt("m1", "ex:is_a", "company")]),
                "d22": ext([board], [stmt("m1", "ex:approved", "the budget plan" if again else "the budget")]),
                "d05": ext([zorn], [stmt("m1", "ex:codename_of", "the LUT project")])}[key]

    def linking(prompt: str) -> Dict[str, Any]:
        prompts.append(prompt)
        lines = prompt.splitlines()
        ms = []
        for i, ln in enumerate(lines):
            if ln[:2] == "m1" or ln[:2] == "m2":
                ref, name = ln.split(" ", 1)[0], ln.split(" ", 1)[1].split(" — ")[0]
                reuse = i + 1 < len(lines) and lines[i + 1].strip().startswith("[0]")
                ms.append(ml(ref, "reuse", 0) if reuse else ml(ref, "create", name=name, kind="org", summary=f"{name}."))
        return {"mentions": ms, "claims": [], "tasks": [], "revision_summary": "x"}

    llm = ScriptedLLM({"QuickFiling": {"title": "t", "kind": "fact", "topics": [], "urgency": "normal", "language": "en",
                                       "one_line": "x"},
                       "Extraction": extraction, "Linking": linking, "CommunityLabels": {"communities": []},
                       "EntitySummaries": {"summaries": []}, "Reflections": {"items": []}})
    nb_dir = tmp_path / "nb"
    runner = NoteRunner.with_llm(llm, bind(nb_dir))
    for key, at in (("d12", day(12)), ("d15", day(15)), ("d20", day(20)), ("d22", day(22)), ("d05", day(5))):
        runner.run_capture(texts[key], idempotency_key=key, occurred_at=at, source={"client": "cli"})
        assert runner.run_drain(max_items=5, budget_s=60)["processed"] == 1
    nbk = Notebook.open(nb_dir)
    try:
        by = {k: next(r["note_id"] for r in nbk.store.q("SELECT * FROM notes") if r["text"] == t) for k, t in texts.items()}
        assert {m["note_id"] for m in nbk.store.reinterpret_marks()} == {by["d12"], by["d20"]}
        assert nbk.reinterpret_state()["propagated"]["may_follow"] >= 1
    finally:
        nbk.close()
    calls = lambda: sum(1 for c in llm.calls if c["schema"] in ("Extraction", "Linking"))  # noqa: E731
    before, n_prompts = calls(), len(prompts)
    runner.run("note-consolidate", {})
    nbk = Notebook.open(nb_dir)
    try:
        order = [r[0] for r in nbk.store.q("SELECT note_id FROM revisions WHERE action='note.reinterpret' ORDER BY seq")]
        assert by["d20"] in order and by["d22"] in order and order.index(by["d20"]) < order.index(by["d22"])
        [m] = [dict(r) for r in nbk.store.q("SELECT * FROM reinterpret_marks WHERE note_id=?", (by["d22"],))]
        assert m["reason_code"] == "propagated" and m["outcome"] == "reinterpreted"
        assert nbk.store.pending_marks() == []
    finally:
        nbk.close()
    d22_prompts = [p for p in prompts[n_prompts:] if "the Zorn board" in p and CHANGED_LABEL in p]
    assert d22_prompts and "no longer read as: Zorn ex:is_a “company”" in d22_prompts[0]
    first = calls() - before
    runner.run("note-consolidate", {})
    assert calls() - before - first == 0


def test_relations_replaced_and_withdrawn_statements_leave_the_graph(nb):
    """R: Alpha depends on Beta, read again as depending on Gamma: Alpha's relations are all replaced (R = 1), and
    the withdrawn statement no longer ties Alpha to Beta in the reliance graph. MUTATIONS: R always 0; withdrawn
    (reinterpreted) statements kept in the graph."""
    w = World(nb)
    w.read("Gamma is a project.", day(2), [("m1", "Gamma", "project", None, None)], [stmt("m1", "ex:is_a", "project")])
    x = w.read("Alpha depends on Beta, not Gamma.", day(10), [("m1", "Alpha", "project", None, None),
                                                              ("m2", "Beta", "project", None, None),
                                                              ("m3", "Gamma", "project", w.eid("Gamma"), None)],
               [stmt("m1", "ex:depends_on", "m2", literal=False)])["note_id"]
    a, b, g = w.eid("Alpha"), w.eid("Beta"), w.eid("Gamma")
    nb.mark_reinterpret([x], "asked")
    r = w.read("", day(10), [("m1", "Alpha", "project", a, None), ("m2", "Beta", "project", b, None),
                             ("m3", "Gamma", "project", g, None)], [stmt("m1", "ex:depends_on", "m3", literal=False)],
               note_id=x, reinterpret=True)
    rec = {e["entity_id"]: e for e in nb.idea_changes(r["out"]["revision_id"])}
    assert rec[a]["relation_share"] == 1.0 and rec[b]["relation_share"] == 1.0 and rec[a]["deep"]
    # Alpha's one neighbour now is Gamma, which this re-reading touched itself (ADR 0008 §4 marks its users): no hop
    assert rec[a]["spread"] and rec[a]["spread_json"]["reached"] == []
    with nb.store.lock:
        near = prop.neighbours(nb.store.conn, prop._canon_map(nb.store.conn), a)
    assert set(near) == {g}


def test_an_ended_statement_weighs_half_and_a_withdrawn_one_nothing():
    """The reliance weights: open 1, ended or superseded ENDED_CLAIM_WEIGHT, retracted or reinterpreted 0.
    MUTATION: every statement weighs 1."""
    rows = [({"status": "active", "valid_until": None}, 1.0), ({"status": "active", "valid_until": day(1)}, 0.5),
            ({"status": "superseded", "valid_until": None}, 0.5), ({"status": "retracted", "valid_until": None}, 0.0),
            ({"status": "reinterpreted", "valid_until": day(1)}, 0.0)]
    assert [prop._weight(r, T) for r, _ in rows] == [w for _, w in rows]
