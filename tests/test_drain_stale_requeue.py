"""W27 item 2 (review 4.1, ADR 0008 §5): a note read against a statement that was closed while it was being read is
put back in the queue ONCE by the drain (same priority, no failure counted, the reason in queue_errors, nothing of
that reading applied); the next reading is applied, with the stale_context warning if it is stale again. Through the
real flows on the real note tools; a ScriptedLLM answers and closes the statement "meanwhile" from its linking call.
The mutation list is in untracked/reports/W27.md."""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from abstractnote.flows.runner import NoteRunner, ScriptedLLM
from abstractnote.process.context import load_packet
from abstractnote.store.notebook import Notebook
from abstractnote.store.queue import STALE_REQUEUE_ERROR
from abstractnote.tools import bind

T1, T2 = "2026-03-01T09:00:00Z", "2026-04-01T09:00:00Z"
FILING = {"title": "Castor", "kind": "fact", "topics": ["castor"], "urgency": "normal", "language": "en",
          "one_line": "Castor's setup."}


def _ext(facts: Dict[str, str]) -> Dict[str, Any]:
    return {"summary": "Castor's setup.", "mentions": [{"ref": "m1", "name": "Castor", "kind": "project", "role": ""}],
            "claims": [{"subject": "m1", "predicate": p, "object": o, "object_is_literal": True,
                        "evidence": f"Castor {p[3:].replace('_', ' ')} {o}"} for p, o in facts.items()],
            "tasks": [], "time_refs": [], "topics": ["castor"]}


FIRST = {"mentions": [{"ref": "m1", "decision": "create", "name": "Castor", "kind": "project",
                       "summary": "Castor, a persistent entity."}],
         "claims": [{"index": i, "relation_to_existing": "new", "existing": None, "reason": "first"} for i in range(2)],
         "tasks": [], "revision_summary": "Castor."}
REUSE = {"ref": "m1", "decision": "reuse", "candidate": 0, "summary": None}


def _supersede_first_shown(root, note_id: List[str], closed: List[str], *, every_time: bool):
    """The linking of the second note: it replaces the first statement its packet shows; before answering, the
    statement is retracted by a person (the notebook changes while the note is read) on the first call, or on
    every call."""
    def answer(prompt: str) -> Dict[str, Any]:
        nb = Notebook.open(root)
        try:
            shown = load_packet(nb, note_id[0])["existing_claims"]
            if shown and (every_time or not closed):
                nb.retract_claim(shown[0], None, "it was wrong", client="cli")
                closed.append(shown[0])
        finally:
            nb.close()
        rel = ({"index": 0, "relation_to_existing": "supersedes", "existing": 0, "reason": "changed"} if shown else
               {"index": 0, "relation_to_existing": "new", "existing": None, "reason": "nothing to replace"})
        return {"mentions": [REUSE], "claims": [rel], "tasks": [], "revision_summary": "Castor changed."}
    return answer


def _setup(tmp_path, every_time: bool, first_facts: Dict[str, str]):
    root = tmp_path / "notebook"
    note2: List[str] = []
    closed: List[str] = []
    later = _supersede_first_shown(root, note2, closed, every_time=every_time)
    calls: List[str] = []

    def linking(prompt: str) -> Dict[str, Any]:  # the first note creates; every reading of the second replaces
        calls.append(prompt)
        return FIRST if len(calls) == 1 else later(prompt)

    script = {"QuickFiling": FILING, "Extraction": [_ext(first_facts), _ext({"ex:runs_on": "Llama"}),
                                                     _ext({"ex:runs_on": "Llama"})],
              "Linking": linking}
    runner = NoteRunner.with_llm(ScriptedLLM(script), bind(root))
    runner.run_capture("Castor runs on Qwen at OVH.", idempotency_key="k1", source={"client": "cli"}, occurred_at=T1)
    assert runner.run_drain(max_items=5, budget_s=60)["processed"] == 1
    runner.run_capture("Castor now runs on Llama.", idempotency_key="k2", source={"client": "cli"},
                             occurred_at=T2)
    nb = Notebook.open(root)
    try:
        note2.append(nb.store.q1("SELECT note_id FROM notes WHERE occurred_at=?", (T2,))[0])
        priority = nb.queue.item(note2[0])["priority"]
    finally:
        nb.close()
    return root, runner, note2[0], closed, priority


def test_a_note_read_while_its_statement_was_closed_is_read_again_once_at_its_priority(tmp_path):
    """MUTATION: drop the requeue refusal in revise.apply_note_revision (applied at once with the warning, no
    requeue) / map the drain's requeue outcome to error (a failure is counted) / drop the requeue branch of
    Queue.ack (raises)."""
    root, runner, note_id, closed, priority = _setup(tmp_path, False, {"ex:runs_on": "Qwen"})
    drained = runner.run_drain(max_items=5, budget_s=60)
    assert drained["processed"] == 1 and drained["failed"] == 0 and drained["errors"] == []
    assert drained["requeued"] == [{"note_id": note_id, "reason": STALE_REQUEUE_ERROR}]
    assert "1 note is read again: the notebook changed while it was being read." in drained["response"]
    nb = Notebook.open(root)
    try:
        row = nb.queue.item(note_id)
        assert row["state"] == "done" and row["failures"] == 0 and row["priority"] == priority
        assert row["attempts"] == 2
        assert [e["error"] for e in nb.queue.errors(note_id)] == [STALE_REQUEUE_ERROR]
        revs = nb.store.q("SELECT warnings_json FROM revisions WHERE note_id=? AND applied_index=1", (note_id,))
        assert len(revs) == 1 and "no longer believed" not in revs[0][0]  # the second reading saw what holds now
        assert len(closed) == 1
    finally:
        nb.close()


def test_the_requeue_happens_once_then_the_reading_is_applied_with_its_warning(tmp_path):
    """Stale again on the second reading: applied, its statement kept as new with the warning; one requeue logged.
    MUTATION: drop the requeued_stale check (the note goes back again and again until the budget)."""
    root, runner, note_id, closed, _ = _setup(tmp_path, True, {"ex:runs_on": "Qwen", "ex:hosted_at": "OVH"})
    drained = runner.run_drain(max_items=5, budget_s=60)
    assert drained["processed"] == 1 and len(drained["requeued"]) == 1 and drained["failed"] == 0
    nb = Notebook.open(root)
    try:
        assert [e["error"] for e in nb.queue.errors(note_id)] == [STALE_REQUEUE_ERROR]
        [w] = [w for (w,) in nb.store.q("SELECT warnings_json FROM revisions WHERE note_id=? AND applied_index=1",
                                        (note_id,))]
        assert "would replace a statement that is no longer believed on this note's date; it was kept as new" in w
        assert len(closed) == 2
    finally:
        nb.close()


def test_a_note_not_held_by_a_drain_is_never_put_back(tmp_path):
    """Only a drain's lease is put back: the ack refuses a requeue of a note no drain holds."""
    nb = Notebook.open(tmp_path / "nb")
    try:
        note_id = nb.capture("Something.", occurred_at=T1).note_id
        with pytest.raises(ValueError, match="drain is reading"):
            nb.queue.ack(note_id, "requeue")
    finally:
        nb.close()
