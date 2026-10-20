"""Claims keep a specific, consistent predicate (eval2 D6, W25).

The extraction prompt says how to choose a predicate (the relation read from subject to object, a CURIE only
when it means exactly that, a specific ``ex:`` verb otherwise, never a vague one, one predicate per
single-valued fact) with examples and no closed list; the packet lists the predicates the notebook already
uses about the names it shows, so a later value takes the same predicate and can replace the earlier one.
Part 2 runs the generated note-process flow headless on the real note tools with a ScriptedLLM."""

from __future__ import annotations

from abstractnote.flows.runner import NoteRunner, ScriptedLLM
from abstractnote.process import packets, prompts, schemas
from abstractnote.process.packets import extraction_packet

LINE = "Predicates the notebook already uses about these names: "


def _entity(name, *preds):
    return {"name": name, "kind": "project", "active_claims": [{"predicate": p} for p in preds]}


def _known(cands):
    return extraction_packet({"note_id": "nt_1"}, {"text": "x"}, 1, [], cands, [], [])["known"]


def test_the_packet_lists_the_known_predicates_most_used_first_and_bounded():
    known = _known([_entity("castor", "ex:status", "ex:version", "schema:mentions"),
                    _entity("pollux", "ex:status", "ex:runs_on")])
    line = next(ln for ln in known.splitlines() if ln.startswith(LINE))
    assert line == LINE + "ex:status, ex:runs_on, ex:version."  # mentions statements are never asked for
    many = _known([_entity(f"e{i}", *(f"ex:p{i}_{k}" for k in range(3))) for i in range(10)])
    line = next(ln for ln in many.splitlines() if ln.startswith(LINE))
    assert packets.KNOWN_PREDICATES == 15 and line.count(",") == packets.KNOWN_PREDICATES - 1
    assert LINE not in _known([_entity("castor")])  # no statements, no line


def test_the_extraction_prompt_guides_the_predicate_without_a_closed_list():
    text = prompts.system_prompt("extract")
    assert "Choosing a predicate:" in text
    assert "read from the subject to the object" in text and "never write the relation backwards" in text
    assert "`schema:participant` is someone taking part in an event or a project" in text
    assert "Never a vague predicate" in text and "examples, not a list to choose from" in text
    assert "A fact with one value at a time" in text and "one statement per value" in text
    assert "use the same one for the same relation" in text
    assert "schema:participant" not in text.split("The answer has this shape")[1]  # the example is specific
    desc = schemas.EXTRACTION["properties"]["claims"]["items"]["properties"]["predicate"]["description"]
    assert "from the subject to the object" in desc and "same one for the same kind of fact" in desc
    assert "enum" not in schemas.EXTRACTION["properties"]["claims"]["items"]["properties"]["predicate"]


def _ext(status):
    return {"summary": f"Castor is {status}.",
            "mentions": [{"ref": "m1", "name": "Castor", "kind": "project", "role": "the entity"}],
            "claims": [{"subject": "m1", "predicate": "ex:status", "object": status, "object_is_literal": True,
                        "evidence": f"Castor is {status}"}],
            "tasks": [], "time_refs": [], "topics": ["castor"]}


def test_a_later_value_takes_the_known_predicate_and_replaces_the_earlier_one(tmp_path):
    from abstractnote.store.notebook import Notebook
    from abstractnote.tools import bind

    prompts_seen = []

    def extract(prompt):
        prompts_seen.append(prompt)
        if len(prompts_seen) == 1:
            assert LINE not in prompt
            return _ext("a draft")
        assert LINE + "ex:status." in prompt  # the first note's statement told the second which predicate
        return _ext("in review")

    first = {"mentions": [{"ref": "m1", "decision": "create", "name": "Castor", "kind": "project",
                           "summary": "Castor is a research project."}], "claims": [], "tasks": [],
             "revision_summary": "Castor."}
    later = {"mentions": [{"ref": "m1", "decision": "reuse", "candidate": 0, "summary": None}],
             "claims": [{"index": 0, "relation_to_existing": "supersedes", "existing": 0, "reason": "new status"}],
             "tasks": [], "revision_summary": "Castor's status."}
    script = {"QuickFiling": {"title": "Castor", "kind": "fact", "topics": ["castor"], "urgency": "normal",
                              "language": "en", "one_line": "Castor's status."},
              "Extraction": extract, "Linking": [first, later]}
    runner = NoteRunner.with_llm(ScriptedLLM(script), bind(tmp_path / "notebook"))
    for at, status in (("2026-01-05T09:00:00Z", "a draft"), ("2026-02-05T09:00:00Z", "in review")):
        runner.run_capture(f"Castor is {status}.", idempotency_key=f"k-{status}", source={"client": "cli"},
                           occurred_at=at)
        assert runner.run_drain(max_items=5, budget_s=60)["processed"] == 1
    assert len(prompts_seen) == 2
    nb = Notebook.open(tmp_path / "notebook")
    try:
        rows = {r["object_literal"]: r["status"] for r in nb.store.q("SELECT * FROM claims WHERE predicate='ex:status'")}
        assert rows == {"a draft": "superseded", "in review": "active"}
    finally:
        nb.close()
