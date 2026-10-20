"""Summaries say what an entity IS, and an earlier note never uses later knowledge (eval2 D4, W25).

The linking card shows a candidate as known on the note's date and puts what was recorded on a later day in a
labelled LATER block (ADR 0008 §2; a raw card whose summary is later still marks it ``[LATER]``); the linking and consolidation prompts ask for a dated description of the
entity (kind, role, relations), never a sentence about the note or one event, within the packet's summary
clip. Part 2 drives the generated note-process flow headless on the real note tools with a ScriptedLLM that
answers only when the packet carries the labels it should."""

from __future__ import annotations

import pytest

from abstractnote.flows.runner import NoteRunner, ScriptedLLM
from abstractnote.process import packets, prompts
from abstractnote.process.packets import candidate_card, claim_line, link_batches
from abstractnote.recall import consolidation

NOTE_AT = "2024-03-01T09:00:00Z"


def _cand(summary_at, observed_at):
    return {"index": 0, "name": "castor", "kind": "project", "aliases": [], "summary": "A persistent entity.",
            "summary_at": summary_at, "last_seen": summary_at,
            "active_claims": [{"index": 4, "subject": "castor", "predicate": "ex:runs_on", "object": "Qwen",
                               "object_is_literal": True, "observed_at": observed_at, "line": "castor runs on “Qwen”"}]}


def test_the_card_marks_a_later_summary_and_later_statements():
    head, claim = candidate_card(_cand("2026-03-01T09:00:00Z", "2026-03-01T09:00:00Z"), (), NOTE_AT)
    assert head.startswith("[0] castor — project — [LATER] A persistent entity.")
    assert claim.strip() == "#4 castor runs on “Qwen” (since 2026-03-01, LATER than this note)"
    # the same day or earlier is not later (live notes minutes apart are not marked)
    head, claim = candidate_card(_cand("2024-03-01T18:00:00Z", "2023-01-01T00:00:00Z"), (), NOTE_AT)
    assert "[LATER]" not in head and "LATER" not in claim
    # no note date, or no recorded summary date: nothing is marked
    assert "LATER" not in " ".join(candidate_card(_cand("2026-03-01", "2026-03-01"), ()))
    assert "LATER" not in " ".join(candidate_card(_cand(None, None), (), NOTE_AT))
    assert claim_line({"index": 1, "line": "x", "observed_at": "2026-01-01"}, NOTE_AT).endswith("LATER than this note)")


def test_link_batches_pass_the_note_date_to_the_cards():
    ext = {"mentions": [{"ref": "m1", "name": "Castor", "kind": "project", "role": "the entity"}], "claims": [],
           "tasks": []}
    [batch] = link_batches({"note_id": "nt_1", "occurred_at": NOTE_AT}, ext,
                           [{"ref": "m1", "candidates": [_cand("2026-03-01T09:00:00Z", "2026-03-01")]}], [])
    assert "[LATER] A persistent entity." in batch["mentions"] and "LATER than this note" in batch["mentions"]


def test_the_prompts_ask_for_what_the_entity_is_within_the_clip():
    link = prompts.system_prompt("link")
    assert "says what the entity IS, as it stands on this note's date" in link
    assert "never the note or an event" in link and "Something the entity did" in link
    assert "`[LATER]`" in link and "LATER than this note" in link
    assert f"at most about {packets.SUMMARY_CHARS} characters" in link
    # a measured run dated a third of its summaries after the note when asked to date changing facts
    assert "never a date after this note's date" in link and "(since 20" not in link
    summaries = consolidation.summary_system_prompt()
    assert "saying what the entry IS" in summaries and "never about the notes or one event" in summaries
    assert f"at most about {packets.SUMMARY_CHARS} characters" in summaries
    assert packets.SUMMARY_CHARS <= consolidation.SUMMARY_MAX_CHARS  # the card shows a whole summary


T26, T22 = "2026-03-01T09:00:00Z", "2022-03-01T09:00:00Z"
S26 = "Castor is a persistent research entity that runs on Qwen (since 2026-03)."
S22 = "Castor is a persistent research entity that runs on Llama (since 2022-03)."


def _ext(model):
    return {"summary": f"Castor runs on {model}.",
            "mentions": [{"ref": "m1", "name": "Castor", "kind": "project", "role": "the entity"}],
            "claims": [{"subject": "m1", "predicate": "ex:runs_on", "object": model, "object_is_literal": True,
                        "evidence": f"Castor runs on {model}"}],
            "tasks": [], "time_refs": [], "topics": ["castor"]}


def test_an_earlier_note_sees_the_later_summary_marked_and_keeps_its_own_dated_summary(tmp_path):
    from abstractnote.process.context import entity_view
    from abstractnote.store.notebook import Notebook
    from abstractnote.tools import bind

    seen = []

    def link(prompt):
        seen.append(prompt)
        if len(seen) == 1:
            assert "no existing entity matched" in prompt
            return {"mentions": [{"ref": "m1", "decision": "create", "name": "Castor", "kind": "project",
                                  "summary": S26}], "claims": [], "tasks": [], "revision_summary": "Castor."}
        # the 2022 note: the 2026 summary and statement are in the labelled LATER block (ADR 0008 §2), nothing is
        # shown as known then, and the message asks for the entity as of 2022, so the model can describe 2022 Castor
        assert packets.LATER_LABEL in prompt and "summary as of 2026-03-01: " + S26 in prompt
        assert "nothing was recorded about it by this note's date" in prompt and "[LATER]" not in prompt
        assert "Write every summary as of 2022-03-01, this note's date" in prompt
        return {"mentions": [{"ref": "m1", "decision": "reuse", "candidate": 0, "summary": S22}],
                "claims": [{"index": 0, "relation_to_existing": "supersedes", "existing": 0, "reason": "older model"}],
                "tasks": [], "revision_summary": "Castor's earlier model."}

    script = {"QuickFiling": {"title": "Castor", "kind": "fact", "topics": ["castor"], "urgency": "normal",
                              "language": "en", "one_line": "Castor's model."},
              "Extraction": [_ext("Qwen"), _ext("Llama")], "Linking": link}
    llm = ScriptedLLM(script)
    runner = NoteRunner.with_llm(llm, bind(tmp_path / "notebook"))
    for at, model in ((T26, "Qwen"), (T22, "Llama")):
        runner.run_capture(f"Castor runs on {model}.", idempotency_key=f"k-{model}", source={"client": "cli"},
                           occurred_at=at)
        assert runner.run_drain(max_items=5, budget_s=60)["processed"] == 1
    assert len(seen) == 2
    system = [c["system"] for c in llm.calls if c["schema"] == "Linking"][0]
    assert "says what the entity IS" in (system or "")
    nb = Notebook.open(tmp_path / "notebook")
    try:
        [eid] = [r[0] for r in nb.store.q("SELECT entity_id FROM entities WHERE name='castor'")]
        assert nb.store.entity_row(eid)["summary"] == S26  # the current summary stays the latest in history
        rows = nb.store.q("SELECT at, summary FROM entity_summaries WHERE entity_id=? ORDER BY at", (eid,))
        assert [(r[0], r[1]) for r in rows] == [(T22, S22), (T26, S26)]
        assert entity_view(nb, eid)["summary_at"] == T26
    finally:
        nb.close()


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
