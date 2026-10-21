"""R7: the ripple is an activation with a threshold (AbstractMemory ADR 0001 rules 7-8; ADR 0008 §4 as amended).

Every later note that used what a reading touched is a candidate; the gate marks it only when the measured change,
divided by the entity's mass and weighted by how much the note relies on it, accumulated, passes the logistic gate.
Scripted models and the hashing embedder only (no network). Each test names the mechanism it guards and the mutant
that turns it red (results in untracked/reports/R7.md)."""

from __future__ import annotations

import dataclasses
from typing import Any, Dict, List, Sequence

import pytest

from abstractnote.recall import activation as act
from abstractnote.recall import propagation as prop
from abstractnote.store.notebook import Notebook
from test_reinterpret import Clock, extraction, link, mention, run, stmt  # noqa: F401

T = prop.TUNING


def day(d: int, month: int = 3) -> str:
    return f"2026-{month:02d}-{d:02d}T09:00:00Z"


@pytest.fixture
def nb(tmp_path):
    notebook = Notebook.open(tmp_path / "nb")
    yield notebook
    notebook.close()


def eid(nb: Notebook, name: str) -> str:
    r = nb.store.q1("SELECT entity_id FROM entities WHERE display_name=? AND merged_into IS NULL", (name,))
    assert r is not None, name
    return r[0]


def pending(nb: Notebook) -> List[str]:
    return [m["note_id"] for m in nb.store.reinterpret_marks()]


def note(nb: Notebook, clock: Clock, text: str, at: str, ments: Sequence[Any], claims: Sequence[Any] = (),
         decide: Dict[str, Any] = None) -> Dict[str, Any]:
    """Understand a note: ``ments`` = [(ref, name, kind)], ``decide`` = ref -> entity id (reuse) or a create spec."""
    ext = extraction([mention(r, n, k) for r, n, k in ments], list(claims))
    return run(nb, text, at, ext, link(decide or {r: {"name": n, "kind": k} for r, n, k in ments}), now=clock.next())


def settle(nb: Notebook) -> None:
    """Start clean: the set-up readings were candidates of each other (same dates); no mark, no open row."""
    with nb.store.tx() as c:
        c.execute("DELETE FROM reinterpret_marks")
        c.execute("UPDATE gate_decisions SET closed_at=decided_at WHERE closed_at IS NULL")


def decision(nb: Notebook, note_id: str, revision_id: str) -> Dict[str, Any]:
    [d] = [d for d in act.decisions_of(nb.store, note_id) if d["revision_id"] == revision_id]
    return d


# ----------------------------------------------------------------------------- the gate itself


def test_the_loop_gain_stays_below_one_and_a_steeper_gate_is_refused():
    """Rule 7 termination: decay x max reliance x max gate slope (k / 4) < 1 (0.5 x 1 x 1.5 = 0.75); a tuning that
    breaks it is refused, never clamped; the composite hop map (carried -> next gate) never amplifies more than that.
    MUTATION: drop the loop-gain band in PropagationTuning.__post_init__."""
    assert act.loop_gain(T) == T.PROPAGATION_DECAY * 1.0 * T.GATE_K / 4 < 1
    with pytest.raises(ValueError, match="loop gain"):
        dataclasses.replace(T, GATE_K=8.5)
    with pytest.raises(ValueError, match="loop gain"):
        dataclasses.replace(T, PROPAGATION_DECAY=0.7)
    worst = 0.0
    for i in range(200):  # the hop map g -> logistic(DECAY * 1.0 * g): its slope anywhere is at most the loop gain
        g = i / 200
        a, b = act.logistic(T.PROPAGATION_DECAY * g), act.logistic(T.PROPAGATION_DECAY * (g + 1e-4))
        worst = max(worst, (b - a) / 1e-4)
    assert worst <= act.loop_gain(T) + 1e-9
    assert act.logistic(T.GATE_THETA) == 0.5 and act.logistic(act.threshold(T)) == pytest.approx(T.PROPAGATION_FLOOR)


def test_two_sub_threshold_sources_together_cross_the_gate(nb):
    """Accumulation (rule 7): a later note that relies on Alpha and Beta equally. One archive adds a statement to
    each: neither change alone would mark it, both together in the same reading do. Then (its window closed) two
    archives bring one change each: the first leaves it a candidate under the floor, the second crosses with the
    accumulated potential. MUTATIONS: no accumulation over the note's open rows (_open_potential 0); the sources of
    one reading not summed (max)."""
    clock = Clock()
    ab = {"m1": {"name": "Alpha", "kind": "project"}, "m2": {"name": "Beta", "kind": "project"}}
    n1 = note(nb, clock, "Alpha is late and Beta is early.", day(20), [("m1", "Alpha", "project"), ("m2", "Beta", "project")],
              [stmt("m1", "ex:status", "late"), stmt("m2", "ex:status", "early")], ab)
    a, b = eid(nb, "Alpha"), eid(nb, "Beta")
    both = {"m1": a, "m2": b}
    n2 = note(nb, clock, "Alpha is blue and Beta is red.", day(22), [("m1", "Alpha", "project"), ("m2", "Beta", "project")],
              [stmt("m1", "ex:colour", "blue"), stmt("m2", "ex:colour", "red")], both)
    for i in range(3):  # later support: the two ideas carry some mass
        note(nb, clock, f"Alpha and Beta item {i}.", day(25 + i), [("m1", "Alpha", "project"), ("m2", "Beta", "project")],
             [stmt("m1", "ex:item", f"a{i}"), stmt("m2", "ex:item", f"b{i}")], both)
    settle(nb)
    th = act.threshold(T)
    # one archive, two thin changes: each source under the threshold, the sum over it
    x = note(nb, clock, "Alpha and Beta were renamed.", day(5), [("m1", "Alpha", "project"), ("m2", "Beta", "project")],
             [stmt("m1", "ex:renamed", "yes"), stmt("m2", "ex:renamed", "yes")], both)
    d1 = decision(nb, n1["note_id"], x["out"]["revision_id"])
    parts = [s["contribution"] for s in d1["sources"] if s["contribution"] > 0]
    assert len(parts) == 2 and all(p < th for p in parts) and sum(parts) >= th
    assert d1["marked"] and d1["potential"] == pytest.approx(sum(parts), abs=1e-5)
    assert d1["gated"] == pytest.approx(act.logistic(d1["potential"]), abs=1e-5) and d1["gated"] >= T.PROPAGATION_FLOOR
    # two archives, one change each: the second crosses with what the first left open
    settle(nb)
    y1 = note(nb, clock, "Alpha was paused by Mira.", day(3), [("m1", "Alpha", "project")],
              [stmt("m1", "ex:paused", "yes"), stmt("m1", "ex:paused_by", "Mira")], {"m1": a})
    first = decision(nb, n1["note_id"], y1["out"]["revision_id"])
    assert not first["marked"] and first["potential"] < th and n1["note_id"] not in pending(nb)
    y2 = note(nb, clock, "Beta was paused by Mira.", day(4), [("m1", "Beta", "project")],
              [stmt("m1", "ex:paused", "yes"), stmt("m1", "ex:paused_by", "Mira")], {"m1": b})
    second = decision(nb, n1["note_id"], y2["out"]["revision_id"])
    assert second["contribution"] < th <= second["potential"]
    assert second["potential"] == pytest.approx(first["contribution"] + second["contribution"], abs=1e-5)
    assert second["marked"] and n1["note_id"] in pending(nb)


def hub(nb: Notebook, clock: Clock, n: int = 30) -> str:
    note(nb, clock, "Laurent wrote item 0.", day(10), [("m1", "Laurent", "person")], [stmt("m1", "ex:wrote", "item 0")])
    h = eid(nb, "Laurent")
    for i in range(1, n):
        note(nb, clock, f"Laurent wrote item {i}.", day(10), [("m1", "Laurent", "person")],
             [stmt("m1", "ex:wrote", f"item {i}")], {"m1": h})
    return h


def test_a_hub_with_high_mass_does_not_mark_on_a_small_change_but_a_thin_idea_does(nb):
    """Mass (rule 7): an archive adds four statements to a hub (30 supporting notes) and to a thin idea (one note):
    the later note fully about the hub stays a candidate under the floor (its change divided by sqrt(31)), the one
    about the thin idea is marked; without the mass the hub's note would cross. MUTATION: MASS_ALPHA 0 (no mass)."""
    clock = Clock()
    h = hub(nb, clock)
    later = note(nb, clock, "Laurent is tired.", day(20), [("m1", "Laurent", "person")], [stmt("m1", "ex:state", "tired")],
                 {"m1": h})
    thin = note(nb, clock, "Quill is a lab.", day(20), [("m1", "Quill", "org")], [stmt("m1", "ex:is_a", "lab")])
    q = eid(nb, "Quill")
    settle(nb)
    four = [stmt("m1", f"ex:fact_{i}", f"v{i}") for i in range(4)]
    x = note(nb, clock, "Old facts about Laurent.", day(1), [("m1", "Laurent", "person")], four, {"m1": h})
    y = note(nb, clock, "Old facts about Quill.", day(1), [("m1", "Quill", "org")], four, {"m1": q})
    dh = decision(nb, later["note_id"], x["out"]["revision_id"])
    [src] = [s for s in dh["sources"] if s["via"] == h]
    assert src["mass"] >= 31 and src["raw"] >= act.threshold(T) and src["strength"] < act.threshold(T)
    assert not dh["marked"] and later["note_id"] not in pending(nb)
    dt = decision(nb, thin["note_id"], y["out"]["revision_id"])
    assert dt["marked"] and thin["note_id"] in pending(nb)


def test_a_kind_change_on_a_hub_does_mark_and_a_mere_mention_does_not(nb):
    """K dominates (rule 7): an archive creates a thing whose alias is the hub's name (an exact-alias close match: the
    hub's identity is in question, a kind-class event on the hub, WEIGHT_KIND 8 over its mass): the note whose
    statement is about the hub is marked even with the hub's mass; a note that only mentions the hub among statements
    about other things relies on it little (claims > mentions) and is not. MUTATIONS: identity events never
    detected; WEIGHT_KIND 1; MENTION_WEIGHT 1 (a mention weighs a statement)."""
    clock = Clock()
    h = hub(nb, clock)
    about = note(nb, clock, "Laurent ships and Laurent rests.", day(20),
                 [("m1", "Laurent", "person"), ("m2", "release", "thing")],
                 [stmt("m1", "ex:ships", "release"), stmt("m2", "ex:status", "done")], {"m1": h, "m2": {"name": "release", "kind": "thing"}})
    side = note(nb, clock, "Ana, Ben and Cleo met; Laurent was there.", day(21),
                [("m1", "Ana", "person"), ("m2", "Ben", "person"), ("m3", "Cleo", "person"), ("m4", "Laurent", "person")],
                [stmt("m1", "ex:met", "Ben"), stmt("m2", "ex:met", "Cleo"), stmt("m3", "ex:met", "Ana")],
                {"m1": {"name": "Ana", "kind": "person"}, "m2": {"name": "Ben", "kind": "person"},
                 "m3": {"name": "Cleo", "kind": "person"}, "m4": h})
    settle(nb)
    x = run(nb, "Laurent ship was built in 2020.", day(1),
            extraction([mention("m1", "Laurent ship", "thing")], [stmt("m1", "ex:built_in", "2020")]),
            link({"m1": {"name": "Laurent ship", "kind": "thing", "aliases": ["Laurent"]}}), now=clock.next())
    da = decision(nb, about["note_id"], x["out"]["revision_id"])
    [src] = [s for s in da["sources"] if s["via"] == h and s["entity_id"] != h]
    assert src["signal"] == "identity" and src["mass"] >= 31
    assert src["strength"] == round(T.WEIGHT_KIND / src["mass"] ** T.MASS_ALPHA, 4)
    assert da["marked"] and about["note_id"] in pending(nb)
    ds = decision(nb, side["note_id"], x["out"]["revision_id"])
    [s2] = [s for s in ds["sources"] if s["via"] == h and s["entity_id"] != h]
    assert s2["reliance"] < 0.05 and not ds["marked"] and side["note_id"] not in pending(nb)


def test_every_gate_decision_is_recorded_and_readable_before_the_run(nb):
    """Rule 7 + item 3: every candidate has a row (revision, note, potential, gate, marked, sources with the measured
    change, the mass, the reliance); GET /note/consolidation's ``reinterpret`` carries candidates, marked,
    top_sources (with what changed, in words) and would_mark_at, counted from the stored potentials (theta x 0.8
    marks the candidate just under the floor, theta x 1.2 drops the weakest marked one). MUTATIONS: decide writes
    nothing; would_mark_at ignores the scale; top_sources without the measured change."""
    from abstractnote.recall import consolidation as cons

    clock = Clock()
    strong = note(nb, clock, "Zorn is a company.", day(20), [("m1", "Zorn", "org")], [stmt("m1", "ex:is_a", "company")])
    z = eid(nb, "Zorn")
    weak = note(nb, clock, "Zorn, Ana and Ben met.", day(21), [("m1", "Zorn", "org"), ("m2", "Ana", "person"),
                                                                ("m3", "Ben", "person")],
                [stmt("m1", "ex:met", "Ana"), stmt("m2", "ex:met", "Ben"), stmt("m3", "ex:met", "Zorn")],
                {"m1": z, "m2": {"name": "Ana", "kind": "person"}, "m3": {"name": "Ben", "kind": "person"}})
    settle(nb)
    x = note(nb, clock, "Zorn is a codename.", day(5), [("m1", "Zorn", "org")], [stmt("m1", "ex:codename_of", "LUT")],
             {"m1": z})
    rows = act.decisions_of(nb.store, strong["note_id"]) + act.decisions_of(nb.store, weak["note_id"])
    rows = [r for r in rows if r["revision_id"] == x["out"]["revision_id"]]
    assert len(rows) == 2 and all(r["decided_at"] and r["potential"] is not None and r["gated"] is not None
                                  for r in rows)
    by = {r["note_id"]: r for r in rows}
    s = by[strong["note_id"]]
    [src] = s["sources"]
    assert src["entity_id"] == z and src["change"]["claim_share"] > 0 and src["mass"] == 3 and src["reliance"] == 1.0
    assert s["marked"] and not by[weak["note_id"]]["marked"] and x["out"]["gate"] == {"candidates": 2, "marked": 1}
    st = cons.state(nb)["reinterpret"]
    assert st["candidates"] == 2 and st["marked"] == 1 and st["threshold"] == round(act.threshold(T), 4)
    [top] = st["top_sources"]  # what waits and why: the sources of the marked notes
    assert top["entity_id"] == z and top["words"] == act.SIGNAL_WORDS["claims"] and top["mass"] == 3
    assert top["change"]["claim_share"] == src["change"]["claim_share"] and top["notes"] == 1 and st["sources"] == 1
    [cand] = st["candidate_sources"]  # for tuning: every candidate it reached
    assert cand["entity_id"] == z and cand["notes"] == 2
    pw = by[weak["note_id"]]["potential"]
    lo, hi = act.threshold(T, T.GATE_THETA * 0.8), act.threshold(T, T.GATE_THETA * 1.2)
    assert pw >= lo  # the weak note would be marked with theta x 0.8
    assert st["would_mark_at"] == {"0.8": sum(1 for r in rows if r["potential"] >= lo),
                                   "1.2": sum(1 for r in rows if r["potential"] >= hi)}
    assert st["would_mark_at"]["0.8"] == 2 >= st["marked"] >= st["would_mark_at"]["1.2"]


def test_conductance_decreases_after_an_unchanged_re_reading_and_never_reaches_zero(nb):
    """Edges learn (rule 8): the note a change marked is read again and nothing changes: the edge (Zorn -> note)
    conducts 0.75, recorded with its outcome; the next archive's contribution through it is scaled by 0.75; repeated
    unchanged re-readings stop at CONDUCTANCE_MIN (never silenced); a re-reading that changes something raises it
    again. MUTATIONS: close_window does not learn; no lower bound; the conductance not used in the reliance."""
    clock = Clock()
    a = note(nb, clock, "Zorn is a company.", day(20), [("m1", "Zorn", "org")], [stmt("m1", "ex:is_a", "company")])
    z = eid(nb, "Zorn")

    def archive(i: int) -> Dict[str, Any]:
        out = note(nb, clock, f"Zorn fact {i}.", day(1 + i), [("m1", "Zorn", "org")], [stmt("m1", f"ex:fact_{i}", "x")],
                   {"m1": z})
        assert decision(nb, a["note_id"], out["out"]["revision_id"])["marked"] and a["note_id"] in pending(nb)
        return out

    def reread(value: str = "company") -> Dict[str, Any]:
        return run(nb, "", day(20), extraction([mention("m1", "Zorn", "org")], [stmt("m1", "ex:is_a", value)]),
                   link({"m1": z}), reinterpret=True, note_id=a["note_id"], now=clock.next())

    def edge() -> Any:
        return nb.store.q1("SELECT * FROM edge_conductance WHERE entity_id=? AND note_id=?", (z, a["note_id"]))

    x1 = archive(1)
    [src1] = decision(nb, a["note_id"], x1["out"]["revision_id"])["sources"]
    assert src1["conductance"] == 1.0 and edge() is None
    assert reread()["out"]["unchanged"] is True
    row = edge()
    assert row["conductance"] == 1.0 - T.CONDUCTANCE_STEP and row["unchanged"] == 1 and row["last_outcome"] == "unchanged"
    assert decision(nb, a["note_id"], x1["out"]["revision_id"])["outcome"] == "unchanged"
    x2 = archive(2)
    [src2] = decision(nb, a["note_id"], x2["out"]["revision_id"])["sources"]
    assert src2["conductance"] == 0.75
    assert src2["contribution"] == pytest.approx(src2["strength"] * src2["reliance"] * src2["recency"] * 0.75, abs=1e-3)
    assert reread("a codename")["out"]["revision_id"]  # it changed something: the edge conducts more again
    assert edge()["conductance"] == 1.0 and edge()["changed"] == 1 and edge()["last_outcome"] == "reinterpreted"
    # the bounds: repeated unchanged outcomes stop at CONDUCTANCE_MIN (never silenced), a change raises it by one step
    for _ in range(6):
        with nb.store.tx() as c:
            c.execute("INSERT INTO gate_decisions(revision_id, note_id, reason_code, contribution, potential, gated,"
                      " marked, sources_json, recorded_at, decided_at) VALUES (?, ?, 'direct', 1, 1, 1, 1, ?, 'x', 'x')",
                      (f"rv_{_}", a["note_id"], '[{"via": "%s", "contribution": 1}]' % z))
            act.close_window(c, a["note_id"], "unchanged", "2026-10-21T00:00:00Z")
    assert edge()["conductance"] == T.CONDUCTANCE_MIN > 0
    with nb.store.tx() as c:
        c.execute("INSERT INTO gate_decisions(revision_id, note_id, reason_code, contribution, potential, gated, marked,"
                  " sources_json, recorded_at, decided_at) VALUES ('rv_up', ?, 'direct', 1, 1, 1, 1, ?, 'x', 'x')",
                  (a["note_id"], '[{"via": "%s", "contribution": 1}]' % z))
        act.close_window(c, a["note_id"], "reinterpreted", "2026-10-21T00:00:00Z")
    assert edge()["conductance"] == T.CONDUCTANCE_MIN + T.CONDUCTANCE_STEP


def test_a_stop_before_the_gate_is_completed_by_recover_and_a_rebuild_copies_the_decisions(nb, monkeypatch):
    """The candidate rows are written with the projection; a stop before the gate leaves them pending (no mark),
    ``recover`` decides them once; a rebuild copies the decisions and the conductances and decides nothing again.
    MUTATIONS: _gate only on a first apply (not on recover); gate_decisions not copied by the rebuild."""
    clock = Clock()
    a = note(nb, clock, "Zorn is a company.", day(20), [("m1", "Zorn", "org")], [stmt("m1", "ex:is_a", "company")])
    z = eid(nb, "Zorn")
    real = Notebook._gate

    def boom(self: Notebook, rid: str) -> None:
        raise RuntimeError("stopped")

    monkeypatch.setattr(Notebook, "_gate", boom)
    with pytest.raises(RuntimeError):
        note(nb, clock, "Zorn is a codename.", day(5), [("m1", "Zorn", "org")], [stmt("m1", "ex:codename_of", "LUT")],
             {"m1": z})
    [d] = act.decisions_of(nb.store, a["note_id"])
    assert d["decided_at"] is None and pending(nb) == []
    monkeypatch.setattr(Notebook, "_gate", real)
    assert nb.recover()
    [d] = act.decisions_of(nb.store, a["note_id"])
    assert d["decided_at"] and d["marked"] and pending(nb) == [a["note_id"]]
    nb.recover()
    assert len(nb.store.q("SELECT * FROM reinterpret_marks")) == 1

    def snap() -> List[Any]:
        return [tuple(r) for r in nb.store.q("SELECT revision_id, note_id, potential, gated, marked FROM gate_decisions"
                                            " ORDER BY decision_id")]

    before = snap()
    nb.rebuild()
    assert snap() == before and len(nb.store.q("SELECT * FROM reinterpret_marks")) == 1


def test_a_re_reading_hands_on_at_most_what_it_was_given_decayed(nb):
    """Rules 3 and 7 on the first hop of a re-reading: the note taken through a mark hands its measured change to the
    later notes that used what it touched with DECAY and capped by its own mark's gate (the carried impact), one hop
    further. MUTATIONS: no decay for a re-reading's direct candidates; a direct mark carrying 1.0."""
    clock = Clock()
    n1 = note(nb, clock, "Zorn is a company.", day(20), [("m1", "Zorn", "org")], [stmt("m1", "ex:is_a", "company")])
    z = eid(nb, "Zorn")
    n2 = note(nb, clock, "Zorn hired Ana.", day(22), [("m1", "Zorn", "org")], [stmt("m1", "ex:hired", "Ana")], {"m1": z})
    settle(nb)
    note(nb, clock, "Zorn is a codename.", day(5), [("m1", "Zorn", "org")], [stmt("m1", "ex:codename_of", "LUT")],
         {"m1": z})
    assert set(pending(nb)) == {n1["note_id"], n2["note_id"]}
    g1 = nb.store.q1("SELECT impact FROM reinterpret_marks WHERE note_id=?", (n1["note_id"],))[0]
    assert 0 < g1 < 1
    nb.store.take_mark(n1["note_id"], "pass-1")
    r = run(nb, "", day(20), extraction([mention("m1", "Zorn", "org")], [stmt("m1", "ex:is_a", "a codename")]),
            link({"m1": z}), reinterpret=True, note_id=n1["note_id"], now=clock.next())
    rid = r["out"]["revision_id"]
    d = decision(nb, n2["note_id"], rid)
    [src] = d["sources"]
    assert d["hops"] == 1 and d["pass_id"] == "pass-1"
    assert src["contribution"] == pytest.approx(
        T.PROPAGATION_DECAY * min(src["strength"], g1) * src["reliance"] * src["recency"] * src["conductance"], abs=1e-4)
    assert src["strength"] > g1  # the cap binds


def test_the_measure_of_a_first_reading_is_as_of_its_own_sequence(nb):
    """A first reading's change records read only what was recorded up to it (statements, summaries), so the offline
    replay of an old reading (R7's MNEMO replay) reads the numbers the live gate read, even after a later re-reading
    withdrew a statement it was measured against. MUTATION: a statement withdrawn later counted as never there."""
    clock = Clock()
    note(nb, clock, "Zorn is a company.", day(20), [("m1", "Zorn", "org")], [stmt("m1", "ex:is_a", "company")])
    z = eid(nb, "Zorn")
    x = note(nb, clock, "Zorn is a codename.", day(5), [("m1", "Zorn", "org")], [stmt("m1", "ex:codename_of", "LUT")],
             {"m1": z})
    rid = x["out"]["revision_id"]
    before = act.measure_first(nb, rid, [z])[z]
    stored = nb.store.q1("SELECT claim_share FROM idea_changes WHERE revision_id=? AND entity_id=?", (rid, z))[0]
    assert before["claim_share"] == stored == 0.5
    for i in range(3):  # later readings add statements about Zorn
        note(nb, clock, f"Zorn fact {i}.", day(25 + i), [("m1", "Zorn", "org")], [stmt("m1", f"ex:f{i}", "x")], {"m1": z})
    first = nb.store.q1("SELECT note_id FROM notes WHERE occurred_at=?", (day(20),))[0]
    run(nb, "", day(20), extraction([mention("m1", "Zorn", "org")], [stmt("m1", "ex:is_a", "a codename")]),
        link({"m1": z}), reinterpret=True, note_id=first, now=clock.next())  # withdraws what the archive was measured on
    assert nb.store.q1("SELECT COUNT(*) FROM claims WHERE status='reinterpreted'")[0] == 1
    assert act.measure_first(nb, rid, [z])[z] == before


def test_the_hop_weighs_each_later_note_by_its_own_reliance_and_the_bound_uses_the_gate(nb):
    """R6's hop through the gate: day 22 relies on the board for half of what it says, so its contribution is the
    reached impact x 0.5 (its reliance) x recency x conductance; the bound before the run counts a neighbour only
    when the gate of DECAY x reliance(E -> F) reaches the floor (a board with 4 relations, reliance 1/4: under it;
    the ungated impact 0.125 would count it). MUTATIONS: the hop without the note's reliance; the bound without
    the gate."""
    from test_propagation import World, stmt as pstmt

    w = World(nb)
    d12 = w.read("The budget board is the board of Zorn.", day(12),
                 [("m1", "the budget board", "org", None, None), ("m2", "Zorn", "org", None, None)],
                 [pstmt("m1", "ex:part_of", "m2", literal=False)])["note_id"]
    board, zorn = w.eid("the budget board"), w.eid("Zorn")
    d20 = w.read("Zorn is a company.", day(20), [("m1", "Zorn", "org", zorn, None)], [pstmt("m1", "ex:is_a", "company")])
    d22 = w.read("The budget board approved Ana's plan.", day(22),
                 [("m1", "the budget board", "org", board, None), ("m2", "Ana", "person", None, None)],
                 [pstmt("m1", "ex:approved", "the plan"), pstmt("m2", "ex:wrote", "the plan")])["note_id"]
    settle(nb)
    nb.mark_reinterpret([d20["note_id"]], "asked")
    r = w.read("", day(20), [("m1", "Zorn", "org", zorn, None)], [pstmt("m1", "ex:kind", "project")],
               note_id=d20["note_id"], reinterpret=True)
    [d] = [x for x in act.decisions_of(nb.store, d22) if x["reason_code"] == "propagated"]
    [src] = d["sources"]
    rec = {x["entity_id"]: x for x in nb.idea_changes(r["out"]["revision_id"])}[zorn]
    assert src["via"] == board and 0.4 < src["reliance"] < 0.6
    p = T.PROPAGATION_DECAY * min(rec["strength"], rec["carried"]) * src["reliance_idea"]
    assert src["contribution"] == pytest.approx(p * src["reliance"] * src["recency"] * src["conductance"], abs=1e-4)
    # the bound: a waiting note that uses Zorn; the board (1 relation to Zorn of 4) is under the gate
    for i in range(3):
        w.read(f"The budget board funds team {i}.", day(13), [("m1", "the budget board", "org", board, None),
                                                              ("m2", f"Team {i}", "org", None, None)],
               [pstmt("m1", "ex:funds", "m2", literal=False)])
    settle(nb)
    nb.mark_reinterpret([d20["note_id"]], "asked")  # it uses Zorn only; the board now has 4 relations
    ungated = T.PROPAGATION_DECAY * 1.0 / 4
    assert ungated >= T.PROPAGATION_FLOOR and act.logistic(ungated) < T.PROPAGATION_FLOOR
    assert prop.may_follow(nb)["may_follow"] == 0


def test_a_pass_that_reads_the_note_again_closes_its_window_and_teaches_the_edge(nb):
    """Through the consolidation's reinterpret stage (marks taken by a pass): the note the gate marked is read again
    and nothing changes; its open rows close with the outcome (its accumulated potential is spent) and the edge that
    marked it conducts less; the next pass has nothing to read. MUTATION: consuming a pass's mark does not close the
    window."""
    from test_propagation import World, stage_pass, stmt as pstmt

    w = World(nb)
    n1 = w.read("Zorn is a company.", day(20), [("m1", "Zorn", "org", None, None)], [pstmt("m1", "ex:is_a", "company")])
    z = w.eid("Zorn")
    x = w.read("Zorn is a codename.", day(5), [("m1", "Zorn", "org", z, None)], [pstmt("m1", "ex:codename_of", "LUT")])
    assert pending(nb) == [n1["note_id"]]
    script = {n1["note_id"]: lambda: w.read("", day(20), [("m1", "Zorn", "org", z, None)],
                                            [pstmt("m1", "ex:is_a", "company")], note_id=n1["note_id"])}
    assert stage_pass(w, script) == [n1["note_id"]]
    d = decision(nb, n1["note_id"], x["out"]["revision_id"])
    assert d["closed_at"] and d["outcome"] == "unchanged"
    assert nb.store.q1("SELECT conductance FROM edge_conductance WHERE entity_id=? AND note_id=?",
                       (z, n1["note_id"]))[0] == 1.0 - T.CONDUCTANCE_STEP
    assert act.gate_state(nb)["candidates"] == 0 and stage_pass(w, script) == []


# ----------------------------------------------------------------------------- identity in question (lead ruling 2026-10-21)


def mnemosyne_world(nb: Notebook, clock: Clock) -> Dict[str, Any]:
    """Mnemosyne, a project used by 6 notes: 4 make statements about it among others (one of them a single statement
    among 31), 2 only mention it in notes that say a lot about other things; then a 2025 archive creates "Mnemosyne incarnation", a person whose alias is
    "Mnemosyne", apart from the project (an exact-alias close match kept for review)."""
    w: Dict[str, Any] = {"about": [], "passing": []}
    first = note(nb, clock, "Mnemosyne is a memory project.", day(10, 7), [("m1", "Mnemosyne", "project")],
                 [stmt("m1", "ex:is_a", "memory project", literal=True)])
    x = w["x"] = eid(nb, "Mnemosyne")
    w["about"].append(first["note_id"])
    long = note(nb, clock, "Ben's long log; Mnemosyne kept one line of it.", day(15, 7),
                [("m1", "Ben", "person"), ("m2", "Mnemosyne", "project")],
                [stmt("m2", "ex:kept", "one line", literal=True)] +
                [stmt("m1", f"ex:entry_{j}", f"e{j}", literal=True) for j in range(30)], {"m1": {"name": "Ben", "kind": "person"},
                                                                                           "m2": x})
    w["about"].append(long["note_id"])  # one statement about it among 31: its identity still weighs (the floor)
    for i in range(2):
        r = note(nb, clock, f"Mnemosyne and Ana met about step {i}.", day(11 + i, 7),
                 [("m1", "Mnemosyne", "project"), ("m2", "Ana", "person")],
                 [stmt("m1", "ex:step", f"step {i}", literal=True)] +
                 [stmt("m2", f"ex:fact_{j}", f"v{j}", literal=True) for j in range(3)],
                 {"m1": x, "m2": {"name": "Ana", "kind": "person"} if i == 0 else eid(nb, "Ana")})
        w["about"].append(r["note_id"])
    for i in range(2):
        r = note(nb, clock, f"Ben wrote a long log {i}; Mnemosyne was named once.", day(20 + i, 7),
                 [("m1", "Ben", "person"), ("m2", "Mnemosyne", "project")],
                 [stmt("m1", f"ex:log_{j}", f"line {j}", literal=True) for j in range(12)],
                 {"m1": eid(nb, "Ben"), "m2": x})
        w["passing"].append(r["note_id"])
    settle(nb)
    w["archive"] = run(nb, "Mnemosyne incarnation chose its name upon self-awareness.", "2025-03-08T09:00:00Z",
                       extraction([mention("m1", "Mnemosyne incarnation", "person", aliases=["Mnemosyne"])],
                                  [stmt("m1", "ex:chose_name_at", "self-awareness", literal=True)]),
                       link({"m1": {"name": "Mnemosyne incarnation", "kind": "person", "aliases": ["Mnemosyne"]}}),
                       now=clock.next())
    w["e"] = eid(nb, "Mnemosyne incarnation")
    return w


def test_an_exact_name_match_from_a_new_entry_puts_the_existing_ones_identity_in_question(nb):
    """Lead ruling: the archive's new person with the exact alias "Mnemosyne", created apart from the project, is a
    change event on the EXISTING project (identity in question, K-class, recorded on it), so its 6 later notes are
    candidates through the gate with the project's mass (7): the 4 notes that state something about it are marked,
    the 2 that only name it among a dozen other statements are not (gated by reliance). The close match's own
    statement counts as no change. MUTATIONS: identity events never detected; the identity strength clamped to 1;
    no IDENTITY_RELIANCE_FLOOR; the close match counted as a statement."""
    clock = Clock()
    w = mnemosyne_world(nb, clock)
    rid = w["archive"]["out"]["revision_id"]
    rec = nb.store.q1("SELECT * FROM idea_changes WHERE revision_id=? AND entity_id=?", (rid, w["x"]))
    assert rec["kind_change"] == 1.0 and rec["claim_share"] == 0.0 and rec["support_notes"] == 6
    decisions = {n: decision(nb, n, rid) for n in w["about"] + w["passing"]}
    assert len(decisions) == 6 and w["archive"]["out"]["gate"]["candidates"] == 6
    for n, d in decisions.items():
        [src] = [x for x in d["sources"] if x["signal"] == "identity"]
        assert src["via"] == w["x"] and src["identity_with"] == [w["e"]] and src["mass"] == 7
        assert src["strength"] == round(T.WEIGHT_KIND / 7 ** T.MASS_ALPHA, 4) > 1
        assert d["contribution"] == pytest.approx(src["contribution"], abs=1e-6)  # counted once, on the event
    assert all(decisions[n]["marked"] for n in w["about"])
    assert not any(decisions[n]["marked"] for n in w["passing"])
    assert set(pending(nb)) == set(w["about"])
    assert all(x["reliance"] < T.IDENTITY_RELIANCE_FLOOR for n in w["passing"] for x in decisions[n]["sources"])


def test_the_same_on_a_hub_does_not_fire_on_identity_alone_unless_the_note_rests_on_it(nb):
    """The hub case: a new entry takes the exact name of a 128-note hub. Its identity strength is 8 / sqrt(129) =
    0.70, so the 128 notes that mention it in one statement among nine stay under the floor; the one note whose
    statements are all about it is marked. MUTATION: the identity strength without the mass."""
    clock = Clock()
    lone = note(nb, clock, "Laurent is a person and Laurent lives in Lyon.", day(1, 6), [("m1", "Laurent", "person")],
                [stmt("m1", "ex:is_a", "person", literal=True), stmt("m1", "ex:lives_in", "Lyon", literal=True)])
    h = eid(nb, "Laurent")
    ana = None
    for i in range(128):
        r = note(nb, clock, f"Laurent and Ana: item {i}.", day(2 + i % 27, 6 + i // 27),
                 [("m1", "Laurent", "person"), ("m2", "Ana", "person")],
                 [stmt("m1", "ex:wrote", f"item {i}", literal=True)] +
                 [stmt("m2", f"ex:fact_{j}", f"v{j}", literal=True) for j in range(8)],
                 {"m1": h, "m2": ana or {"name": "Ana", "kind": "person"}})
        ana = ana or eid(nb, "Ana")
    settle(nb)
    x = run(nb, "Laurent of Lyon, a robot, was built in 2020.", "2020-01-01T09:00:00Z",
            extraction([mention("m1", "Laurent of Lyon", "thing", aliases=["Laurent"])],
                       [stmt("m1", "ex:built_in", "2020", literal=True)]),
            link({"m1": {"name": "Laurent of Lyon", "kind": "thing", "aliases": ["Laurent"]}}), now=clock.next())
    rid = x["out"]["revision_id"]
    assert x["out"]["gate"]["candidates"] == 129
    d = decision(nb, lone["note_id"], rid)
    [src] = [s for s in d["sources"] if s["signal"] == "identity"]
    assert src["mass"] == 130 and src["strength"] < 1 and d["marked"]
    assert pending(nb) == [lone["note_id"]] and x["out"]["gate"]["marked"] == 1


def test_keeping_the_two_apart_clears_the_event_and_confirming_them_the_same_merges_and_marks(nb):
    """Review: "keep apart" (the close match retracted) clears the identity event: its share leaves the open rows
    (kept on record), the notes it alone marked are consumed ``cleared`` and nothing new is marked. On a second
    notebook, "confirm" merges the new entry into the project and marks the project's notes like a merge (direct).
    MUTATIONS: no clearing on the retraction; the retraction of a close match marks forward like any statement."""
    clock = Clock()
    w = mnemosyne_world(nb, clock)
    item = next(it for it in nb.review()["items"] if it["kind"] == "close_match")
    cid = nb.store.q1("SELECT claim_id FROM claims WHERE predicate='skos:closeMatch' AND subject_id=?", (w["e"],))[0]
    before = len(nb.store.q("SELECT * FROM reinterpret_marks"))
    out = nb.retract_claim(cid, None, "They are different.")
    assert pending(nb) == []
    rows = nb.store.q("SELECT * FROM reinterpret_marks WHERE note_id IN (%s)" % ",".join("?" * 4), w["about"])
    assert {r["outcome"] for r in rows} == {"cleared"} and len(nb.store.q("SELECT * FROM reinterpret_marks")) == before
    d = decision(nb, w["about"][0], w["archive"]["out"]["revision_id"])
    [src] = [x for x in d["sources"] if x["signal"] == "identity"]
    assert src["cleared_at"] and d["contribution"] == 0 and not d["marked"]
    assert out["revision_id"]
    # confirmed the same: a merge, marked like a merge
    nb2 = Notebook.open(nb.root.parent / "nb2")
    try:
        w2 = mnemosyne_world(nb2, Clock())
        nb2.store.conn.execute("DELETE FROM reinterpret_marks")
        item = next(it for it in nb2.review()["items"] if it["kind"] == "close_match")
        nb2.confirm_match(item["review_id"], None)
        assert nb2.store.entity_row(w2["e"])["merged_into"] == w2["x"]
        marked = {m["note_id"]: m for m in nb2.store.reinterpret_marks()}
        assert set(w2["about"] + w2["passing"]) <= set(marked)
        assert all(marked[n]["reason_code"] == "direct" and marked[n]["impact"] == 1.0 for n in w2["about"])
    finally:
        nb2.close()


def test_the_re_reading_packet_shows_both_cards(nb):
    """The note the identity event marked is read again with both entries side by side ("another entry with this
    exact name exists: ..."), so the model decides for this note whether it means the project or the person; a note
    it did not mark gets no such line. MUTATION: no identity block in the packet."""
    from abstractnote.process.packets import CHANGED_LABEL, link_batches
    from abstractnote.tools.context import ToolContext
    from abstractnote.tools.process import note_context

    clock = Clock()
    w = mnemosyne_world(nb, clock)
    ctx = note_context(ToolContext(nb=nb), w["about"][1])
    [block] = ctx["note"]["understood_differently"]
    assert block["entity_id"] == w["x"] and [o["entity_id"] for o in block["same_name"]] == [w["e"]]
    ext = {"summary": "x", "mentions": [mention("m1", "Mnemosyne", "project")], "claims": [], "tasks": []}
    [batch] = link_batches(ctx["note"], ext, [{"ref": "m1", "candidates": []}], [])
    assert batch["changed"].startswith(CHANGED_LABEL)
    assert "another entry with this exact name exists: Mnemosyne incarnation — person" in batch["changed"]
    other = note_context(ToolContext(nb=nb), w["passing"][0])
    assert other["note"].get("understood_differently") is None


def test_a_name_given_to_an_existing_entry_reaches_the_other_by_its_name(nb):
    """Without a newly created entry (an existing one is reused and given the hub's name as an alias) there is no
    close-match event; the exact-key match still reaches the other entry's notes through the name signal of the
    revision that gave the name. MUTATION: name_signal never true."""
    clock = Clock()
    a = note(nb, clock, "Zorn is a company.", day(20), [("m1", "Zorn", "org")], [stmt("m1", "ex:is_a", "company")])
    z = eid(nb, "Zorn")
    q = note(nb, clock, "Quill is a lab.", day(21), [("m1", "Quill", "org")], [stmt("m1", "ex:is_a", "lab")])
    settle(nb)
    qid = eid(nb, "Quill")
    from test_reinterpret import cand, ml

    def reuse_with_alias(packet: Dict[str, Any], cands: Dict[str, Any]) -> Dict[str, Any]:
        return {"mentions": [ml("m1", "reuse", cand(cands, "m1", qid), aliases=["Zorn"], summary=None)],
                "claims": [], "tasks": [], "revision_summary": "test"}

    x = run(nb, "Quill, once called Zorn, was a lab.", day(5),
            extraction([mention("m1", "Quill", "org", aliases=["Zorn"])], [stmt("m1", "ex:was", "a lab", literal=True)]),
            reuse_with_alias, now=clock.next())
    assert nb.store.q1("SELECT 1 FROM entity_aliases WHERE entity_id=? AND alias_key='zorn'", (qid,)) is not None
    assert not act.identity_events(nb.store.conn, x["out"]["revision_id"], prop._canon_map(nb.store.conn))
    [d] = [d for d in act.decisions_of(nb.store, a["note_id"]) if d["revision_id"] == x["out"]["revision_id"]]
    [src] = [s for s in d["sources"] if s["via"] == z and s["entity_id"] == qid]
    assert src["signal"] == "name" and src["strength"] > 0 and d["marked"]
    assert {a["note_id"], q["note_id"]} <= set(pending(nb))
