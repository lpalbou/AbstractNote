"""The reflect flow names communities (backlog 0030 item 2): recompute -> pending -> one CommunityLabels call ->
label, mapped by index; no model call when nothing waits; the same on the core lane's real tools."""

from __future__ import annotations

import copy

import pytest

from abstractnote.flows.runner import NoteRunner, ScriptedLLM

from test_flows_headless import SCRIPT, Doubles  # noqa: E402

PENDING = {"count": 2, "items": [
    {"index": 0, "community_id": "cm_a", "size": 4, "stale_label": False, "previous_label": None,
     "previous_summary": None, "top_entities": [{"entity_id": "ex:project-lut", "name": "lut", "kind": "project",
                                                  "note_count": 3, "summary": "The LUT project."}],
     "claims": [{"claim_id": "cl_1", "text": "dana participant lut"}], "excerpts": []},
    {"index": 1, "community_id": "cm_b", "size": 3, "stale_label": True, "previous_label": "Old name",
     "previous_summary": "Old.", "top_entities": [], "claims": [], "excerpts": []},
]}
LABELS = {"communities": [
    {"index": 1, "label": "Framework tools", "summary": "The software of the framework.", "kind": "tooling"},
    {"index": 0, "label": "LUT budget", "summary": "The LUT project and its budget.", "kind": "project"},
    {"index": 7, "label": "Invented", "summary": "Out of range.", "kind": "other"},
]}


def _runner(doubles, pending, script_extra=None):
    tools = doubles.tools()
    rec = doubles._rec
    tools["note_communities_recompute"] = rec("note_communities_recompute", lambda **k: {
        "communities": pending["count"], "loose_ends": 1, "entities": 9, "edges": 12, "new": 0, "labels_kept": 0,
        "stale_labels": 0, "unlabelled": pending["count"], "resolution": 1.0, "computed_at": "2026-10-19T10:00:00Z"})
    tools["note_communities_pending"] = rec("note_communities_pending", lambda **k: copy.deepcopy(pending))
    tools["note_communities_label"] = rec("note_communities_label", lambda **k: {
        "labelled": [i["community_id"] for i in k["items"]], "warnings": []})
    llm = ScriptedLLM({**SCRIPT, "CommunityLabels": LABELS, **(script_extra or {})})
    return NoteRunner.with_llm(llm, tools), llm


def test_reflect_names_the_pending_communities_by_index():
    doubles = Doubles()
    runner, llm = _runner(doubles, PENDING)
    out = runner.run_reflect(limit=5)
    assert doubles.names("note_communities") == ["note_communities_recompute", "note_communities_pending",
                                                 "note_communities_label"]
    (call,) = doubles.args("note_communities_label")
    assert call["items"] == [
        {"community_id": "cm_b", "label": "Framework tools", "summary": "The software of the framework.",
         "kind": "tooling"},
        {"community_id": "cm_a", "label": "LUT budget", "summary": "The LUT project and its budget.",
         "kind": "project"}]
    assert out["communities_labelled"] == 2 and out["response"].startswith("Wrote 1 insight.")
    (labels_call,) = [c for c in llm.calls if c["schema"] == "CommunityLabels"]
    assert "Old name" in labels_call["prompt"] and "dana participant lut" in labels_call["prompt"]
    assert "never invent" in labels_call["system"]


def test_reflect_makes_no_labelling_call_when_nothing_waits():
    doubles = Doubles()
    runner, llm = _runner(doubles, {"count": 0, "items": []})
    out = runner.run_reflect(limit=5)
    assert doubles.names("note_communities") == ["note_communities_recompute", "note_communities_pending"]
    assert [c["schema"] for c in llm.calls] == ["Reflections"] and out["communities_labelled"] == 0


def test_reflect_on_the_core_tools_labels_the_notebook_communities(tmp_path):
    from test_communities import world

    from abstractnote.tools import bind, bound_notebook

    tools = bind(tmp_path / "nb")
    nb = bound_notebook(tools)
    world(nb)

    def name_them(prompt):
        return {"communities": [{"index": 0, "label": "Group one", "summary": "One.", "kind": "theme"},
                                {"index": 1, "label": "Group two", "summary": "Two.", "kind": "people"}]}

    llm = ScriptedLLM({**SCRIPT, "Reflections": {"items": []}, "CommunityLabels": name_them})
    out = NoteRunner.with_llm(llm, tools).run_reflect(limit=5)
    assert out["communities_labelled"] == 2
    labels = sorted(c["label"] for c in nb.communities()["items"])
    assert labels == ["Group one", "Group two"]
    again = NoteRunner.with_llm(llm, tools).run_reflect(limit=5)  # nothing new: no second labelling call
    assert again["communities_labelled"] == 0
    assert [c["schema"] for c in llm.calls].count("CommunityLabels") == 1


def test_the_labelling_schema_is_part_of_the_flow_schemas():
    from abstractnote.flows.build import load_schemas
    from abstractnote.recall.communities import COMMUNITY_LABELS_SCHEMA

    assert load_schemas()["CommunityLabels"] == COMMUNITY_LABELS_SCHEMA
    props = COMMUNITY_LABELS_SCHEMA["properties"]["communities"]["items"]["properties"]
    assert props["kind"]["enum"] == ["project", "theme", "people", "tooling", "process", "other"]


@pytest.mark.parametrize("name", ["note_communities_recompute", "note_communities_pending", "note_communities_label"])
def test_the_reflect_automation_may_call_the_community_tools(name):
    from abstractnote.flows.build import AUTOMATION_DEFAULTS, NOTE_TOOLS

    assert name in NOTE_TOOLS
    assert name in AUTOMATION_DEFAULTS["note-reflect"]["input_data"]["_runtime"]["allowed_tools"]
