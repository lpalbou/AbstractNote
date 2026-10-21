"""W28: archiving a note withdraws its reading (its statements close as a retraction "note archived", what it closed
re-opens, entities and tasks only it made retire, its mentions, topics and summaries leave the views, the later notes
that built on it are marked ``source_archived``); unarchiving restores it op by op. Scripted models only (no network);
each test names what it guards (the mutation list is in untracked/reports/W28.md)."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List

import pytest

from test_reinterpret import Clock, claim_index, extraction, link, mention, ml, run, stmt  # noqa: F401

from abstractnote.store.notebook import Notebook

LUT = "ex:project-lut"
BEFORE = "2026-10-20T23:00:00Z"  # after every scripted note was received (Clock), before any archive (wall clock)


def day(k: int) -> str:
    return f"2026-03-{k:02d}T09:00:00Z"


@pytest.fixture
def nb(tmp_path):
    notebook = Notebook.open(tmp_path / "nb")
    yield notebook
    notebook.close()


def thirteen(nb: Notebook) -> Dict[str, Any]:
    """13 notes on LUT, one a day; note k states ``LUT ex:budget k0k`` and supersedes note k-1's budget; note 1 creates
    LUT with a summary, note 10 writes another; note 3 opens a task; note 5 alone names Priya (created there)."""
    clock = Clock()
    notes: List[str] = []
    budgets: List[str] = []
    for k in range(1, 14):
        text = f"LUT budget is {k}0k."
        mentions = [mention("m1", "LUT", "project")]
        decisions: Dict[str, Any] = {"m1": {"name": "LUT", "kind": "project", "summary": "LUT, the first word."}} \
            if k == 1 else {"m1": LUT}
        tasks = []
        if k == 3:
            text += " Ask the board for the LUT budget."
            tasks = [{"text": "Ask the board for the LUT budget", "status": "open", "due": None}]
        if k == 5:
            text += " Priya reviews it."
            mentions.append(mention("m2", "Priya", "person"))
            decisions["m2"] = {"name": "Priya", "kind": "person"}
        claims = [stmt("m1", "ex:budget", f"{k}0k", literal=True, evidence=f"LUT budget is {k}0k")]
        prev = budgets[-1] if budgets else None
        links = [] if prev is None else [
            lambda p, c, prev=prev: {"index": 0, "relation_to_existing": "supersedes",
                                     "existing": claim_index(p, c, prev), "reason": "a new figure"}]
        fn = link(decisions, links, [{"index": 0, "closes_open_task": None, "evidence": ""}] if tasks else [])
        if k == 10:
            inner = fn

            def fn(packet, cands, inner=inner):  # noqa: E306 - note 10 rewrites LUT's summary
                out = inner(packet, cands)
                out["mentions"][0]["summary"] = "LUT, as of day 10."
                return out
        r = run(nb, text, day(k), extraction(mentions, claims, tasks), fn, now=clock.next())
        assert r["out"]["claims_added"], r["out"]
        notes.append(r["note_id"])
        budgets.append(r["out"]["claims_added"][0])
    return {"notes": notes, "budgets": budgets}


def lut_budgets(nb: Notebook, at: str, **kw: Any) -> List[str]:
    return [c["claim_id"] for c in nb.store.beliefs_at(at, entity_id=LUT, **kw) if c["predicate"] == "ex:budget"]


def kg_active(nb: Notebook, at: str) -> List[str]:
    return [c for c in nb.kg.active_claim_ids(at) if nb.store.claim_row(c) is not None and
            nb.store.claim_row(c)["predicate"] == "ex:budget"]


def revisions_snapshot(nb: Notebook) -> Dict[str, tuple]:
    return {r["revision_id"]: (r["ops_json"], r["context_json"], r["summary"], r["warnings_json"], r["action"])
            for r in nb.store.q("SELECT * FROM revisions")}


def journal(nb: Notebook) -> List[sqlite3.Row]:
    con = sqlite3.connect(nb.root / "kg.sqlite3")
    con.row_factory = sqlite3.Row
    try:
        return con.execute("SELECT * FROM memj_closures").fetchall()
    finally:
        con.close()


def test_archiving_12_of_13_notes_leaves_the_card_with_one_note_and_its_beliefs_only(nb):
    """R5's finding: an archived note kept shaping beliefs. Archive 12 of the 13 notes on LUT (all but note 7), one
    person action each: the card shows 1 note and note 7's budget only (re-opened: note 8 had superseded it), the
    summaries the archived notes wrote are gone (marked stale), Priya (only note 5) and note 3's task retired, KG
    agrees at every date, the journal has one retraction "note archived" per archived statement, the later notes
    are marked ``source_archived``, and the archive as received before still shows the old graph.
    MUTATIONS: no _withdraw_note (13 notes), no claim.retract ops, no cross claim.reopen, no narrow of the KG copy,
    no withdrawn filter in beliefs_at, no archived clause in summary_superseded_sql, the archive actions not replayed
    in store.archive, the mark code."""
    w = thirteen(nb)
    notes, budgets = w["notes"], w["budgets"]
    keep = notes[6]
    before_card = nb.entity(LUT)
    assert before_card["note_count"] == 13 and before_card["summary"] == "LUT, as of day 10."
    assert lut_budgets(nb, "2026-10-21T00:00:00Z") == [budgets[12]]
    priya = next(e for e in nb.store.q("SELECT entity_id FROM entities WHERE name='priya'"))[0]
    snap = revisions_snapshot(nb)
    results = []
    for n in notes:
        if n != keep:
            results.append(nb.archive_note(n, "me", client="cli"))
    # one revision per archive, nothing written changed (ADR 0003 append-only; the trigger would abort it too)
    after = revisions_snapshot(nb)
    assert {k: v for k, v in after.items() if k in snap} == snap and len(after) == len(snap) + 12
    assert all(r["revision_id"] and r["status"] == "archived" for r in results)
    card = nb.entity(LUT)
    assert card["note_count"] == 1 and [x["note_id"] for x in card["notes"]] == [keep]
    assert [b["claim_id"] for b in card["beliefs"] if b["predicate"] == "ex:budget"] == [budgets[6]]
    assert nb.store.entity_row(LUT)["note_count"] == 1
    # no archived statement is a belief at any date (claims without a start were "always" in the window)
    for at in ("2026-03-01T00:00:00Z", day(3), "2026-03-07T12:00:00Z", "2026-10-21T00:00:00Z"):
        assert set(lut_budgets(nb, at)) <= {budgets[6]}, at
        assert set(kg_active(nb, at)) <= {budgets[6]}, at
    assert lut_budgets(nb, "2026-10-21T00:00:00Z") == kg_active(nb, "2026-10-21T00:00:00Z") == [budgets[6]]
    row = nb.store.claim_row(budgets[2])
    assert row["status"] == "retracted" and row["closed_reason"] == "note archived"
    # the summaries of notes 1 and 10 left the dated history; LUT waits for a fresh one
    assert card["summary"] is None and card["summary_history"] == []
    assert LUT in [x["entity_id"] for x in nb.retro_state()["stale_summaries"]]
    # only note 5 made Priya, only note 3 had the task
    assert nb.store.entity_row(priya)["retired_revision_id"] == results[4]["revision_id"]
    assert nb.store.q1("SELECT status FROM tasks")[0] == "retired"
    # AbstractMemory: one retraction per archived statement, reason "note archived"
    retracts = [j for j in journal(nb) if j["kind"] == "retract"]
    assert sorted({j["assertion_id"] for j in retracts}) == sorted(b for b in budgets if b != budgets[6])
    assert all(j["reason"] == "note archived" for j in retracts)
    # the kept note was marked for re-understanding because its foundations were archived
    marks = nb.store.q("SELECT reason_code, reason FROM reinterpret_marks WHERE note_id=? AND consumed_at IS NULL",
                       (keep,))
    assert [m["reason_code"] for m in marks] == ["source_archived"] and "archived" in marks[0]["reason"]
    assert [m["note_id"] for m in nb.store.pending_marks()] == [keep]  # an archived note is never re-read
    # the archive as received before: the old graph
    old = nb.entity(LUT, as_received=BEFORE)
    assert old["note_count"] == 13
    assert lut_budgets(nb, "2026-10-21T00:00:00Z", as_received=BEFORE) == [budgets[12]]
    assert nb.store.entity_row(priya) is not None and nb.entity(priya, as_received=BEFORE)["note_count"] == 1


def test_unarchiving_restores_the_reading_op_by_op(nb):
    """note.unarchive is the inverse of the archive: archive 12, bring all 12 back in one revision -> the card, the
    beliefs at every date, KG, the summary, Priya and the task are as before, and the kept note is marked again
    (``source_unarchived``). MUTATIONS: no mention restore, no claim.reopen of the retracted statements, no
    re-close of what the archive re-opened, no task.restore / entity.restore."""
    w = thirteen(nb)
    notes, budgets = w["notes"], w["budgets"]
    keep = notes[6]
    dates = ("2026-03-01T00:00:00Z", day(3), "2026-03-07T12:00:00Z", "2026-03-12T12:00:00Z", "2026-10-21T00:00:00Z")
    beliefs_before = {at: lut_budgets(nb, at) for at in dates}
    kg_before = {at: kg_active(nb, at) for at in dates}
    card_before = nb.entity(LUT)
    gone = [n for n in notes if n != keep]
    for n in gone:
        nb.archive_note(n, "me")
    # the kept note's re-understanding is pending; consume it so the unarchive's mark is visible on its own
    nb.store.conn.execute("UPDATE reinterpret_marks SET consumed_at='2026-10-21T00:00:00Z', outcome='unchanged'")
    nb.store.conn.commit()
    out = nb.unarchive_notes(gone, "me")
    assert out["restored"]["claims_reopened"] == 12 and out["restored"]["claims_closed_again"] == 1
    assert out["restored"]["entities_restored"] == 1 and out["restored"]["tasks_restored"] == 1
    card = nb.entity(LUT)
    assert card["note_count"] == 13 and card["summary"] == card_before["summary"] == "LUT, as of day 10."
    assert {at: lut_budgets(nb, at) for at in dates} == beliefs_before
    assert {at: kg_active(nb, at) for at in dates} == kg_before
    assert nb.store.q1("SELECT status FROM tasks")[0] == "open"
    assert nb.store.q1("SELECT retired_revision_id FROM entities WHERE name='priya'")[0] is None
    assert all(nb.store.note_row(n)["archived_at"] is None and nb.store.note_row(n)["status"] == "processed"
               for n in gone)
    marks = nb.store.q("SELECT reason_code FROM reinterpret_marks WHERE note_id=? AND consumed_at IS NULL", (keep,))
    assert [m["reason_code"] for m in marks] == ["source_unarchived"]
    # archive again: withdrawn again (a new revision, the restore did not lock anything)
    again = nb.archive_note(notes[0], "me")
    assert again["revision_id"] and nb.entity(LUT)["note_count"] == 12


def test_a_note_archived_before_w28_is_withdrawn_when_archived_again(nb):
    """An archive written before W28 (no revision, or a note.archive op without ``withdrawn``) left the reading in
    place; archiving the note again withdraws it now, and a second archive changes nothing.
    MUTATION: archive_note returning early for any archived note."""
    w = thirteen(nb)
    n = w["notes"][12]
    nb.store.conn.execute("UPDATE notes SET archived_at='2026-10-20T00:00:00Z', status='archived' WHERE note_id=?",
                          (n,))
    nb.store.conn.commit()
    assert nb.entity(LUT)["note_count"] == 13  # R5's finding: hidden from the lists, still counted and believed
    assert lut_budgets(nb, "2026-10-21T00:00:00Z") == [w["budgets"][12]]
    out = nb.archive_note(n, "me")
    assert out["revision_id"] and out["withdrawn"]["claims_retracted"] == 1 and out["withdrawn"]["claims_reopened"] == 1
    assert lut_budgets(nb, "2026-10-21T00:00:00Z") == [w["budgets"][11]] and nb.entity(LUT)["note_count"] == 12
    assert nb.archive_note(n, "me")["already_archived"] is True
    # and it comes back as it was (status from before: processed)
    nb.unarchive_note(n, "me")
    assert lut_budgets(nb, "2026-10-21T00:00:00Z") == [w["budgets"][12]]
    assert nb.store.note_row(n)["status"] == "processed"


def test_archive_and_unarchive_survive_a_rebuild(nb):
    """The revisions are the truth (ADR 0003): a rebuild replays the archive and the unarchive into the same graph.
    MUTATION: the withdrawal computed at projection time instead of written as ops (a replay would differ)."""
    w = thirteen(nb)
    for n in w["notes"][:6]:
        nb.archive_note(n, "me")
    nb.unarchive_note(w["notes"][2], "me")
    state = (nb.entity(LUT)["note_count"], lut_budgets(nb, "2026-03-04T12:00:00Z"), lut_budgets(nb, "2026-10-21T00:00:00Z"),
             sorted(r[0] for r in nb.store.q("SELECT claim_id FROM claims WHERE status='retracted'")))
    nb.rebuild()
    assert (nb.entity(LUT)["note_count"], lut_budgets(nb, "2026-03-04T12:00:00Z"),
            lut_budgets(nb, "2026-10-21T00:00:00Z"),
            sorted(r[0] for r in nb.store.q("SELECT claim_id FROM claims WHERE status='retracted'"))) == state
    assert state[0] == 8


def test_a_queued_note_archived_and_brought_back_waits_in_the_queue_again(nb):
    """A note not understood yet has nothing to withdraw; archived it leaves the queue, brought back it waits again.
    MUTATION: the unarchive's requeue."""
    note_id = nb.capture("Something to read later.", occurred_at=day(2)).note_id
    assert nb.store.q1("SELECT state FROM queue WHERE note_id=?", (note_id,))[0] == "queued"
    out = nb.archive_note(note_id, "me")
    assert out["withdrawn"]["claims_retracted"] == 0
    assert nb.store.q1("SELECT state FROM queue WHERE note_id=?", (note_id,))[0] == "done"
    nb.unarchive_note(note_id, "me")
    assert nb.store.q1("SELECT state FROM queue WHERE note_id=?", (note_id,))[0] == "queued"
    assert nb.store.note_row(note_id)["archived_at"] is None
    with pytest.raises(ValueError, match="not archived"):
        nb.unarchive_note(note_id, "me")


def test_the_revision_log_stays_append_only_through_archive_and_unarchive(nb):
    """The trigger (INT2) is live: every op of an archive and an unarchive leaves every written revision as it was;
    the journal records each withdrawal as a retraction and each restore as a new copy (no row updated in place)."""
    w = thirteen(nb)
    snap = revisions_snapshot(nb)
    nb.archive_note(w["notes"][5], "me")
    nb.unarchive_note(w["notes"][5], "me")
    after = revisions_snapshot(nb)
    assert {k: v for k, v in after.items() if k in snap} == snap
    acts = [json.loads(v[0])[0]["op"] for k, v in after.items() if k not in snap]
    assert sorted(acts) == ["note.archive", "note.unarchive"]
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        nb.store.conn.execute("UPDATE revisions SET ops_json='[]' WHERE action='note.archive'")


def _evidence_world(nb: Notebook) -> Dict[str, Any]:
    """Day 1 "30k"; day 20 "40k" supersedes it; day 10 (read last) restates 40k: the belief starts on day 10
    (claim.start_earlier: a reinforcement, the start moved, 30k narrowed to day 10, an earlier-evidence copy in KG)."""
    from test_reinterpret import D20

    clock = Clock()
    budget = lambda v: extraction([mention("m1", "LUT", "project")],  # noqa: E731
                                  [stmt("m1", "ex:budget", v, literal=True, evidence=f"LUT budget {v}")])
    c = run(nb, "LUT budget 30k.", day(1), budget("30k"), link({"m1": {"name": "LUT", "kind": "project"}}),
            now=clock.next())
    p30 = c["out"]["claims_added"][0]
    b = run(nb, "LUT budget 40k now.", D20, budget("40k"), link({"m1": LUT}, claims=[
        lambda p, cs: {"index": 0, "relation_to_existing": "supersedes", "existing": claim_index(p, cs, p30),
                       "reason": "new figure"}]), now=clock.next())
    x40 = b["out"]["claims_added"][0]
    a = run(nb, "LUT budget 40k already.", day(10), budget("40k"), link({"m1": LUT}, claims=[
        lambda p, cs: {"index": 0, "relation_to_existing": "restates", "existing": claim_index(p, cs, x40),
                       "reason": "same figure"}]), now=clock.next())
    assert a["out"]["claims_started_earlier"] == [x40]
    return {"p30": p30, "x40": x40, "a": a["note_id"], "b": b["note_id"], "c": c["note_id"], "D20": D20}


def test_archiving_the_earlier_evidence_drops_its_reinforcement_and_gives_the_start_back(nb):
    """Archive the day-10 note: its reinforcement is dropped, the 40k belief starts on day 20 again, 30k holds until
    day 20 again (index and KG); unarchive: day 10 again in both stores.
    MUTATIONS: no reinforcement.drop / claim.start_restore in plan_archive, no claim.reinforce / claim.start_earlier
    in the unarchive, no fresh evidence id (kg.earlier_evidence_id)."""
    w = _evidence_world(nb)
    p30, x40, d15 = w["p30"], w["x40"], "2026-03-15T09:00:00Z"
    assert lut_budgets(nb, d15) == kg_active(nb, d15) == [x40]
    out = nb.archive_note(w["a"], "me")
    assert out["withdrawn"]["reinforcements_dropped"] == 1 and out["withdrawn"]["starts_restored"] == 1
    assert (nb.store.claim_row(x40)["valid_from"], nb.store.claim_row(p30)["valid_until"]) == (w["D20"], w["D20"])
    assert nb.store.claim_row(x40)["reinforced_count"] == 0
    assert lut_budgets(nb, d15) == kg_active(nb, d15) == [p30]
    back = nb.unarchive_note(w["a"], "me")
    assert back["restored"]["reinforcements_restored"] == 1 and back["restored"]["starts_moved_back"] == 1
    assert (nb.store.claim_row(x40)["valid_from"], nb.store.claim_row(p30)["valid_until"]) == (day(10), day(10))
    assert nb.store.claim_row(x40)["reinforced_count"] == 1
    assert lut_budgets(nb, d15) == kg_active(nb, d15) == [x40]


def test_a_note_that_repeated_a_withdrawn_statement_is_marked_whatever_its_date(nb):
    """Archive the day-20 note that made "40k": the day-10 note (earlier, so no forward dependent) only restated it
    and is marked ``source_archived``; nothing says 40k any more, so 30k is believed again (in both stores) until the
    day-10 note is read again. MUTATION: drop _mark_repeaters; the superseded_by half of the re-open query."""
    w = _evidence_world(nb)
    nb.archive_note(w["b"], "me")
    marks = {m["note_id"]: m for m in nb.store.reinterpret_marks()}
    assert w["a"] in marks and marks[w["a"]]["reason_code"] == "source_archived"
    assert nb.store.claim_row(w["x40"])["status"] == "retracted"
    assert lut_budgets(nb, "2026-10-21T00:00:00Z") == kg_active(nb, "2026-10-21T00:00:00Z") == [w["p30"]]


def test_a_statement_without_a_start_is_no_belief_once_its_note_is_archived(nb):
    """A statement without a known start (``valid_from`` null) was in every window before it closed; its withdrawal
    closes it at its observed date, an empty window that beliefs_at and the graph leave out at any date, in the
    index as in KG. MUTATION: drop claim_withdrawn_sql from beliefs_at or from the graph's edges."""
    from test_store_support import claim, create, rev

    note_id = nb.capture("Dana works on LUT.", occurred_at=day(5)).note_id
    nb.apply_revision(rev("rv_w28_nostart", day(5), create("ex:person-dana", "Dana"), create(LUT, "LUT"),
                          claim("cl_w28", "ex:person-dana", "ex:works_on", LUT, at=day(5), valid_from=None,
                                note_id=note_id),
                          {"op": "entity.mention", "payload": {"entity_id": LUT, "note_id": note_id}},
                          note_id=note_id))
    other = nb.capture("Dana and LUT again.", occurred_at=day(6)).note_id  # keeps both entries alive
    nb.apply_revision(rev("rv_w28_other", day(6), *({"op": "entity.mention", "payload": {"entity_id": e,
                                                                                         "note_id": other}}
                                                    for e in ("ex:person-dana", LUT)), note_id=other))
    early = "2026-03-01T00:00:00Z"
    assert [c["claim_id"] for c in nb.store.beliefs_at(early, entity_id=LUT)] == ["cl_w28"]  # no start: "always"
    assert nb.graph()["edges"]
    nb.archive_note(note_id, "me")
    assert nb.store.beliefs_at(early, entity_id=LUT) == [] and nb.store.beliefs_at(day(6), entity_id=LUT) == []
    assert nb.graph()["edges"] == [] and {n["id"] for n in nb.graph()["nodes"]} >= {LUT, "ex:person-dana"}
