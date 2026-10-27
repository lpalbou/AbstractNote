"""The consolidation summary accounts for the notes its current summary has not absorbed (R1, lead request after R3's
live S4: gpt-oss-120b named Helix 1 time in 4 with three Helix notes marked ``new``).

The EntitySummaries answer lists, before the summary, the ``ties`` the ``new`` notes add (each project,
organisation or person, with the entry's role and date) and the static prompt says the summary must name every one
of them while staying a dated description of what the entry is. ``ties`` is a scaffold for the model, never stored
and never checked against the wording (ADR 0004). A real consolidation runs headless with a ScriptedLLM that
answers with ties: the summary is stored, the ties are not."""

from __future__ import annotations

import json

from abstractnote.flows import build
from abstractnote.flows.runner import NoteRunner
from abstractnote.process import schemas
from abstractnote.recall import consolidation as cons
from test_communities import world
from test_consolidation import _bound, _llm


def test_the_summary_schema_asks_for_the_new_ties_before_the_summary():
    item = schemas.ENTITY_SUMMARIES["properties"]["summaries"]["items"]
    assert list(item["properties"]) == ["index", "ties", "summary"]  # written first, so the summary follows them
    assert item["properties"]["ties"]["default"] == [] and "ties" not in item["required"]
    assert "naming every tie listed in ties" in item["properties"]["summary"]["description"]
    flow = build.flow_schemas()["EntitySummaries"]["properties"]["summaries"]["items"]["properties"]
    assert list(flow) == ["index", "ties", "summary"]  # the flows carry the same schema
    ok = schemas.validate("EntitySummaries", {"summaries": [{"index": 0, "summary": "X is a tool."}]})
    assert ok.summaries[0].ties == []  # an answer without ties is still valid (no note was new)


def test_the_summary_prompt_makes_the_new_notes_ties_part_of_the_summary():
    text = cons.summary_system_prompt()
    assert "read every note marked `new` and list each project, organisation and person" in text
    assert "The summary names every tie you listed in `ties`" in text
    assert "a summary that leaves out a project, organisation or person a `new` note ties the entry to is wrong" in text
    assert "never a list of what the notes say happened" in text  # still a dated description of what it is
    assert "keep the current role and the new ties and leave out older detail" in text
    # the illustration shows a tie carried into the summary
    shape = json.loads(text.split("The answer has this shape (values are illustrations):\n", 1)[1].split("\n")[0])
    first = shape["summaries"][0]
    assert first["ties"] and "Helix Atlas" in first["ties"][0] and "Helix Atlas" in first["summary"]
    schemas.validate("EntitySummaries", shape)


def test_a_consolidation_answer_with_ties_stores_the_summary_only(tmp_path):
    tools, nb_ = _bound(tmp_path)
    world(nb_)
    nb_.recompute_communities()

    def summaries(prompt):
        view = json.loads(prompt.split("Entries:\n", 1)[1])
        return {"summaries": [{"index": e["index"], "ties": [f"works with Helix Atlas ({e['index']})"],
                               "summary": f"{e['name']} works with Helix Atlas."} for e in view]}

    llm = _llm(EntitySummaries=summaries)
    out = NoteRunner.with_llm(llm, tools).run("note-consolidate", {})
    assert "11 summaries refreshed" in out["response"]
    calls = [c for c in llm.calls if c["schema"] == "EntitySummaries"]
    assert calls and all(c["system"] == cons.summary_system_prompt() for c in calls)
    rows = [r[0] for r in nb_.store.q("SELECT summary FROM entity_summaries WHERE revision_id IN (SELECT revision_id"
                                      " FROM revisions WHERE action='entity.summary')")]
    assert len(rows) == 11 and all(r.endswith("works with Helix Atlas.") for r in rows)
    assert not any("(" in r for r in rows)  # the ties are a scaffold, never stored
