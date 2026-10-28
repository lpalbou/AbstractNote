"""W27 item 3: a stored note without a kind (an import: imports skip the quick filing a live capture gets) is filed
by its processing (note-process runs the note-file subflow first: the capture's packet, prompt, QuickFiling schema
and settings, one call, thinking off, kind + up to 4 topics), and ``abstractnote refile --missing`` files the notes
understood before that, within a count and a time budget, with one progress row per repair (ADR 0007). Scripted
answers only; the mutation list is in untracked/reports/W27.md."""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from abstractnote.flows import build
from abstractnote.flows.runner import FlowRunError, NoteRunner, ScriptedLLM
from abstractnote.ingest.refile import refile_missing
from abstractnote.store.notebook import Notebook
from abstractnote.tools import bind
from test_process import extraction, mention, ml, run

T1 = "2026-03-01T09:00:00Z"
FILED = {"title": "Castor setup", "kind": "fact", "topics": ["castor", "hosting", "models", "ovh", "gpu"],
         "urgency": "urgent", "language": "en", "one_line": "Castor's setup."}
EXT = {"summary": "Castor runs on Qwen.", "mentions": [{"ref": "m1", "name": "Castor", "kind": "project", "role": ""}],
       "claims": [], "tasks": [], "time_refs": [], "topics": []}
LINK = {"mentions": [{"ref": "m1", "decision": "create", "name": "Castor", "kind": "project",
                      "summary": "Castor, a project."}], "claims": [], "tasks": [], "revision_summary": "Castor."}


def _import(nb: Notebook, text: str, key: str) -> str:
    """What an importer does: store the note at bulk priority, no quick filing."""
    return nb.capture(text, occurred_at=T1, priority=0, idempotency_key=key,
                      source={"client": "importer", "importer": "markdown"}).note_id


def test_the_drain_files_an_imported_note_once_before_reading_it(tmp_path):
    """A capture whose own filing never happened is filed the same way and keeps its place in the queue (the urgency
    is not applied: the note is being read). MUTATION: drop the note-process filing branch (the note stays without
    a kind) / make filing_context say needed for a note that has a kind (a third call) / pass the urgency."""
    root = tmp_path / "notebook"
    llm = ScriptedLLM({"QuickFiling": FILED, "Extraction": EXT, "Linking": LINK})
    runner = NoteRunner.with_llm(llm, bind(root))
    nb = Notebook.open(root)
    try:
        imported = _import(nb, "Castor runs on Qwen at OVH.", "md-1")
        filed_before = nb.capture("Castor is filed already.", occurred_at=T1, idempotency_key="k-2").note_id
        nb.file_note(filed_before, kind="idea", topics=["castor"])
        unfiled = nb.capture("Castor's filing never ran.", occurred_at=T1, idempotency_key="k-3").note_id
        prio = nb.queue.item(imported)["priority"]
        live_prio = nb.queue.item(unfiled)["priority"]
    finally:
        nb.close()
    out = runner.run_drain(max_items=5, budget_s=60)
    assert out["processed"] == 3 and out["failed"] == 0
    calls = [c for c in llm.calls if c["schema"] == "QuickFiling"]
    assert len(calls) == 2 and sum("Castor runs on Qwen at OVH." in c["prompt"] for c in calls) == 1
    nb = Notebook.open(root)
    try:
        row = nb.store.note_row(imported)
        assert row["kind"] == "fact" and row["title"] == "Castor setup" and row["priority"] == prio  # urgency unused
        assert [t for t in nb.store.note_topics(imported) if t in FILED["topics"]] == ["castor", "hosting", "models",
                                                                                      "ovh"]
        assert nb.store.note_row(filed_before)["kind"] == "idea"
        assert nb.store.note_row(unfiled)["kind"] == "fact" and nb.store.note_row(unfiled)["priority"] == live_prio
    finally:
        nb.close()


def test_the_filing_step_is_the_captures_own():
    """Same prompt template, schema, system prompt, thinking off and output cap as the capture's quick filing."""
    flows = build.build_all()
    node = lambda fid, nid: next(n for n in flows[fid]["nodes"] if n["id"] == nid)  # noqa: E731
    cap, fil = node("note-capture", "filing")["data"], node("note-file", "filing")["data"]
    assert cap["pinDefaults"] == fil["pinDefaults"] and fil["pinDefaults"]["thinking"] == "off"
    assert cap.get("effectConfig") == fil.get("effectConfig")
    assert node("note-capture", "filing_prompt")["data"]["pinDefaults"] == node("note-file", "filing_prompt")["data"][
        "pinDefaults"]
    assert {n["data"].get("subflowId") for n in flows["note-process"]["nodes"] if n["id"] == "filing"} == {"note-file"}


def test_a_note_with_nothing_to_read_is_not_filed(tmp_path):
    """No filing call for a note with no text: its processing fails at once with that reason (FIX1)."""
    from abstractnote.tools.queue import NO_TEXT_ERROR

    root = tmp_path / "notebook"
    llm = ScriptedLLM({"QuickFiling": FILED})
    runner = NoteRunner.with_llm(llm, bind(root))
    nb = Notebook.open(root)
    try:
        note_id = _import(nb, "Something.", "md-1")
        with nb.store.tx() as c:
            c.execute("UPDATE notes SET text='', title=NULL WHERE note_id=?", (note_id,))
    finally:
        nb.close()
    assert runner.run_drain()["failed"] == 1 and llm.calls == []
    nb = Notebook.open(root)
    try:
        assert [e["error"] for e in nb.queue.errors(note_id)] == [NO_TEXT_ERROR]
    finally:
        nb.close()


def _understood_without_kind(nb: Notebook, n: int) -> List[str]:
    """Notes understood before the filing step existed (the tools path, no filing): processed, no kind."""
    out = []
    for i in range(n):
        note_id, _, _, _ = run(nb, f"Note number {i} about Castor.", f"2026-03-0{i + 1}T09:00:00Z",
                               extraction([mention("m1", "Castor", "project")]),
                               lambda p, c: {"mentions": [ml("m1", "create", name="Castor", kind="project")],
                                             "claims": [], "tasks": [], "revision_summary": "x"},
                               idempotency_key=f"k-{i}")
        out.append(note_id)
    return out


def test_refile_missing_files_understood_notes_within_its_budget_and_says_what_is_left(tmp_path):
    """One progress row per repair: done, filed, skipped with its reason, kinds, not_reached; status
    completed_with_gaps then completed. MUTATION: drop the skip record (a failed note vanishes) / count not_reached
    from the selection only (a budget gap is called completed) / no per-note row update."""
    root = tmp_path / "notebook"

    def filing(prompt: str) -> Dict[str, Any]:
        if "Note number 1" in prompt:
            raise RuntimeError("the model is unreachable")
        return {**FILED, "kind": "event"}

    runner = NoteRunner.with_llm(ScriptedLLM({"QuickFiling": filing}), bind(root))
    nb = Notebook.open(root)
    try:
        notes = _understood_without_kind(nb, 3)
        seen: List[int] = []

        def file_one(note_id: str) -> Dict[str, Any]:
            seen.append(int(nb.store.q1("SELECT done FROM refile_runs WHERE status='running'")[0]))
            return runner.run("note-file", {"note_id": note_id})

        out = refile_missing(nb, file_one, max_items=2, budget_s=600)
        assert out["status"] == "completed_with_gaps" and out["done"] == 2 and out["total"] == 3
        assert [f["note_id"] for f in out["filed"]] == [notes[0]] and out["kinds"] == {"event": 1}
        assert [s["note_id"] for s in out["skipped"]] == [notes[1]] and "unreachable" in out["skipped"][0]["reason"]
        assert out["not_reached"] == 1 and seen == [0, 1]
        assert out["notice"] == ("2 notes are still without a kind (1 could not be filed and 1 were not reached this"
                                 " time); run it again to file them.")
        assert nb.store.note_row(notes[0])["kind"] == "event" and nb.store.note_row(notes[1])["kind"] is None
        again = refile_missing(nb, file_one, max_items=5, budget_s=600)
        assert [f["note_id"] for f in again["filed"]] == [notes[2]] and again["not_reached"] == 0
        assert again["status"] == "completed_with_gaps" and len(again["skipped"]) == 1  # the unreachable one again
        rows = nb.store.q("SELECT status FROM refile_runs ORDER BY started_at")
        assert len(rows) == 2
    finally:
        nb.close()


def test_refile_stops_at_its_time_budget(tmp_path):
    """A clock past the budget files nothing more: the rest is not_reached, the row is completed_with_gaps."""
    nb = Notebook.open(tmp_path / "nb")
    try:
        _understood_without_kind(nb, 2)
        ticks = iter([0.0, 0.0, 0.0, 10.0, 10.0, 10.0, 10.0])
        out = refile_missing(nb, lambda nid: (nb.file_note(nid, kind="fact"), {"filed": True})[1], max_items=5,
                             budget_s=5, clock=lambda: next(ticks))
        assert len(out["filed"]) == 1 and out["not_reached"] == 1 and out["status"] == "completed_with_gaps"
    finally:
        nb.close()


def test_the_cli_needs_missing(tmp_path, capsys):
    from abstractnote.cli import main

    assert main(["refile", "--notebook", str(tmp_path / "nb"), "--local", "--provider", "x", "--model", "y"]) == 2
    assert "--missing" in capsys.readouterr().err


def test_a_file_note_is_filed_on_its_files_text(tmp_path):
    """An imported file note has no words of its own: the filing reads its file's extracted text, whole.
    MUTATION: drop the extracted-text fallback of filing_context (the filing sees an empty text)."""
    from abstractnote.ingest.capture import filing_context

    nb = Notebook.open(tmp_path / "nb")
    try:
        body = "Castor runs on Qwen at OVH. " * 400
        note_id = nb.capture("", occurred_at=T1, priority=0, attachments=[{"data": body.encode(), "filename":
                                                                           "castor.txt"}]).note_id
        ctx = filing_context(nb, note_id)
        assert ctx["needed"] and ctx["text"].strip() == body.strip()
        nb.file_note(note_id, kind="fact")
        assert filing_context(nb, note_id)["needed"] is False
    finally:
        nb.close()
