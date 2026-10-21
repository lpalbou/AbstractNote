"""W31: the live gate's blockers (retro-C, R5, 2026-10-21). Scripted models only.

F6 (blocker 2, ADR 0007): a consolidation whose reinterpret stage could not read its notes again closed as
``completed`` with ``reinterpret_failed 35``, ``left_out.reinterpret 0`` and no notice. The cause (the marks of the
F6 run, cs_090b0b6085754932b37d): all 35 notes had been understood again three times that day, so the stage held
each one (``fail_mark`` with the per-day sentence) in seconds, and those holds never reached the stage's skip list,
which is what the close counted. Now a note held or failing in the stage waits alone (a code beside the sentence:
``held_today``, ``held_by_inline``, ``held_by_drain``, ``held_by_reinterpret``, ``error``), its mark keeps the
sentence (GET note ``reinterpret_error``, ``reinterpret.errors``), the close counts every mark the pass left
(``left_out.reinterpret``, ``reinterpret_reasons``), the run closes ``completed_with_gaps`` with the causes on its
row (``why``) and in its notice.

Each test names the mutants that turn it red (untracked/reports/W31.md)."""

from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest

from abstractnote import ids
from abstractnote.store.notebook import Notebook

from test_reinterpret import D05, D20, D25, _stage_llm, _texts


# ----------------------------------------------------------------------------- F6: the reinterpret stage's gaps


def _three_notes(tmp_path) -> Dict[str, Any]:
    """F4's shape: day 20 and day 25 are understood, then day 5 (an earlier note naming the same entries) marks both."""
    from abstractnote.flows.runner import NoteRunner
    from abstractnote.tools import bind

    nb_dir = tmp_path / "nb"
    llm = _stage_llm()
    runner = NoteRunner.with_llm(llm, bind(nb_dir))
    for key, at in (("d20", D20), ("d25", D25), ("d05", D05)):
        runner.run_capture(_texts()[key], idempotency_key=key, occurred_at=at, source={"client": "cli"})
        assert runner.run_drain(max_items=5, budget_s=60)["processed"] == 1
    nb = Notebook.open(nb_dir)
    try:
        by_key = {k: next(r["note_id"] for r in nb.store.q("SELECT * FROM notes") if r["text"] == t)
                  for k, t in _texts().items()}
        assert {m["note_id"] for m in nb.store.reinterpret_marks()} == {by_key["d20"], by_key["d25"]}
    finally:
        nb.close()
    return {"dir": nb_dir, "runner": runner, "llm": llm, **by_key}


def _read_three_times_today(nb_dir, note_id: str) -> None:
    """History as the gate's notebook had it: the note was understood again three times today (consumed marks)."""
    nb = Notebook.open(nb_dir)
    try:
        now = ids.utc_now()
        with nb.store.tx() as c:
            for i in range(3):
                c.execute("INSERT INTO reinterpret_marks(note_id, reason, marked_at, pass_id, taken_at, consumed_at,"
                          " outcome, consumed_revision_id, calls) VALUES (?,?,?,?,?,?,?,?,?)",
                          (note_id, "earlier", now, f"cs_old{i}", now, now, "reinterpreted", f"rv_old{i}", 2))
    finally:
        nb.close()


def _last_stage(nb: Notebook) -> Dict[str, Any]:
    return json.loads(nb.store.q1("SELECT stages_json FROM consolidation_runs ORDER BY started_ts DESC LIMIT 1")[0])


def test_f6_a_stage_that_holds_every_note_closes_with_gaps_says_why_and_the_marks_keep_the_error(tmp_path):
    """F6 replayed (retro-C blocker 2): every marked note was understood again three times today, so the stage holds
    them all. Before: status completed, left_out.reinterpret 0, notice null. Now: completed_with_gaps, both counted,
    the reason on the run (``why``), in ``left_out.reinterpret_reasons`` and in the notice; each mark keeps it (GET
    note ``reinterpret_error``; ``reinterpret.errors``); the stage's skip list has both with the code ``held_today``.
    MUTANTS: _left_out ignores the pass's failed marks AND reinterpret_next records no skip (F6-M1+M2); the notice
    has no Why (F6-M3); the run row keeps no cause (F6-M4); GET note without reinterpret_error (F6-M5)."""
    from abstractnote.recall import consolidation as cons

    s = _three_notes(tmp_path)
    for key in ("d20", "d25"):
        _read_three_times_today(s["dir"], s[key])
    out = s["runner"].run("note-consolidate", {})
    nb = Notebook.open(s["dir"])
    try:
        last = cons.last(nb)
        assert last["reinterpreted"] == 0 and last["reinterpret_failed"] == 2
        assert last["status"] == "completed_with_gaps" and last["left_out"]["reinterpret"] == 2
        assert last["left_out"]["reinterpret_reasons"] == [{"reason": cons.HELD_REASON, "notes": 2}]
        # (the scripted model answers no summaries: the summaries stage leaves its own gap beside this one)
        assert cons.HELD_REASON in last["left_out"]["reasons"] and cons.HELD_REASON in last["why"]
        assert last["notice"].endswith("2 notes could not be understood again this time; they will be read again"
                                       " next time. Why: Already understood again three times today; read again"
                                       " tomorrow (2 notes).")
        assert "Consolidated with gaps" in out["response"] and last["notice"] in out["response"]
        assert last["error"] is None  # a finished run is no failure (a tool output with an error reads as one)
        for key in ("d20", "d25"):
            assert nb.note(s[key])["reinterpret_error"] == cons.HELD_REASON
        assert nb.reinterpret_state()["errors"] == [{"error": cons.HELD_REASON, "notes": 2}]
        stage = _last_stage(nb)["reinterpret"]
        assert {(x["id"], x.get("code")) for x in stage["skipped_ids"]} == {(s["d20"], "held_today"),
                                                                          (s["d25"], "held_today")}
        assert cons.pending(nb)["reinterpret"] == 2  # the close agrees with what waits
    finally:
        nb.close()


def test_f6_the_close_counts_the_marks_even_when_no_stage_skip_was_recorded(tmp_path):
    """The marks are the truth: a note the pass took and could not read again counts in ``left_out`` even when the
    stage record lost it (the flow's report, or a tool that never wrote it), like the 35 of F6.
    MUTANT: _left_out ignores the pass's failed marks (F6-M1)."""
    from abstractnote.recall import consolidation as cons

    s = _three_notes(tmp_path)
    nb = Notebook.open(s["dir"])
    try:
        cons.begin(nb)
        pid = cons.open_row(nb)["consolidation_id"]
        nb.store.take_mark(s["d20"], pid)
        nb.store.fail_mark(s["d20"], pid, "The model could not be reached.")  # no stage skip recorded
        last = cons.end(nb, "completed")
        assert last["status"] == "completed_with_gaps" and last["left_out"]["reinterpret"] == 1
        assert last["left_out"]["reinterpret_reasons"] == [{"reason": "The model could not be reached.", "notes": 1}]
        assert "Why: The model could not be reached (1 note)." in last["notice"]
    finally:
        nb.close()


def test_f6_a_note_held_by_an_inline_reading_waits_alone_and_the_next_pass_reads_it(tmp_path):
    """The live F6 situation (a capture understood inline while the stage runs): the note an inline reading holds is
    skipped with the code ``held_by_inline`` and its mark waits; the other marked note is read again in the same
    stage; the run closes with gaps; once the inline claim ends the next pass reads it.
    MUTANTS: reinterpret_next records no skip and _left_out ignores failed marks (F6-M1+M2); no held code (F6-M6)."""
    from abstractnote.recall import consolidation as cons

    s = _three_notes(tmp_path)
    nb = Notebook.open(s["dir"])
    try:
        assert nb.queue.claim(s["d20"], "inline", "inline@capture")
    finally:
        nb.close()
    s["runner"].run("note-consolidate", {})
    nb = Notebook.open(s["dir"])
    try:
        last = cons.last(nb)
        assert last["reinterpreted"] == 1 and last["status"] == "completed_with_gaps"
        assert last["left_out"]["reinterpret"] == 1
        assert last["left_out"]["reinterpret_reasons"] == [{"reason": cons.BUSY_REASON, "notes": 1}]
        stage = _last_stage(nb)["reinterpret"]
        assert [(x["id"], x.get("code")) for x in stage["skipped_ids"]] == [(s["d20"], "held_by_inline")]
        assert [m["note_id"] for m in nb.store.pending_marks()] == [s["d20"]]
        assert nb.note(s["d20"])["reinterpret_error"] == cons.BUSY_REASON
        nb.queue.release(s["d20"], "inline")
    finally:
        nb.close()
    s["runner"].run("note-consolidate", {})
    nb = Notebook.open(s["dir"])
    try:
        last = cons.last(nb)
        assert last["reinterpreted"] == 1 and last["left_out"]["reinterpret"] == 0
        assert last["left_out"]["reinterpret_reasons"] == [] and nb.store.pending_marks() == [] and nb.note(s["d20"])["reinterpret_error"] is None
    finally:
        nb.close()


def test_f6_the_stage_claims_a_note_before_it_reads_its_context(tmp_path, monkeypatch):
    """A note an inline reading holds is not touched by the stage at all: the no-call check (which writes the note's
    context packet) runs only after the stage holds the note's claim. MUTANT: the claim after digest_unchanged (the
    base order, F6-M7)."""
    from abstractnote.recall import consolidation as cons

    s = _three_notes(tmp_path)
    seen: List[str] = []
    real = cons.digest_unchanged

    def spy(nb: Any, note_id: str) -> bool:
        seen.append(note_id)
        return real(nb, note_id)

    monkeypatch.setattr(cons, "digest_unchanged", spy)
    nb = Notebook.open(s["dir"])
    try:
        assert nb.queue.claim(s["d20"], "inline", "inline@capture")
        cons.begin(nb)
        got = cons.reinterpret_next(nb, 2)
        assert [h["id"] for h in got["held"]] == [s["d20"]] and s["d20"] not in seen
        assert [i["note_id"] for i in got["items"]] == [s["d25"]]
        assert (nb.queue.claimed(s["d20"]) or {}).get("kind") == "inline"  # the inline claim is untouched
    finally:
        nb.close()


def test_f6_an_error_on_one_note_holds_that_note_only_and_releases_its_claim(tmp_path, monkeypatch):
    """ADR 0007: an exception while preparing one note (here the no-call check) never fails the round: that note is
    held with the code ``error`` and the error's sentence on its mark, its claim is released, the next note is
    returned. MUTANTS: no per-note guard (the tool raises, F6-M8); the claim kept after the error (F6-M9)."""
    from abstractnote.recall import consolidation as cons

    s = _three_notes(tmp_path)
    real = cons.digest_unchanged

    def broken(nb: Any, note_id: str) -> bool:
        if note_id == s["d20"]:
            raise RuntimeError("the context index is locked")
        return real(nb, note_id)

    monkeypatch.setattr(cons, "digest_unchanged", broken)
    nb = Notebook.open(s["dir"])
    try:
        cons.begin(nb)
        got = cons.reinterpret_next(nb, 2)
        assert got["held"] == [{"id": s["d20"], "code": "error",
                                "reason": f"{cons.FAILED_REASON} the context index is locked."}]
        assert [i["note_id"] for i in got["items"]] == [s["d25"]]
        assert nb.queue.claimed(s["d20"]) is None
        assert nb.note(s["d20"])["reinterpret_error"] == f"{cons.FAILED_REASON} the context index is locked."
    finally:
        nb.close()


# ----------------------------------------------------------------------------- blocker 1b: supersession invariants

from test_reinterpret import Clock, claim_index, link, run  # noqa: E402
from test_reread_slots import D21 as Y21, D23 as Y23, D25 as Y25, ZORN, budget  # noqa: E402

D26 = "2026-06-01T09:00:00Z"


@pytest.fixture
def nb(tmp_path):
    notebook = Notebook.open(tmp_path / "nb")
    yield notebook
    notebook.close()


def _set3(nb: Notebook) -> Dict[str, str]:
    """R5's set 3 before the corruption: the 2025 note's 40k (A), the 2023 note's 25k placed before it (B, superseded
    by A, window [2023-02-07, 2025-02-05)), and the 2021 note's 18k placed before the 25k (C, superseded by B)."""
    clock = Clock()
    a = run(nb, "Zorn Labs annual budget approved at 40k.", Y25, budget("40k"),
            link({"m1": {"name": "Zorn Labs", "kind": "org"}}), now=clock.next())
    x40 = a["out"]["claims_added"][0]
    earlier = lambda p, cs: {"index": 0, "relation_to_existing": "earlier_state_of",  # noqa: E731
                             "existing": claim_index(p, cs, x40), "reason": "the budget before"}
    b = run(nb, "Zorn Labs annual budget is 25k.", Y23, budget("25k"), link({"m1": ZORN}, claims=[earlier]),
            now=clock.next())
    c = run(nb, "Zorn Labs annual budget was 18k.", Y21, budget("18k"), link({"m1": ZORN}, claims=[earlier]),
            now=clock.next())
    x25, x18 = b["out"]["claims_added"][0], c["out"]["claims_added"][0]
    assert (nb.store.claim_row(x25)["superseded_by"], nb.store.claim_row(x18)["superseded_by"]) == (x40, x25)
    return {"x40": x40, "x25": x25, "x18": x18, "a": a["note_id"]}


def _raw(rid: str, *ops: Any) -> Dict[str, Any]:
    """A revision written as is (a person's action or a replayed one): ``ops`` = (name, payload) pairs, then an alias
    (the rest of the revision, which must apply when one op is refused)."""
    return {"revision_id": rid, "at": D26, "summary": "test", "source": {"client": "test"},
            "ops": [{"op": n, "payload": p} for n, p in ops]
            + [{"op": "entity.alias", "payload": {"entity_id": ZORN, "alias": "ZL"}}]}


def assert_lineages_sound(nb: Notebook) -> None:
    """The invariants of docs/data-model.md "Invariants": ``superseded_by`` never forms a cycle, and no statement is
    closed empty in favour of an EARLIER statement that starts before it."""
    rows = {r["claim_id"]: r for r in nb.store.q("SELECT * FROM claims")}
    for cid in rows:
        seen, cur = {cid}, rows[cid]["superseded_by"]
        while cur:
            assert cur not in seen, f"supersession cycle through {cid}"
            seen.add(cur)
            cur = rows[cur]["superseded_by"] if cur in rows else None
    for r in rows.values():
        succ = rows.get(r["superseded_by"] or "")
        if succ is None or not r["valid_from"] or r["valid_until"] is None or r["valid_until"] > r["valid_from"]:
            continue
        s_start = succ["valid_from"] or succ["observed_at"]
        assert not (s_start < r["valid_from"] and succ["observed_at"] < r["observed_at"]), \
            f"{r['claim_id']} erased by the earlier {succ['claim_id']}"


def _forty_k_still_holds(nb: Notebook, s: Dict[str, str]) -> None:
    row = nb.store.claim_row(s["x40"])
    assert (row["status"], row["valid_until"], row["superseded_by"]) == ("active", None, None)
    # (the refused placement's own statement stays as added, beside it: a visible parallel belief, never an erasure)
    assert "40k" in [c["object_literal"] for c in nb.beliefs_at("2026-06-01", entity_id=ZORN)]
    assert s["x40"] in nb.kg.active_claim_ids("2026-06-01")  # AbstractMemory agrees
    assert_lineages_sound(nb)


def _set3_ops(s: Dict[str, str], nb: Notebook) -> Dict[str, Any]:
    r25, r40 = nb.store.claim_row(s["x25"]), nb.store.claim_row(s["x40"])
    fields = {k: r25[k] for k in ("subject_id", "predicate", "object_id", "object_literal", "certainty", "stance")}
    return {
        # the 40k closed by the 25k at the 25k's date: 40k superseded_by 25k AND 25k superseded_by 40k, 40k emptied
        "supersede": [("claim.supersede", {"claim_id": s["x40"], "by_claim_id": s["x25"], "valid_until": Y23,
                                           "reason": "re-read"})],
        # the earlier evidence path: the 25k "already held" narrows the 40k back to the 25k's date
        "start_earlier": [("claim.start_earlier", {"claim_id": s["x25"], "note_id": None, "observed_at": Y21,
                                                   "valid_from": Y21, "previous_observed_at": r25["observed_at"],
                                                   "previous_valid_from": r25["valid_from"], "evidence": "x",
                                                   "reason": "re-read", "after_claim_id": s["x40"],
                                                   "after_valid_until": Y23, **fields})],
        # a 2025 statement placed BEFORE the 2023 one (an earlier state of an older statement): emptied, erased
        "insert_before": [("claim.add", {"claim_id": "cl_w31copy40", "subject_id": ZORN, "predicate": "ex:annual_budget",
                                         "object_id": None, "object_literal": "40k approved", "certainty": "stated",
                                         "stance": "assert", "observed_at": Y25, "valid_from": Y25,
                                         "valid_until": None, "evidence": "x", "note_id": None}),
                          ("claim.insert_before", {"claim_id": "cl_w31copy40", "before_claim_id": s["x25"],
                                                   "valid_until": Y23, "reason": "re-read"})],
        # a re-opened 40k restored as superseded by the 25k (a replayed state)
        "reopen": [("claim.reopen", {"claim_id": s["x40"], "status": "superseded", "valid_until": Y25,
                                     "superseded_by": s["x25"], "closed_reason": "x", "closed_revision_id": None,
                                     "assertion_id": "as_w31", "previous_assertion_id": s["x40"], "empty_at": Y25,
                                     "assertion": {**{k: r40[k] for k in ("subject_id", "predicate", "object_id",
                                                                          "object_literal", "certainty", "stance",
                                                                          "observed_at", "valid_from", "evidence")},
                                                   "claim_id": "as_w31", "of_claim_id": s["x40"], "valid_until": Y25}})],
        # the 40k pointed at the 18k, which leads through the 25k back to the 40k: a three-step cycle
        "repoint": [("claim.repoint", {"claim_id": s["x40"], "superseded_by": s["x18"], "previous": None})],
    }


@pytest.mark.parametrize("path,codes", [("supersede", {"cycle"}), ("start_earlier", {"cycle"}),
                                        ("insert_before", {"erases_later_belief"}), ("reopen", {"cycle"}),
                                        ("repoint", {"cycle"})])
def test_set3_the_cycle_and_the_erased_later_belief_are_refused_visibly_and_the_rest_applies(nb, path, codes):
    """Blocker 1b (retro-C set 3, Arbor Systems): ``25k superseded_by 40k`` AND ``40k superseded_by 25k`` with the 40k
    window empty, so no budget belief in 2026, must be impossible, whichever op would write it. The offending op is
    refused in the note index AND in AbstractMemory, said in the revision's warnings (``invariant_refused``) and its
    result (``ops_refused`` with the code), and the rest of the revision (an alias) applies.
    MUTANTS: no cycle check (I1: supersede/start_earlier/reopen/repoint red); no erase check (I2: insert_before
    red); refused ops still written to AbstractMemory (I3); no warning (I4); the guard removed from one op (I5..I9)."""
    s = _set3(nb)
    out = nb.apply_revision(_raw(f"rv_w31_{path}", *_set3_ops(s, nb)[path]))
    assert {x["code"] for x in out["ops_refused"]} == codes
    assert any(w.startswith("invariant_refused: ") for w in out["warnings"])
    assert "ZL" in nb.entity(ZORN)["aliases"]  # the rest of the revision applied
    _forty_k_still_holds(nb, s)


def test_the_refusal_is_replayed_by_a_rebuild_once(nb):
    """A rebuild replays the revision log: the same op is refused again (the same state), the warning is not added
    twice, AbstractMemory agrees. MUTANT: the warning appended on every replay (I10)."""
    s = _set3(nb)
    out = nb.apply_revision(_raw("rv_w31_rb", *_set3_ops(s, nb)["supersede"]))
    nb.rebuild()
    warnings = nb.revision(out["revision_id"])["warnings"]
    assert sum(w.startswith("invariant_refused: ") for w in warnings) == 1
    _forty_k_still_holds(nb, s)


def test_abstractmemory_refuses_the_cycle_on_its_own_state(nb):
    """The AbstractMemory projection checks the invariants on its own journal and windows (a second line, whatever
    the index let through): a supersede closure of the 40k naming the 25k, which its journal already says the 40k
    replaced, is refused and named; a sound closure passes. MUTANT: no ClaimStore.lineage_violation (I11)."""
    s = _set3(nb)
    rev = {"revision_id": "rv_w31_kg", "at": D26, "recorded_at": D26, "note_id": None,
           "ops": [{"op": "claim.supersede", "payload": {"claim_id": s["x40"], "by_claim_id": s["x25"],
                                                         "valid_until": Y25, "reason": "x"}}]}
    got = nb.kg.project(rev, {})
    assert [x["code"] for x in got["refused"]] == ["cycle"] and got["closed"] == 0
    assert s["x40"] in nb.kg.active_claim_ids("2026-06-01")
    assert not [c for c in nb.kg.journal.closures(assertion_id=s["x40"], limit=0)]
    ok = {"revision_id": "rv_w31_kg2", "at": D26, "recorded_at": D26, "note_id": None,
          "ops": [{"op": "claim.supersede", "payload": {"claim_id": s["x40"], "by_claim_id": None,
                                                        "valid_until": D26, "reason": "closed by a person"}}]}
    assert nb.kg.project(ok, {})["refused"] == []


def test_a_same_day_correction_and_a_cancelled_plan_still_close_empty(nb):
    """The legitimate empty windows stay: a statement replaced by one that starts at the same moment (B P1d), and a
    plan dated after the note that cancels it (the later note is recorded after it). Control for I2's scope."""
    s = _set3(nb)
    r40 = nb.store.claim_row(s["x40"])
    same = {"claim_id": "cl_w31same", "subject_id": ZORN, "predicate": "ex:annual_budget", "object_id": None,
            "object_literal": "41k", "certainty": "stated", "stance": "assert", "observed_at": D26,
            "valid_from": r40["valid_from"], "valid_until": None, "evidence": "x", "note_id": None}
    out = nb.apply_revision(_raw("rv_w31_same", ("claim.add", same),
                                 ("claim.supersede", {"claim_id": s["x40"], "by_claim_id": "cl_w31same",
                                                      "valid_until": r40["valid_from"], "reason": "corrected"})))
    assert "ops_refused" not in out and nb.store.claim_row(s["x40"])["status"] == "superseded"
    assert_lineages_sound(nb)


# ----------------------------------------------------------------------------- INT3: statements the person withdrew

def _packets(nb: Notebook, note_id: str, ext: Dict[str, Any]) -> Dict[str, Any]:
    from abstractnote.process.merge import merge_extractions
    from abstractnote.process.packets import extraction_packet, link_batches
    from abstractnote.tools.context import ToolContext
    from abstractnote.tools.process import note_context, note_resolve_candidates

    ctx = ToolContext(nb=nb)
    seen = note_context(ctx, note_id)
    known = extraction_packet(seen["note"], seen["sections"][0], 1, seen["similar_notes"], seen["candidate_entities"],
                              seen["open_tasks"], seen["topics"])["known"]
    _, names = merge_extractions([ext])
    cands = note_resolve_candidates(ctx, note_id, names)
    batch = link_batches(seen["note"], ext, cands["mentions"], seen["open_tasks"])[0]
    return {"seen": seen, "known": known, "batch": batch}


def test_a_statement_the_person_retracted_is_shown_withdrawn_never_as_a_previous_statement(nb):
    """INT3's product call: the previous-reading block of a note read again lists a statement the person retracted
    apart, "withdrawn by you — do not restate", with no P number (so the linking cannot name it the same as its
    previous one); the statements the reading still holds keep their P numbers; the prompts say what the line means;
    the re-reading that leaves it out keeps the person's retraction (never closed ``reinterpreted``).
    MUTANTS: the withdrawn statements kept in the numbered list (W1); no withdrawn line in the block (W2); the plan
    closes the retracted statement as reinterpreted (W3); no prompt line (W4)."""
    from abstractnote.process.packets import WITHDRAWN_LABEL
    from abstractnote.process.prompts import load_system
    from test_reinterpret import extraction, mention, stmt

    two = extraction([mention("m1", "Zorn Labs", "org")],
                     [stmt("m1", "ex:annual_budget", "40k", literal=True, evidence="Zorn Labs budget 40k"),
                      stmt("m1", "ex:approved_by", "the board", literal=True, evidence="approved by the board")])
    clock = Clock()
    a = run(nb, "Zorn Labs budget 40k, approved by the board.", Y25, two,
            link({"m1": {"name": "Zorn Labs", "kind": "org"}}), now=clock.next())
    x40, approved = a["out"]["claims_added"]
    nb.retract_claim(approved, None, "The board never approved it.")
    one = extraction([mention("m1", "Zorn Labs", "org")],
                     [stmt("m1", "ex:annual_budget", "40k", literal=True, evidence="Zorn Labs budget 40k")])
    got = _packets(nb, a["note_id"], one)
    reading = got["seen"]["note"]["previous_reading"]
    assert [c["claim_id"] for c in reading["claims"]] == [x40]
    assert [c["claim_id"] for c in reading["withdrawn"]] == [approved]
    previous = got["batch"]["previous"]
    assert "P0 Zorn Labs ex:annual_budget “40k”" in previous and "P1" not in previous
    assert WITHDRAWN_LABEL + "\n  - Zorn Labs ex:approved_by “the board”" in previous
    assert WITHDRAWN_LABEL + "\n  - Zorn Labs ex:approved_by “the board”" in got["known"]
    assert "`withdrawn by you — do not restate`" in load_system("link")
    assert "`withdrawn by you — do not restate`" in load_system("extract")
    same = lambda p, cs: {"index": 0, "relation_to_existing": "same_statement_as_previous", "existing": 0,  # noqa: E731
                          "reason": "same budget"}
    run(nb, "", Y25, one, link({"m1": ZORN}, claims=[same]), reinterpret=True, note_id=a["note_id"], now=clock.next())
    assert nb.store.claim_row(approved)["status"] == "retracted"
    assert nb.store.claim_row(x40)["status"] == "active"


# ----------------------------------------------------------------------------- F1: time consistency

def test_f1_a_note_read_again_sees_when_each_statement_was_stated_and_the_prompts_say_time_decides(nb):
    """F1 (lead's ruling: a statement cannot be resolved by an event dated after it): in the packet of a note read
    again, every known-then and LATER statement says when it was stated, so day 20 sees that the day-5 placeholder
    waits for a board meeting dated after it; a first reading's message is unchanged; the link and extraction
    prompts say a note cannot be the resolution of a condition set for a later date (the model decides the
    certainty: no rule on text); the earlier note's linking is told that a LATER statement stated before the date it
    sets is not that outcome (no earlier_state_of). MUTANTS: no stated date on a re-reading (T1); the date on a first
    reading's known-then lines too (T2); no link prompt line (T3); no extraction prompt line (T4); no LATER stated
    date (T5); no symmetric link line (T6)."""
    from abstractnote.process.prompts import load_system
    from test_reinterpret import day05_budget, day20_budget, extraction, mention, stmt

    clock = Clock()
    a = day20_budget(nb, clock.next())
    day05_budget(nb, clock.next())
    fresh = nb.capture("The LUT budget is discussed.", occurred_at="2026-03-10T09:00:00Z", now=clock.next()).note_id
    first = _packets(nb, fresh, extraction([mention("m1", "LUT", "project")]))  # read for the first time
    assert first["batch"]["previous"] == ""
    fl = [x.strip() for x in first["batch"]["mentions"].splitlines() if x.strip().startswith("#")]
    later = first["batch"]["mentions"].split("LATER (", 1)[1]
    # a first reading: its LATER statements say when they were stated, the rest of its message is unchanged
    assert any(x not in later for x in fl) and any(x in later for x in fl)  # known-then and LATER lines both
    assert all(("(stated " in x) == (x in later) for x in fl)
    ext = extraction([mention("m1", "LUT", "project")],
                     [stmt("m1", "ex:budget", "40k", literal=True, evidence="We go with 40k for the LUT budget")])
    again = _packets(nb, a["note_id"], ext)
    lines = [x.strip() for x in again["batch"]["mentions"].splitlines() if x.strip().startswith("#")]
    assert lines and all("(stated " in x for x in lines)
    assert any("placeholder until the board meets" in x and "(stated 2026-03-05; since 2026-03-05)" in x for x in lines)
    link_prompt, extract_prompt = load_system("link"), load_system("extract")
    assert "a statement of this note is never the resolution of a condition that a statement known on this note's" \
           " date sets for a LATER date" in link_prompt
    assert "that statement is not the condition's outcome: this note's statement is not an `earlier_state_of` it" \
        in link_prompt
    assert "this text, written before that date, cannot be that outcome" in extract_prompt
