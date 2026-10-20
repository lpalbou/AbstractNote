"""The generated note-process flow, headless, on the real note tools: notes arriving 2026 -> 2022 -> 2024 end with
each belief in its interval (W23; a ScriptedLLM answers, no model is loaded)."""

from __future__ import annotations

import pytest

from abstractnote.flows.runner import NoteRunner, ScriptedLLM

T26, T22, T24 = "2026-03-01T09:00:00Z", "2022-03-01T09:00:00Z", "2024-03-01T09:00:00Z"


def _ext(model):
    return {"summary": f"Castor runs on {model}.",
            "mentions": [{"ref": "m1", "name": "Castor", "kind": "project", "role": "the entity"}],
            "claims": [{"subject": "m1", "predicate": "ex:runs_on", "object": model, "object_is_literal": True,
                        "evidence": f"Castor runs on {model}"}],
            "tasks": [], "time_refs": [], "topics": ["castor"]}


FIRST = {"mentions": [{"ref": "m1", "decision": "create", "name": "Castor", "kind": "project",
                       "summary": "Castor, a persistent entity."}], "claims": [], "tasks": [],
         "revision_summary": "Castor."}
LATER = {"mentions": [{"ref": "m1", "decision": "reuse", "candidate": 0, "summary": None}],
         "claims": [{"index": 0, "relation_to_existing": "supersedes", "existing": 0, "reason": "another model"}],
         "tasks": [], "revision_summary": "Castor's model."}


def test_note_process_places_out_of_order_notes_in_history(tmp_path):
    try:
        from abstractnote.tools import bind
    except ImportError as e:  # pragma: no cover - the core lane is always there in this repo
        pytest.fail(f"abstractnote.tools is not importable ({e})")
    from abstractnote.store.notebook import Notebook

    script = {"QuickFiling": {"title": "Castor", "kind": "fact", "topics": ["castor"], "urgency": "normal",
                              "language": "en", "one_line": "Castor's model."},
              "Extraction": [_ext("Qwen"), _ext("Llama"), _ext("Mistral")],
              "Linking": [FIRST, LATER, LATER]}
    runner = NoteRunner.with_llm(ScriptedLLM(script), bind(tmp_path / "notebook"))
    for at, model in ((T26, "Qwen"), (T22, "Llama"), (T24, "Mistral")):  # one at a time: the queue goes by date
        runner.run_capture(f"Castor runs on {model}.", idempotency_key=f"k-{model}", source={"client": "cli"},
                           occurred_at=at)
        assert runner.run_drain(max_items=5, budget_s=60)["processed"] == 1
    nb = Notebook.open(tmp_path / "notebook")
    try:
        rows = {r["object_literal"]: (r["valid_from"], r["valid_until"], r["status"])
                for r in nb.store.q("SELECT * FROM claims WHERE predicate='ex:runs_on'")}
        assert rows == {"Llama": (T22, T24, "superseded"), "Mistral": (T24, T26, "superseded"),
                        "Qwen": (T26, None, "active")}
        for at, want in (("2023-01-01", "Llama"), ("2025-01-01", "Mistral"), ("2027-01-01", "Qwen")):
            [b] = [c for c in nb.beliefs_at(at) if c["predicate"] == "ex:runs_on"]
            assert b["object_literal"] == want
            assert nb.kg.active_claim_ids(at) == [b["claim_id"]]
    finally:
        nb.close()
