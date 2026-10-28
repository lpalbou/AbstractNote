"""Nothing is understood twice (ADR 0008 §8; R4 item 3; retro-A S5 / retro-B behaviour 7).

A note captured with ``process_inline`` is claimed (``inline_run_id``) in the enqueue's transaction, so no drain
leases it during the quick filing or the inline note-process; the outcome ends the claim (a failure is a failed
attempt in ``queue_errors``); a claim whose run died is leasable after ``CLAIM_S`` and the cause is logged. The
re-understanding stage claims with ``reinterpret_run_id``: a drain never takes such a note and the stage never
takes a note a drain holds.
"""

from __future__ import annotations

import pytest

from abstractnote import ids
from abstractnote.store.queue import CLAIM_S, CLAIM_STOPPED_ERROR, LEASE_S
from test_store_support import nb  # noqa: F401

T0 = "2026-10-20T12:00:00Z"


def at(seconds):
    return (ids.parse_time(T0) + __import__("datetime").timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_an_inline_capture_is_claimed_with_the_enqueue_and_never_leased(nb):
    """MUTATION: lease any queued row (drop the claim filter) -> the drain takes the inline note."""
    inline = nb.capture("Live note, understood right away.", occurred_at=T0, inline=True, inline_run_id="run-1",
                        now=T0).note_id
    queued = nb.capture("Another note for the drain.", occurred_at=T0, now=T0).note_id
    row = nb.queue.item(inline)
    assert (row["inline_run_id"], row["inline_until"]) == ("run-1", at(CLAIM_S))
    assert nb.queue.claimed(inline, now=at(1))["kind"] == "inline"
    assert [i["note_id"] for i in nb.queue.lease("drain-1", 10, now=at(5))["items"]] == [queued]
    assert nb.queue.item(inline)["state"] == "queued"
    nb.queue.ack(inline, "ok", now=at(60))  # the inline understanding finished
    row = nb.queue.item(inline)
    assert (row["state"], row["inline_run_id"], row["inline_until"]) == ("done", None, None)


def test_an_inline_failure_is_a_failed_attempt_and_ends_the_claim(nb):
    nid = nb.capture("Live note.", occurred_at=T0, inline=True, inline_run_id="run-2", now=T0).note_id
    nb.queue.ack(nid, "error", "The model's linking answer was malformed twice (x); nothing from it was applied.",
                 now=at(30))
    row = nb.queue.item(nid)
    assert (row["state"], row["failures"], row["inline_run_id"]) == ("queued", 1, None)
    assert nb.queue.errors(nid)[0]["error"].startswith("The model's linking answer was malformed twice")
    # after the backoff the drain takes it
    assert [i["note_id"] for i in nb.queue.lease("drain-1", 1, now=at(30 + 61))["items"]] == [nid]


def test_a_dead_inline_run_is_leasable_after_the_claim_timeout_and_the_cause_is_logged(nb):
    """MUTATION: never expire claims (the note waits forever) or expire at once (a live run is raced)."""
    nid = nb.capture("Live note.", occurred_at=T0, inline=True, now=T0).note_id
    assert nb.queue.item(nid)["inline_run_id"] == f"inline@{T0}"  # outside a runtime: named by its time
    assert nb.queue.lease("drain-1", 1, now=at(CLAIM_S - 1))["items"] == []
    assert nb.queue.heartbeat(nid, now=at(CLAIM_S - 1))  # the run is still working: its claim moves on
    assert nb.queue.lease("drain-1", 1, now=at(CLAIM_S + 1))["items"] == []
    [item] = nb.queue.lease("drain-1", 1, now=at(2 * CLAIM_S))["items"]
    assert item["note_id"] == nid and nb.queue.item(nid)["inline_run_id"] is None
    [err] = nb.queue.errors(nid)
    assert err["error"] == CLAIM_STOPPED_ERROR["inline"]
    assert nb.queue.item(nid)["failures"] == 0  # not a failure: the note was never leased
    assert CLAIM_S == LEASE_S == 15 * 60


def test_reinterpret_and_inline_and_drain_exclude_each_other(nb):
    done = nb.capture("An understood note.", occurred_at=T0, now=T0).note_id
    with nb.store.tx() as c:
        c.execute("UPDATE queue SET state='done' WHERE note_id=?", (done,))
    assert nb.queue.claim(done, "reinterpret", "stage-1", now=at(1))
    assert not nb.queue.claim(done, "reinterpret", "stage-2", now=at(2))  # another stage run
    assert nb.queue.claim(done, "reinterpret", "stage-1", now=at(3))  # the same run again: its heartbeat
    assert not nb.queue.claim(done, "inline", "run-x", now=at(4))
    nb.queue.release(done, "reinterpret", "stage-1")
    assert nb.queue.claimed(done, now=at(5)) is None
    # a note leased by a drain is never taken by the stage; a note in the stage is never leased
    leased = nb.capture("Queued note.", occurred_at=T0, now=T0).note_id
    assert [i["note_id"] for i in nb.queue.lease("drain-1", 1, now=at(6))["items"]] == [leased]
    assert not nb.queue.claim(leased, "reinterpret", "stage-1", now=at(7))
    retried = nb.capture("A note re-queued for a retry.", occurred_at=T0, now=T0).note_id
    assert nb.queue.claim(retried, "reinterpret", "stage-1", now=at(8))
    assert nb.queue.lease("drain-1", 5, now=at(9))["items"] == []
    with pytest.raises(ValueError):
        nb.queue.claim("nt_nope", "inline")
    with pytest.raises(ValueError):
        nb.queue.claim(done, "other")


def test_inline_capture_during_a_drain_is_understood_once(tmp_path):
    """Headless note-capture with process_inline: a drain that leases from inside the inline understanding (a
    second handle, as the processor automation would) gets nothing, and the note is understood by exactly one
    note-process (one extraction, one linking). B's S5: 2 children before. MUTATION: drop the claim in capture."""
    from abstractnote.flows.runner import NoteRunner, ScriptedLLM
    from abstractnote.store.notebook import Notebook
    from abstractnote.tools import bind

    nb_dir = tmp_path / "nb"
    seen = {}

    def filing(prompt):  # the drain runs during the quick filing too
        other = Notebook.open(nb_dir)
        try:
            seen["during_filing"] = other.queue.lease("drain-a", 10)["items"]
        finally:
            other.close()
        return {"title": "Zorn", "kind": "fact", "topics": [], "urgency": "normal", "language": "en",
                "one_line": "Zorn."}

    def extract(prompt):
        other = Notebook.open(nb_dir)
        try:
            seen["row"] = dict(other.store.q1("SELECT * FROM queue"))
            seen["during_process"] = other.queue.lease("drain-b", 10)["items"]
        finally:
            other.close()
        return {"summary": "Zorn Labs.", "mentions": [{"ref": "m1", "name": "Zorn Labs", "kind": "org", "aliases": [], "role": "the lab"}],
                "claims": [], "tasks": [], "time_refs": [], "topics": []}

    llm = ScriptedLLM({"QuickFiling": filing, "Extraction": extract,
                       "Linking": {"mentions": [{"ref": "m1", "decision": "create", "name": "Zorn Labs", "kind": "org",
                                                 "summary": "Zorn Labs, a lab."}],
                                   "claims": [], "tasks": [], "revision_summary": "Zorn Labs."}})
    runner = NoteRunner.with_llm(llm, bind(nb_dir))
    out = runner.run_capture("Zorn Labs live note during a drain.", idempotency_key="live",
                             occurred_at="2026-10-20T12:00:00Z", source={"client": "web"}, process_inline=True)
    assert out["status"] == "processed"
    assert seen["during_filing"] == [] and seen["during_process"] == []
    assert seen["row"]["inline_run_id"]
    assert [c["schema"] for c in llm.calls] == ["QuickFiling", "Extraction", "Linking"]
    nb = Notebook.open(nb_dir)
    try:
        row = nb.queue.item(out["note_id"])
        assert (row["state"], row["inline_run_id"]) == ("done", None)
        assert nb.store.q1("SELECT COUNT(*) FROM revisions WHERE note_id=?", (out["note_id"],))[0] == 1
    finally:
        nb.close()
