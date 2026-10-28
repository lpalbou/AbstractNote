"""Summaries, then-and-now, themes and statistics as rebuilt views of the dated history (R3, ADR 0008 §2, §5).

No model: revisions are written as ops (``test_store_support``), the consolidation's summary step is driven
through its own functions, a drain through the headless runner with a scripted model. The scenarios are the
retrospective ones of untracked/reviews/retro-A.md (S1: 2026 -> 2022 -> 2024; S4: an older project's notes tie
Mira to Helix) and the re-interpretation of ADR 0008 §1. Each test names the code it goes red without.
"""

from __future__ import annotations

import copy

import pytest

from test_store_support import claim, create, nb, rev, t  # noqa: F401

from abstractnote.recall import communities as cm
from abstractnote.store.notes import REINTERPRETED
from abstractnote.recall import consolidation as cons

MIRA = "ex:person-mira-voss"
ZORN = "ex:org-zorn-labs"
T22, T24, T26 = t(1, 6, 2022), t(15, 9, 2024), t(10, 3, 2026)
S22 = "Mira Voss is an intern at Zorn Labs (since 2022-06)."
S24 = "Mira Voss is head of research at Zorn Labs (since 2024-09)."
S26 = "Mira Voss is the director of Zorn Labs (since 2026-03)."


def mention(eid, note_id):
    return {"op": "entity.mention", "payload": {"entity_id": eid, "note_id": note_id}}


def summary(eid, text, as_of=None):
    p = {"entity_id": eid, "summary": text}
    if as_of:
        p["as_of"] = as_of
    return {"op": "entity.summary", "payload": p}


def note(nb, text, at):
    return nb.capture(text, occurred_at=at).note_id


def s1(nb):
    """Mira's notes processed out of order: 2026, then 2022, then 2024, each writing the summary of its own date."""
    n26 = note(nb, "Mira Voss leads Zorn Labs.", T26)
    nb.apply_revision(rev("rv_26", T26, create(MIRA, "Mira Voss", summary=S26), create(ZORN, "Zorn Labs"),
                          mention(MIRA, n26), mention(ZORN, n26),
                          claim("cl_dir", MIRA, "ex:role", literal="director", at=T26, note_id=n26), note_id=n26))
    n22 = note(nb, "Mira Voss joined Zorn Labs as an intern.", T22)
    nb.apply_revision(rev("rv_22", T22, mention(MIRA, n22), mention(ZORN, n22), summary(MIRA, S22),
                          claim("cl_int", MIRA, "ex:role", literal="intern", at=T22, note_id=n22), note_id=n22))
    n24 = note(nb, "Mira Voss is now head of research.", T24)
    nb.apply_revision(rev("rv_24", T24, mention(MIRA, n24), summary(MIRA, S24),
                          claim("cl_head", MIRA, "ex:role", literal="head of research", at=T24, note_id=n24),
                          note_id=n24))
    return {"n22": n22, "n24": n24, "n26": n26}


# --------------------------------------------------------------------------- 1. dated order

def test_summary_history_and_the_card_follow_the_date_each_summary_describes(nb):
    s1(nb)
    card = nb.entity(MIRA)
    # ordered by the date described, never by when it was written (2026 was written first, 2024 last)
    assert [h["summary"] for h in card["summary_history"]] == [S22, S24, S26]
    assert card["summary"] == S26 and nb.store.entity_row(MIRA)["summary"] == S26
    # as of a date: the summary that described that date
    assert nb.entity(MIRA, at=t(1, 1, 2025))["summary"] == S24
    assert [n["summary"] for n in nb.graph(at=t(1, 1, 2023))["nodes"] if n["id"] == MIRA] == [S22]


def test_two_summaries_of_the_same_date_are_ordered_by_the_revision_log(nb):
    """Goes red when the tie is broken by the revision id (a hash) instead of the log's sequence."""
    n = note(nb, "Dana.", t(5))
    nb.apply_revision(rev("rv_z", t(5), create("ex:person-dana", "Dana", summary="First."),
                          mention("ex:person-dana", n), note_id=n))
    nb.apply_revision(rev("rv_a", t(9), summary("ex:person-dana", "Second, same date.", as_of=t(5))))
    hist = nb.entity("ex:person-dana")["summary_history"]
    assert [h["summary"] for h in hist] == ["First.", "Second, same date."]
    assert nb.store.entity_row("ex:person-dana")["summary"] == "Second, same date."
    assert hist[1]["at"] == t(5)  # dated at what it describes, not when it was written


def test_then_now_earliest_is_the_earliest_dated_knowledge_and_latest_the_current_summary(nb):
    s1(nb)
    tn = nb.then_now(MIRA)
    assert tn["first_seen"] == T22
    assert (tn["earliest"]["at"], tn["earliest"]["summary"]) == (T22, S22)
    assert tn["latest"]["summary"] == S26
    assert [c["claim_id"] for c in tn["earliest_claims"]] == ["cl_int"]  # only what was known that first day
    # turning points never come from summary rewrites
    assert all(p["kind"] != "summary" for p in tn["turning_points"])


def test_earliest_without_a_summary_of_that_date_shows_what_was_known_then(nb):
    """A 2022 archive whose note wrote no summary: earliest is still 2022 (its statements), never the 2026
    summary. Goes red with the old "earliest = the first summary row"."""
    n26 = note(nb, "Mira Voss leads Zorn Labs.", T26)
    nb.apply_revision(rev("rv_26", T26, create(MIRA, "Mira Voss", summary=S26), mention(MIRA, n26), note_id=n26))
    n22 = note(nb, "Mira Voss joined as an intern.", T22)
    nb.apply_revision(rev("rv_22", T22, mention(MIRA, n22),
                          claim("cl_int", MIRA, "ex:role", literal="intern", at=T22, note_id=n22), note_id=n22))
    tn = nb.then_now(MIRA)
    assert tn["earliest"]["at"] == T22 and tn["earliest"]["summary"] is None
    assert tn["earliest"]["note_id"] == n22
    assert [c["claim_id"] for c in tn["earliest_claims"]] == ["cl_int"]
    assert tn["latest"]["summary"] == S26
    # a mention alone: the first note's own words stand for what was known then
    n21 = nb.capture("Zorn's first visitor list names Mira Voss.", occurred_at=t(1, 1, 2021), title="Visitors").note_id
    nb.apply_revision(rev("rv_21", t(1, 1, 2021), mention(MIRA, n21), note_id=n21))
    tn = nb.then_now(MIRA)
    assert tn["earliest"]["at"] == t(1, 1, 2021) and tn["earliest"]["note_id"] == n21
    assert tn["earliest_claims"] == [] and tn["earliest"]["text"]


def test_turning_points_come_from_claim_windows(nb):
    n1 = note(nb, "Castor runs on Llama.", T22)
    nb.apply_revision(rev("rv_1", T22, create("ex:project-castor", "Castor", summary="Castor, on Llama."),
                          mention("ex:project-castor", n1),
                          claim("cl_llama", "ex:project-castor", "ex:runs_on", literal="Llama", at=T22, note_id=n1),
                          {"op": "claim.add", "payload": {**claim("cl_pilot", "ex:project-castor", "ex:status",
                                                                  literal="pilot", at=T22, note_id=n1)["payload"],
                                                          "valid_until": t(1, 1, 2023)}}, note_id=n1))
    n2 = note(nb, "Castor now runs on Qwen.", T24)
    nb.apply_revision(rev("rv_2", T24, mention("ex:project-castor", n2), summary("ex:project-castor", "Castor, on Qwen."),
                          claim("cl_qwen", "ex:project-castor", "ex:runs_on", literal="Qwen", at=T24, note_id=n2),
                          {"op": "claim.supersede", "payload": {"claim_id": "cl_llama", "by_claim_id": "cl_qwen",
                                                                "reason": "moved", "valid_until": T24}}, note_id=n2))
    tn = nb.then_now("ex:project-castor")
    kinds = [(p["kind"], p["at"]) for p in tn["turning_points"]]
    assert kinds == [("ended", t(1, 1, 2023)), ("supersede", T24)]  # the summary rewrite is not one
    sup = tn["turning_points"][1]
    assert sup["replaced_by"]["claim_id"] == "cl_qwen" and sup["note_id"] == n2
    assert "Llama" in sup["text"] and "Qwen" in sup["text"]


# --------------------------------------------------------------------------- re-interpretation (ADR 0008 §1)

def reinterpret(nb, rid, note_id, at, *ops, recorded_at=None):
    r = {**rev(rid, at, *ops, note_id=note_id), "action": "note.reinterpret"}
    if recorded_at:
        r["recorded_at"] = recorded_at
    return nb.apply_revision(r)


def test_a_reinterpretation_takes_its_notes_summary_out_of_the_history_and_off_the_card(nb):
    """The current summary survives a re-interpretation: the superseded reading's summary is no longer the card
    nor in summary_history, except as received before the re-interpretation. Goes red without
    summary_superseded_sql / refresh_superseded_summaries."""
    n24 = note(nb, "Mira Voss leads Zorn Labs.", T24)
    nb.apply_revision({**rev("rv_old", T24, create(MIRA, "Mira Voss", summary=S24), mention(MIRA, n24),
                             note_id=n24), "recorded_at": "2026-10-01T00:00:00Z"})
    n26 = note(nb, "Mira Voss is the director.", T26)
    nb.apply_revision({**rev("rv_26", T26, mention(MIRA, n26), summary(MIRA, S26), note_id=n26),
                       "recorded_at": "2026-10-02T00:00:00Z"})
    wrong = "Mira Voss leads Zorn Labs (a misreading of 2026)."
    nb.apply_revision({**rev("rv_mis", T26, summary(MIRA, wrong), note_id=n26), "recorded_at": "2026-10-03T00:00:00Z"})
    assert nb.store.entity_row(MIRA)["summary"] == wrong
    # the 2026 note understood again; its new reading writes no summary for Mira
    reinterpret(nb, "rv_re", n26, T26, mention(MIRA, n26), recorded_at="2026-10-04T00:00:00Z")
    assert nb.store.entity_row(MIRA)["summary"] == S24  # re-selected from the dated history
    assert [h["summary"] for h in nb.entity(MIRA)["summary_history"]] == [S24]
    assert nb.then_now(MIRA)["latest"]["summary"] == S24
    # what an archive that had not received the re-interpretation held keeps the old readings (the archive's own
    # membership rule for a note.reinterpret revision is R2's: archive.py; here the restriction on the superseding
    # revision is given directly)
    held = nb.store.summary_rows([MIRA], superseded_within=("r2.recorded_at<=?", ["2026-10-03T12:00:00Z"]))
    assert [h["summary"] for h in held] == [S24, S26, wrong]
    # the re-interpretation's own summary is the current one
    reinterpret(nb, "rv_re2", n26, T26, mention(MIRA, n26), summary(MIRA, S26 + " Again."),
                recorded_at="2026-10-05T00:00:00Z")
    assert nb.store.entity_row(MIRA)["summary"] == S26 + " Again."


def test_reinterpreted_closures_are_no_turning_points_nor_earlier_beliefs(nb):
    """A claim closed because its own note was understood again is a system event (R5 review 4.4)."""
    n24 = note(nb, "Budget 40k, decided.", T24)
    nb.apply_revision(rev("rv_24", T24, create(ZORN, "Zorn Labs", summary="Zorn Labs."), mention(ZORN, n24),
                          claim("cl_40", ZORN, "ex:budget", literal="40k", at=T24, note_id=n24), note_id=n24))
    reinterpret(nb, "rv_re", n24, T24, mention(ZORN, n24),
                claim("cl_40p", ZORN, "ex:budget_planned", literal="40k", at=T24, note_id=n24))
    # R2's op_claim_reinterpret closes the superseded reading's claim with the stored kind (claims.status), an empty
    # window and a reason; written here as that op leaves the row
    with nb.store.tx() as c:
        c.execute("UPDATE claims SET status=?, valid_until=valid_from, closed_reason='understood again',"
                  " closed_revision_id='rv_re' WHERE claim_id='cl_40'", (REINTERPRETED,))
    nb.store.conn.execute("DELETE FROM then_now_cache")
    tn = nb.then_now(ZORN)
    assert [p for p in tn["turning_points"] if p.get("claim_id") == "cl_40"] == []
    assert [c["claim_id"] for c in tn["earliest_claims"]] == ["cl_40p"]
    # a real retraction by a person stays a turning point
    nb.retract_claim("cl_40p", "user:1", "wrong", occurred_at=t(1, 1, 2025))
    assert [p["kind"] for p in nb.then_now(ZORN)["turning_points"]] == ["retract"]
    # a closure kind the view does not know fails loudly, never read as a supersession
    nb.store.conn.execute("UPDATE claims SET status='withdrawn_somehow' WHERE claim_id='cl_40'")
    nb.store.conn.execute("DELETE FROM then_now_cache")
    with pytest.raises(RuntimeError, match="closure kind 'withdrawn_somehow'"):
        nb.then_now(ZORN)


# --------------------------------------------------------------------------- 2. the current summary re-derived

def s4(nb):
    """Mira's card (2026) is current; then five notes of 2023-24 about Helix at the Quill Institute arrive, each
    writing its own dated summary."""
    n26 = note(nb, "Mira Voss leads Zorn Labs.", T26)
    nb.apply_revision(rev("rv_26", T26, create(MIRA, "Mira Voss", summary=S26), mention(MIRA, n26), note_id=n26))
    helix = "ex:project-helix-atlas"
    n23 = note(nb, "Mira advised Priya on Helix Atlas.", t(18, 7, 2023))
    nb.apply_revision(rev("rv_23", t(18, 7, 2023), create(helix, "Helix Atlas", summary="A Quill Institute project."),
                          mention(MIRA, n23), mention(helix, n23),
                          summary(MIRA, "Mira Voss advises the Helix Atlas project at the Quill Institute."),
                          claim("cl_adv", MIRA, "ex:advises", helix, at=t(18, 7, 2023), note_id=n23), note_id=n23))
    return {"n26": n26, "n23": n23, "helix": helix}


def test_stale_reads_the_current_summary_in_dated_order_and_consumes_the_retro_marks(nb):
    """S4: the 2023 note wrote a summary AFTER the 2026 one; the card stays 2026's and is stale (reason
    retrospective). Goes red when _stale takes the last-written summary (it then sees nothing stale) or ignores
    retro_state (reason 'changed')."""
    s4(nb)
    assert nb.store.entity_row(MIRA)["summary"] == S26
    assert MIRA in [x["entity_id"] for x in nb.retro_state()["stale_summaries"]]
    stale = {x["entity_id"]: x for x in cons._stale(nb)}
    assert stale[MIRA]["reason"] == "retrospective"
    assert stale[MIRA]["summary_revision_count"] == nb.store.q1("SELECT seq FROM revisions WHERE revision_id='rv_26'")[0]


def test_the_summary_packet_shows_the_dated_history_earliest_first(nb):
    s4(nb)
    p = next(i for i in cons.stale_summaries(nb, 50)["items"] if i["entity_id"] == MIRA)
    assert [h["date"] for h in p["history"]] == ["2023-07-18", "2026-03-10"]
    assert "Helix" in p["history"][0]["summary"] and p["as_of"] == "2026-03-10"
    assert any("helix" in c.lower() and "(since 2023-07-18)" in c for c in p["claims"])
    assert p["omitted"]["history"] == 0 and p["earlier"] == [] and p["omitted"]["earlier"] == 0
    # the note the current summary does not account for yet is shown, marked new (it is dated before the summary)
    assert [(n["date"], n.get("new", False)) for n in p["notes"]] == [("2026-03-10", False), ("2023-07-18", True)]
    # the model sees it (the flow's view code)
    from abstractnote.flows.build import SUMMARY_VIEW_CODE

    view = _run_code(SUMMARY_VIEW_CODE, items=[p])["view"][0]
    assert view["history"] == p["history"] and view["as_of"] == "2026-03-10" and "earlier" in view


def test_ended_statements_and_a_long_history_reach_the_packet_with_what_was_left_out(nb):
    n = note(nb, "Castor.", T22)
    nb.apply_revision(rev("rv_0", T22, create("ex:project-castor", "Castor", summary="v0"),
                          mention("ex:project-castor", n),
                          claim("cl_a", "ex:project-castor", "ex:runs_on", literal="Llama", at=T22, note_id=n), note_id=n))
    nb.apply_revision(rev("rv_s", T24, {"op": "claim.supersede", "payload": {"claim_id": "cl_a", "by_claim_id": None,
                                                                               "reason": "r", "valid_until": T24}}))
    for i in range(1, 9):
        nb.apply_revision(rev(f"rv_{i}", t(i, 2, 2025), summary("ex:project-castor", f"v{i}")))
    nb.apply_revision(rev("rv_m", t(1, 3, 2025), mention("ex:project-castor", note(nb, "Castor again.", t(1, 3, 2025)))))
    p = next(i for i in cons.stale_summaries(nb, 50)["items"] if i["entity_id"] == "ex:project-castor")
    assert [h["summary"] for h in p["history"]] == ["v0", "v4", "v5", "v6", "v7", "v8"]
    assert p["omitted"]["history"] == 3  # v1..v3, said, never silently
    from abstractnote.flows.build import SUMMARY_VIEW_CODE

    assert _run_code(SUMMARY_VIEW_CODE, items=[p])["view"][0]["omitted"] == {"history": 3}  # the model is told
    assert p["earlier"] == [{"from": "2022-06-01", "until": "2024-09-15", "statement": p["earlier"][0]["statement"]}]
    assert "Llama" in p["earlier"][0]["statement"]


def test_what_holds_now_is_never_the_part_the_packet_cuts(nb):
    """Nine statements of equal strength over nine years: the packet keeps the eight most recent and counts the
    one it leaves out. Goes red with the old earliest-first tie-break (the 2026 role would be cut)."""
    n = note(nb, "Mira.", T22)
    nb.apply_revision(rev("rv_0", T22, create(MIRA, "Mira Voss", summary=S22), mention(MIRA, n), note_id=n))
    for i in range(9):
        at = t(1, 1, 2018 + i)
        nb.apply_revision(rev(f"rv_c{i}", at, claim(f"cl_{i}", MIRA, f"ex:fact_{i}", literal=f"f{i}", at=at)))
    nb.apply_revision(rev("rv_m", T26, mention(MIRA, note(nb, "Mira later.", T26))))
    p = next(i for i in cons.stale_summaries(nb, 50)["items"] if i["entity_id"] == MIRA)
    assert any("f8" in c for c in p["claims"]) and not any(c.endswith("f0 (since 2018-01-01)") for c in p["claims"])
    assert p["omitted"]["claims"] == 1


def test_a_consolidation_summary_is_dated_at_the_latest_knowledge_and_clears_the_mark(nb):
    """Goes red without the entity.summary as_of: dated now, it would hide a note dated in between."""
    w = s4(nb)
    out = cons.set_summaries(nb, [{"entity_id": MIRA, "summary": "Mira Voss directs Zorn Labs (since 2026-03), "
                                                               "having advised the Helix Atlas project in 2023."}])
    op = nb.revision(out["revision_id"])["ops"][0]
    assert op["payload"]["as_of"] == T26
    hist = nb.entity(MIRA)["summary_history"]
    assert [h["at"] for h in hist] == [t(18, 7, 2023), T26, T26] and "Helix" in hist[-1]["summary"]
    assert "Helix" in nb.store.entity_row(MIRA)["summary"]
    assert MIRA not in [x["entity_id"] for x in cons._stale(nb)]
    assert MIRA not in [x["entity_id"] for x in nb.retro_state()["stale_summaries"]]
    # a live note dated after that knowledge (but before today) writes the current summary
    later = t(1, 4, 2026)
    n = note(nb, "Mira Voss now chairs the board.", later)
    nb.apply_revision(rev("rv_live", later, mention(MIRA, n), summary(MIRA, "Mira Voss chairs the board."), note_id=n))
    assert nb.store.entity_row(MIRA)["summary"] == "Mira Voss chairs the board."
    assert w["helix"]


def test_a_rebuild_keeps_the_dated_summaries_and_the_current_one(nb):
    s4(nb)
    cons.set_summaries(nb, [{"entity_id": MIRA, "summary": "Mira Voss directs Zorn Labs, having advised Helix."}])
    before = (nb.store.entity_row(MIRA)["summary"], [h["summary"] for h in nb.entity(MIRA)["summary_history"]])
    nb.rebuild()
    assert (nb.store.entity_row(MIRA)["summary"], [h["summary"] for h in nb.entity(MIRA)["summary_history"]]) == before


# --------------------------------------------------------------------------- 3. communities at the end of a batch

def test_communities_are_due_after_one_earlier_note_a_reinterpretation_or_many_revisions(nb):
    from test_communities import world

    w = world(nb)
    d = cm.due(nb)
    assert d["due"] and d["reasons"] == ["never_computed"]
    nb.recompute_communities()
    assert cm.due(nb)["due"] is False
    # one note dated earlier than what was known (a 2025 note about Dana) is enough (R5 review 4.3)
    old = note(nb, "Dana in 2025.", t(1, 6, 2025))
    nb.apply_revision(rev("rv_old", t(1, 6, 2025), mention("ex:person-dana", old), note_id=old))
    d = cm.due(nb)
    assert d["reasons"] == ["earlier_notes"] and d["retro_notes_since"] == 1 and d["revisions_since"] == 1
    out = cm.refresh_if_due(nb)
    assert out["recomputed"] and out["revisions_since"] == 1 and out["communities"] == 2
    assert out["labels_waiting"] == 2 and out["label_calls"] == 1  # what naming will cost the consolidation
    assert cm.refresh_if_due(nb)["recomputed"] is False
    # a note understood again
    reinterpret(nb, "rv_re", w["n1"], t(1), mention("ex:person-dana", w["n1"]))
    assert cm.due(nb)["reasons"] == ["reinterpreted"]
    nb.recompute_communities()
    # many ordinary revisions (live notes, each later than the last)
    for i in range(cm.DUE_REVISIONS):
        n = note(nb, f"Ben {i}.", t(1 + i, 3))
        nb.apply_revision(rev(f"rv_n{i}", t(1 + i, 3), mention("ex:person-ben", n), note_id=n))
        assert cm.due(nb)["due"] is (i + 1 >= cm.DUE_REVISIONS)
    assert cm.due(nb)["reasons"] == ["revisions"]


def _drain_runner(tmp_path):
    from test_flows_headless import SCRIPT

    from abstractnote.flows.runner import NoteRunner, ScriptedLLM
    from abstractnote.tools import bind, bound_notebook

    tools = bind(tmp_path / "nb")
    return NoteRunner.with_llm(ScriptedLLM(copy.deepcopy(SCRIPT)), tools), tools, bound_notebook(tools)


def test_a_drain_ends_by_finding_the_themes_again_when_due_and_says_so(tmp_path):
    from test_communities import world

    runner, tools, nb_ = _drain_runner(tmp_path)
    world(nb_)
    nb_.capture("Met Dana about the LUT budget.", occurred_at=t(4))
    out = runner.run_drain(max_items=5, budget_s=60)
    assert out["processed"] == 1
    c = out["communities"]
    assert c["recomputed"] is True and c["reasons"] == ["never_computed"] and c["revisions_since"] >= 4
    assert "The themes were updated" in out["response"]
    assert nb_.communities()["computed_at"] is not None
    again = runner.run_drain()
    assert again["response"] == "Nothing to process." and again["communities"]["recomputed"] is False


def test_a_themes_failure_at_the_end_of_a_drain_is_said_and_keeps_the_notes(tmp_path):
    runner, tools, nb_ = _drain_runner(tmp_path)
    nb_.capture("Met Dana about the LUT budget.", occurred_at=t(4))

    def broken(**k):
        raise RuntimeError("The graph could not be read.")

    broken.__name__ = "note_communities_recompute"
    tools["note_communities_recompute"] = broken
    runner2 = type(runner).with_llm(runner_llm(), tools)
    out = runner2.run_drain(max_items=5, budget_s=60)
    assert out["processed"] == 1
    assert "The themes could not be updated" in out["response"] and out["communities"]["recomputed"] is False


def runner_llm():
    from test_flows_headless import SCRIPT

    from abstractnote.flows.runner import ScriptedLLM

    return ScriptedLLM(copy.deepcopy(SCRIPT))


# --------------------------------------------------------------------------- 4. statistics

def test_new_entities_are_counted_where_first_seen_and_recorded_time_is_shown_beside(nb):
    n26 = note(nb, "Mira.", T26)
    nb.apply_revision(rev("rv_26", T26, create(MIRA, "Mira Voss"), mention(MIRA, n26), note_id=n26))
    n22 = note(nb, "Mira in 2022.", T22)
    nb.apply_revision(rev("rv_22", T22, mention(MIRA, n22), note_id=n22))
    s = nb.stats(now="2026-10-20T00:00:00Z")
    weeks = {w["week"]: w for w in s["activity_weekly"]}
    assert weeks["2022-05-30"]["new_entities"] == 1  # the week of 2022-06-01, where Mira was first seen
    assert weeks["2026-03-09"]["new_entities"] == 0
    rec = s["recorded_weekly"]
    assert sum(w["notes_received"] for w in rec) == 2 and sum(w["revisions_recorded"] for w in rec) == 2
    assert s["span"]["first_note_at"] == T22 and s["span"]["first_received_at"] > T26


# --------------------------------------------------------------------------- helpers

def _run_code(code: str, **inputs):
    """Run a flow code node's body as the runtime does (a function of its inputs)."""
    src = "def _f(" + ", ".join(inputs) + "):\n" + "\n".join("    " + line for line in code.splitlines())
    scope: dict = {}
    exec(src, scope)  # noqa: S102 - the repository's own flow code
    return scope["_f"](**inputs)


@pytest.fixture(autouse=True)
def _no_cache_bleed():
    cons._pending_cache.clear()
    yield
    cons._pending_cache.clear()
