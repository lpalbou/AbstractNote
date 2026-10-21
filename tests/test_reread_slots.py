"""W29 (retro-C §2 and A3): a re-reading keeps the note's own slots, and an earlier note's evidence keeps the start
it moved back when the later note is read again.

1. The re-understanding packet shows the note's OWN previous reading first ("This note was previously understood
   as:", its statements numbered P0, P1, ...); the linking's ``same_statement_as_previous`` (``existing`` = that
   number) keeps the previous statement's claim id and predicate (revised in place: value, certainty and window may
   change). S2's shape: the 2025 "budget 40k" read again after the 2023 note keeps ``ex:annual_budget``.
2. A belief whose start an earlier note moved back (``claim.start_earlier``) keeps that start when its own note is
   read again (index, AbstractMemory, as received); a withdrawn mover still gives it back (``claim.start_restore``).

Scripted models only. Each test names the mutants that turn it red (untracked/reports/W29.md)."""

from __future__ import annotations

import json
from typing import Any, Dict

import pytest

from abstractnote.store.notebook import Notebook
from abstractnote.tools.context import ToolContext
from abstractnote.tools.process import note_context

from test_reinterpret import Clock, claim_index, extraction, link, mention, run, stmt

ZORN = "ex:org-zorn-labs"
D21, D23, D25 = "2021-09-20T09:00:00Z", "2023-02-07T09:00:00Z", "2025-02-05T09:00:00Z"
D10, D15, D20 = "2026-03-10T09:00:00Z", "2026-03-15T09:00:00Z", "2026-03-20T09:00:00Z"
LUT = "ex:project-lut"


@pytest.fixture
def nb(tmp_path):
    notebook = Notebook.open(tmp_path / "nb")
    yield notebook
    notebook.close()


def budget(value: str, predicate: str = "ex:annual_budget", certainty: str = "stated") -> Dict[str, Any]:
    return extraction([mention("m1", "Zorn Labs", "org")],
                      [stmt("m1", predicate, value, literal=True, certainty=certainty,
                            evidence=f"Zorn Labs budget {value}")])


def previous_index(packet: Dict[str, Any], claim_id: str) -> int:
    for c in (packet["note"].get("previous_reading") or {}).get("claims") or []:
        if c["claim_id"] == claim_id:
            return c["index"]
    raise AssertionError(f"{claim_id} is not in the previous reading")


# ----------------------------------------------------------------------------- 1. the note's own slots


def test_the_re_reading_packet_shows_the_previous_reading_first(nb):
    """The context of a note read again carries its previous reading (statements numbered, with the predicate as
    spelled, the value and the window; the entities it named), the extraction packet lists it before what the
    notebook knows and the linking message before the mentions and their LATER blocks. MUTANT: no previous reading
    in reinterpret_view; no block in extraction_packet / link_batches."""
    from abstractnote.process.merge import merge_extractions
    from abstractnote.process.packets import PREVIOUS_LABEL, extraction_packet, link_batches
    from abstractnote.tools.process import note_resolve_candidates

    clock = Clock()
    a = run(nb, "Zorn Labs budget 40k.", D25, budget("40k"), link({"m1": {"name": "Zorn Labs", "kind": "org"}}),
            now=clock.next())
    run(nb, "Zorn Labs budget 25k.", D23, budget("25k"), link({"m1": ZORN}), now=clock.next())
    ctx = ToolContext(nb=nb)
    seen = note_context(ctx, a["note_id"])
    prev = seen["note"]["previous_reading"]
    cid = a["out"]["claims_added"][0]
    assert [(c["index"], c["claim_id"], c["predicate"], c["object"]) for c in prev["claims"]] == \
        [(0, cid, "ex:annual_budget", "40k")]
    assert [e["entity_id"] for e in prev["entities"]] == [ZORN]
    from abstractnote.process.context import load_packet

    assert load_packet(nb, a["note_id"])["previous_claims"] == [cid]
    known = extraction_packet(seen["note"], seen["sections"][0], 1, seen["similar_notes"],
                              seen["candidate_entities"], seen["open_tasks"], seen["topics"])["known"]
    assert known.startswith("This note was previously understood as") and "ex:annual_budget “40k”" in known
    _, names = merge_extractions([budget("40k", "ex:budget_cap")])
    cands = note_resolve_candidates(ctx, a["note_id"], names)
    batch = link_batches(seen["note"], budget("40k", "ex:budget_cap"), cands["mentions"], seen["open_tasks"])[0]
    assert batch["previous"].startswith(PREVIOUS_LABEL) and "P0 Zorn Labs ex:annual_budget “40k”" in batch["previous"]
    from abstractnote.process.prompts import load, load_system

    template = load("link")
    assert template.index("{{batch.previous}}") < template.index("{{batch.mentions}}")
    # the static prompts say what the block is for: the linking names a statement by its P number, the extraction
    # keeps the predicate written there
    assert "`same_statement_as_previous` with `existing` = its P number" in load_system("link")
    assert "previously understood (a note read again)" in load_system("extract")
    # a note read for the first time shows no such block: the message is byte for byte what it was
    first = link_batches({**seen["note"], "previous_reading": None}, budget("40k"), cands["mentions"],
                         seen["open_tasks"])[0]
    assert first["previous"] == ""


def _s2(nb: Notebook, clock: Clock, answer) -> Dict[str, Any]:
    """S2's shape: the 2025 note (40k), then the 2023 note (25k, an earlier state of 40k), then the 2025 note read
    again; ``answer(x40)(packet, cands)`` answers the re-reading's statement 0, which the model now spells
    ``ex:budget_cap``."""
    a = run(nb, "Zorn Labs annual budget approved at 40k.", D25, budget("40k"),
            link({"m1": {"name": "Zorn Labs", "kind": "org"}}), now=clock.next())
    x40 = a["out"]["claims_added"][0]
    b = run(nb, "Zorn Labs annual budget is 25k.", D23, budget("25k"), link({"m1": ZORN}, claims=[
        lambda p, cs: {"index": 0, "relation_to_existing": "earlier_state_of", "existing": claim_index(p, cs, x40),
                       "reason": "budget before"}]), now=clock.next())
    assert b["out"]["claims_inserted_before"] and [m["note_id"] for m in nb.store.reinterpret_marks()] == [a["note_id"]]
    r = run(nb, "", D25, budget("40k", "ex:budget_cap"), link({"m1": ZORN}, claims=[answer(x40)]), reinterpret=True,
            note_id=a["note_id"], now=clock.next())
    return {"a": a, "b": b, "r": r, "x40": x40, "x25": b["out"]["claims_added"][0]}


def test_s2_re_read_keeps_the_previous_predicate_and_claim_id(nb):
    """S2 (retro-C §2): the re-reading spells the budget ``ex:budget_cap`` but names it the same statement as its
    previous reading's P0: the claim id and ``ex:annual_budget`` stay, nothing closes ``reinterpreted``, one slot
    holds 25k then 40k. MUTANT: the relation not in CLAIM_RELATIONS, the builder branch ignored, the plan not
    mapping it to the previous claim."""
    clock = Clock()
    out = _s2(nb, clock, lambda x40: lambda p, cs: {"index": 0, "relation_to_existing": "same_statement_as_previous",
                                                    "existing": previous_index(p, x40), "reason": "same budget"})
    x40 = out["x40"]
    row = nb.store.claim_row(x40)
    assert (row["predicate"], row["status"], row["object_literal"]) == ("ex:annual_budget", "active", "40k")
    preds = {r[0] for r in nb.store.q("SELECT predicate FROM claims WHERE subject_id=? AND status!='reinterpreted'",
                                      (ZORN,))}
    assert preds == {"ex:annual_budget"}
    assert nb.store.q1("SELECT COUNT(*) FROM claims WHERE status='reinterpreted'")[0] == 0
    assert out["r"]["out"].get("diff") is None or out["r"]["out"]["diff"]["claims_closed"] == []
    assert any("predicate_kept: ex:budget_cap -> ex:annual_budget" in w for w in out["r"]["out"]["warnings"])
    then = [c["object_literal"] for c in nb.beliefs_at("2024-06-01", entity_id=ZORN)]
    now = [c["object_literal"] for c in nb.beliefs_at("2025-06-01", entity_id=ZORN)]
    assert then == ["25k"] and now == ["40k"]


def test_s2_without_the_relation_the_slot_splits(nb):
    """The control (retro-C §2 as measured live): the same re-reading answered ``new`` writes ``ex:budget_cap`` and
    closes the previous ``ex:annual_budget 40k`` as reinterpreted; this is what the relation prevents."""
    out = _s2(nb, Clock(), lambda x40: lambda p, cs: {"index": 0, "relation_to_existing": "new", "existing": None,
                                                      "reason": "x"})
    assert nb.store.claim_row(out["x40"])["status"] == "reinterpreted"
    assert {r[0] for r in nb.store.q("SELECT predicate FROM claims WHERE subject_id=? AND status='active'", (ZORN,))} \
        == {"ex:budget_cap"}


@pytest.mark.parametrize("certainty", ["possible", "stated"])
def test_same_statement_as_previous_may_change_the_value_and_certainty_in_place(nb, certainty):
    """The previous statement read again with another value and certainty: revised in place (claim id and predicate
    kept), the index, the KG copy and as received before the re-reading agree. MUTANT: claim.revise ignores the
    object (the index keeps 40k), or the KG copy keeps the old object."""
    clock = Clock()
    a = run(nb, "Zorn Labs budget 40k.", D25, budget("40k"), link({"m1": {"name": "Zorn Labs", "kind": "org"}}),
            now=clock.next())
    x40 = a["out"]["claims_added"][0]
    before = clock.next()
    import time

    time.sleep(1.1)
    r = run(nb, "", D25, budget("35k", "ex:budget_cap", certainty=certainty), link({"m1": ZORN}, claims=[
        lambda p, cs: {"index": 0, "relation_to_existing": "same_statement_as_previous",
                       "existing": previous_index(p, x40), "reason": "same budget"}]),
        reinterpret=True, note_id=a["note_id"], now=clock.next())
    assert r["out"]["claims_revised"] == [x40] and r["out"]["claims_added"] == []
    row = nb.store.claim_row(x40)
    assert (row["predicate"], row["object_literal"], row["certainty"]) == ("ex:annual_budget", "35k", certainty)
    open_ = [a.object for a in nb.kg.query(subject=ZORN, predicate="ex:annual_budget") if a.valid_until is None]
    assert open_ == ["35k"]
    held = {c["claim_id"]: c for c in nb.store.beliefs_at("2025-06-01", entity_id=ZORN, as_received=before)}
    assert held[x40]["object_literal"] == "40k" and held[x40]["certainty"] == "stated"
    # a changed value is a change of meaning for what relies on it (R6's measure counts the statement)
    from abstractnote.recall.propagation import measure

    [rec] = [x for x in measure(nb, r["out"]["revision_id"]) if x["entity_id"] == ZORN]
    assert rec["claim_share"] > 0


def test_a_previous_statement_of_another_subject_is_kept_as_new(nb):
    """The relation names a previous statement about another subject: refused with a warning, the statement is new
    (the previous one closes as reinterpreted). MUTANT: no same-subject check."""
    clock = Clock()
    two = extraction([mention("m1", "Zorn Labs", "org"), mention("m2", "LUT", "project")],
                     [stmt("m1", "ex:annual_budget", "40k", literal=True, evidence="Zorn Labs budget 40k"),
                      stmt("m2", "ex:annual_budget", "10k", literal=True, evidence="LUT budget 10k")])
    a = run(nb, "Zorn Labs budget 40k, LUT budget 10k.", D25, two,
            link({"m1": {"name": "Zorn Labs", "kind": "org"}, "m2": {"name": "LUT", "kind": "project"}}),
            now=clock.next())
    x40 = next(c for c in a["out"]["claims_added"] if nb.store.claim_row(c)["subject_id"] == ZORN)
    r = run(nb, "", D25, extraction([mention("m2", "LUT", "project")],
                                    [stmt("m2", "ex:budget_cap", "10k", literal=True, evidence="LUT budget 10k")]),
            link({"m2": LUT}, claims=[lambda p, cs: {"index": 0, "relation_to_existing": "same_statement_as_previous",
                                                     "existing": previous_index(p, x40), "reason": "x"}]),
            reinterpret=True, note_id=a["note_id"], now=clock.next())
    assert any("another subject" in w for w in r["out"]["warnings"])
    assert nb.store.claim_row(x40)["status"] == "reinterpreted"


# ----------------------------------------------------------------------------- 2. earlier evidence keeps the start


def _moved(nb: Notebook, clock: Clock) -> Dict[str, Any]:
    """Day 20 "LUT budget 40k" (day 1's 30k superseded by it), day 10 restates it: the belief starts on day 10."""
    d01 = "2026-03-01T09:00:00Z"
    lut = lambda v: extraction([mention("m1", "LUT", "project")],  # noqa: E731
                               [stmt("m1", "ex:budget", v, literal=True, evidence=f"LUT budget {v}")])
    c = run(nb, "LUT budget 30k.", d01, lut("30k"), link({"m1": {"name": "LUT", "kind": "project"}}), now=clock.next())
    p30 = c["out"]["claims_added"][0]
    b = run(nb, "LUT budget 40k now.", D20, lut("40k"), link({"m1": LUT}, claims=[
        lambda p, cs: {"index": 0, "relation_to_existing": "supersedes", "existing": claim_index(p, cs, p30),
                       "reason": "new figure"}]), now=clock.next())
    x40 = b["out"]["claims_added"][0]
    a = run(nb, "LUT budget 40k already.", D10, lut("40k"), link({"m1": LUT}, claims=[
        lambda p, cs: {"index": 0, "relation_to_existing": "restates", "existing": claim_index(p, cs, x40),
                       "reason": "same figure"}]), now=clock.next())
    assert a["out"]["claims_started_earlier"] == [x40]
    return {"lut": lut, "p30": p30, "x40": x40, "d20": b["note_id"], "d10": a["note_id"]}


@pytest.mark.parametrize("certainty,since", [("stated", None), ("possible", None), ("possible", "2026-03-18")])
def test_reading_the_later_note_again_keeps_the_start_its_earlier_evidence_moved(nb, certainty, since):
    """A3 (retro-C §1, R5 live: ``ex:approved`` kept observed_at and first_seen 2023 but valid_from went back to 2025):
    day 20 read again (unchanged, or with another certainty: ``claim.revise``) keeps valid_from = day 10 in the index,
    observed_at day 10, believed on day 15 in the index AND AbstractMemory; as received before the re-reading the same;
    day 10 read again without the statement still gives the start back (``claim.start_restore``), to the start the
    LATEST reading of day 20 gave (day 18 when it said "since the 18th"), and day 20 read once more after that keeps
    it (the withdrawn evidence no longer counts). MUTANTS: the plan compares and revises with the reading's own start
    (no earlier-evidence start); replay ignores a claim.revise's own start; a withdrawn move still counted (no
    start_restore drop); the previous copy closed before its own start."""
    clock = Clock()
    m = _moved(nb, clock)
    x40, p30 = m["x40"], m["p30"]
    import time

    time.sleep(1.1)
    before = clock.next()
    again = extraction([mention("m1", "LUT", "project")],
                       [{**stmt("m1", "ex:budget", "40k", literal=True, certainty=certainty, evidence="LUT budget 40k"),
                         "valid_from": since}])
    r = run(nb, "", D20, again, link({"m1": LUT}), reinterpret=True, note_id=m["d20"], now=clock.next())
    own = f"{since}T00:00:00Z" if since else D20
    if certainty == "stated":
        assert r["out"]["unchanged"] is True
    else:
        assert r["out"]["claims_revised"] == [x40]
    row = nb.store.claim_row(x40)
    assert (row["observed_at"], row["valid_from"], row["certainty"]) == (D10, D10, certainty)
    assert [c["claim_id"] for c in nb.beliefs_at(D15, entity_id=LUT) if c["predicate"] == "ex:budget"] == [x40]
    assert x40 in nb.kg.active_claim_ids(D15) and p30 not in nb.kg.active_claim_ids(D15)
    held = {x["claim_id"]: x for x in nb.store.beliefs_at(D15, entity_id=LUT, as_received=before)}
    assert x40 in held and held[x40]["valid_from"] == D10
    # the mover withdrawn: the start goes back to day 20 (its own reading's start) in both stores
    r2 = run(nb, "", D10, extraction([mention("m1", "LUT", "project")]), link({"m1": LUT}), reinterpret=True,
             note_id=m["d10"], now=clock.next())
    assert r2["out"]["diff"]["starts_restored"] == [x40]
    back = nb.store.claim_row(x40)
    assert (back["observed_at"], back["valid_from"]) == (D20, own)
    assert [c["claim_id"] for c in nb.beliefs_at(D15, entity_id=LUT) if c["predicate"] == "ex:budget"] == [p30]
    assert sorted(c for c in nb.kg.active_claim_ids(D15) if c in (p30, x40)) == [p30]
    assert x40 in nb.kg.active_claim_ids("2026-03-25T09:00:00Z")
    for a in nb.kg.query(assertion_ids=(x40,)):  # the original copy closes empty at its own start, never before it
        assert a.valid_until is None or a.valid_from is None or a.valid_until >= a.valid_from
    # day 20 read once more: the withdrawn evidence of day 10 no longer counts, nothing changes
    r3 = run(nb, "", D20, again, link({"m1": LUT}), reinterpret=True, note_id=m["d20"], now=clock.next())
    assert r3["out"]["unchanged"] is True and nb.store.claim_row(x40)["valid_from"] == own


def test_a_rebuild_replays_the_kept_start(nb):
    """The kept start is a function of the revision log: rebuild gives the same index start and the same KG
    answer. MUTANT: as above (the stored revise op carries the reading's own start)."""
    clock = Clock()
    m = _moved(nb, clock)
    run(nb, "", D20, extraction([mention("m1", "LUT", "project")],
                                [stmt("m1", "ex:budget", "40k", literal=True, certainty="possible",
                                      evidence="LUT budget 40k")]),
        link({"m1": LUT}), reinterpret=True, note_id=m["d20"], now=clock.next())
    nb.rebuild()
    row = nb.store.claim_row(m["x40"])
    assert (row["valid_from"], row["certainty"]) == (D10, "possible")
    assert m["x40"] in nb.kg.active_claim_ids(D15)
    ops = [op for (oj,) in nb.store.q("SELECT ops_json FROM revisions WHERE action='note.reinterpret'")
           for op in json.loads(oj) if op["op"] == "claim.revise"]
    assert [(o["payload"]["valid_from"], o["payload"]["own_valid_from"], o["payload"]["assertion"]["valid_from"])
            for o in ops] == [(D10, D20, D20)]


# ----------------------------------------------------------------------------- the generated flow, headless


T25 = "Zorn Labs annual budget approved at 40k."
T23 = "Zorn Labs annual budget is 25k."


def _s2_llm():
    """A model stand-in for S2 through the real note-process / note-consolidate flows. Its extraction drifts on the
    re-reading as gpt-5.6-terra did live (the 2025 note read again writes ``ex:budget_cap``); its linking names a
    statement only by what the MESSAGE shows: the LATER ``#n`` of the 40k (the 2023 note) and, on the re-reading,
    the ``P<n>`` line of the previous reading's 40k when the message has one (else the statement is new, as live)."""
    import re

    from abstractnote.flows.runner import ScriptedLLM

    seen = {"t25": 0}

    def extract(prompt: str) -> Dict[str, Any]:
        if T25.split(" at ")[0] in prompt:
            seen["t25"] += 1
            return budget("40k", "ex:annual_budget" if seen["t25"] == 1 else "ex:budget_cap")
        return budget("25k")

    def linking(prompt: str) -> Dict[str, Any]:
        from test_reinterpret import ml

        has_candidate = re.search(r"^\s+\[0\] ", prompt, re.M) is not None
        ms = [ml("m1", "reuse", 0, summary=None) if has_candidate else ml("m1", "create", name="Zorn Labs", kind="org")]
        claims = []
        prev = re.search(r"^\s+P(\d+) Zorn Labs ex:annual_budget “40k”", prompt, re.M)
        later = re.search(r"^\s+#(\d+) zorn_labs ex:annual_budget “40k”", prompt, re.M)
        if prev:
            claims.append({"index": 0, "relation_to_existing": "same_statement_as_previous",
                           "existing": int(prev.group(1)), "reason": "same budget"})
        elif later and "“25k”" in prompt:
            claims.append({"index": 0, "relation_to_existing": "earlier_state_of", "existing": int(later.group(1)),
                           "reason": "budget before"})
        return {"mentions": ms, "claims": claims, "tasks": [], "revision_summary": "x"}

    return ScriptedLLM({"QuickFiling": {"title": "t", "kind": "fact", "topics": [], "urgency": "normal",
                                        "language": "en", "one_line": "x"},
                        "Extraction": extract, "Linking": linking,
                        "CommunityLabels": {"communities": []}, "EntitySummaries": {"summaries": []},
                        "Reflections": {"items": []}})


def test_s2_through_the_flows_the_re_reading_keeps_one_budget_predicate(tmp_path):
    """S2 headless (the after-probe's shape, scripted): 2025 note, 2023 note, consolidate. The re-reading's
    extraction spells ``ex:budget_cap``, but the linking message shows the previous reading (P0) and the answer names
    it: ``zorn_labs`` keeps ONE budget predicate, ``ex:annual_budget`` 25k [2023, 2025) then 40k, the 40k claim id
    unchanged, nothing reinterpreted. MUTANTS (red): no previous-reading block in link_batches (or no
    ``{{batch.previous}}`` in link.md, or no previous reading in reinterpret_view); the relation removed from
    CLAIM_RELATIONS; the builder ignoring it."""
    from abstractnote.flows.runner import NoteRunner
    from abstractnote.tools import bind

    nb_dir = tmp_path / "nb"
    llm = _s2_llm()
    runner = NoteRunner.with_llm(llm, bind(nb_dir))
    for key, text, at in (("t25", T25, D25), ("t23", T23, D23)):
        runner.run_capture(text, idempotency_key=key, occurred_at=at, source={"client": "cli"})
        assert runner.run_drain(max_items=5, budget_s=60)["processed"] == 1
    nbk = Notebook.open(nb_dir)
    try:
        before = {r["claim_id"]: r["object_literal"] for r in nbk.store.q("SELECT * FROM claims")}
        x40 = next(c for c, v in before.items() if v == "40k")
        assert len(nbk.store.reinterpret_marks()) == 1
    finally:
        nbk.close()
    runner.run("note-consolidate", {})
    assert sum(1 for c in llm.calls if c["schema"] == "Extraction") == 3  # the 2025 note was read again
    nbk = Notebook.open(nb_dir)
    try:
        rows = nbk.store.q("SELECT predicate, object_literal, status, claim_id, valid_from, valid_until FROM claims"
                           " WHERE subject_id=? ORDER BY valid_from", (ZORN,))
        assert {r["predicate"] for r in rows} == {"ex:annual_budget"}
        assert [(r["object_literal"], r["status"]) for r in rows] == [("25k", "superseded"), ("40k", "active")]
        assert rows[1]["claim_id"] == x40 and rows[0]["valid_until"] == D25
        assert nbk.store.q1("SELECT COUNT(*) FROM revisions WHERE action='note.reinterpret'")[0] <= 1
    finally:
        nbk.close()
