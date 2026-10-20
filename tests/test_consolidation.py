"""Memory consolidation (backlog 0032): stale summaries found structurally, progress rows with a measured ETA,
the note-consolidate flow end to end on the core tools (ScriptedLLM), its failure routing, and the settings key.
The gateway side (the third automation, the schedule, the view) is in test_consolidation_gateway.py."""

from __future__ import annotations

import copy
import json

import pytest

from test_communities import world
from test_flows_headless import EXTRACTION, LINKING, PINNED, SCRIPT
from test_store_support import create, nb, rev, t  # noqa: F401

from abstractnote.flows.runner import FlowRunError, NoteRunner, ScriptedLLM
from abstractnote.recall import consolidation as cons


def _labels(prompt):
    view = json.loads(prompt.split("Groups:\n", 1)[1])
    return {"communities": [{"index": g["index"], "label": f"Group {g['index']}", "summary": "One group.",
                             "kind": "theme"} for g in view]}


def _summaries(prompt):
    view = json.loads(prompt.split("Entries:\n", 1)[1])
    return {"summaries": [{"index": e["index"], "summary": f"{e['name']} is in the notebook."} for e in view]}


def _llm(**extra):
    return ScriptedLLM({**SCRIPT, "Reflections": {"items": []}, "CommunityLabels": _labels,
                        "EntitySummaries": _summaries, **extra})


def _bound(tmp_path):
    from abstractnote.tools import bind, bound_notebook

    tools = bind(tmp_path / "nb")
    return tools, bound_notebook(tools)


def _recording(tools, name, log):
    real = tools[name]

    def tool(**kw):
        log.append((name, kw))
        return real(**kw)

    tool.__name__ = name
    tools[name] = tool


# --------------------------------------------------------------------------- what is stale


def test_an_entry_created_without_a_summary_by_processing_is_a_placeholder(tmp_path):
    from abstractnote.process.revise import apply_note_revision

    _, nb_ = _bound(tmp_path)
    note = nb_.capture("Met Dana about the LUT budget.", occurred_at=t(1)).note_id
    linking = copy.deepcopy(LINKING)
    linking["mentions"][0]["summary"] = ""  # the model left Dana without a summary
    out = apply_note_revision(nb_, note, EXTRACTION, linking)
    assert any(w.startswith("No summary was written for Dana") for w in out["warnings"])
    by_reason = {nb_.store.entity_row(x["entity_id"])["display_name"]: x["reason"] for x in cons._stale(nb_)}
    assert by_reason == {"Dana": "placeholder"}  # LUT has the model's own summary
    # the same text written as a summary later is no longer the creation placeholder
    dana = next(x["entity_id"] for x in cons._stale(nb_))
    cons.set_summaries(nb_, [{"entity_id": dana, "summary": nb_.store.entity_row(dana)["summary"]}])
    assert cons._stale(nb_) == []


def test_empty_and_changed_summaries_are_stale_until_refreshed(nb):  # noqa: F811
    nb.apply_revision(rev("rv_1", t(1), create("ex:person-dana", "Dana", summary="Dana runs the budget."),
                          create("ex:project-lut", "LUT")))
    assert [(x["entity_id"], x["reason"]) for x in cons._stale(nb)] == [("ex:project-lut", "empty")]
    note = nb.capture("Dana again.", occurred_at=t(2)).note_id
    nb.apply_revision(rev("rv_2", t(2), {"op": "entity.mention", "payload": {"entity_id": "ex:person-dana",
                                                                             "note_id": note}}, note_id=note))
    stale = {x["entity_id"]: x for x in cons._stale(nb)}
    assert stale["ex:person-dana"]["reason"] == "changed"
    assert stale["ex:person-dana"]["since_summary"] == {"notes": 1, "claims": 0}
    seq_1 = nb.store.q1("SELECT seq FROM revisions WHERE revision_id='rv_1'")[0]
    assert stale["ex:person-dana"]["summary_revision_count"] == seq_1
    out = cons.set_summaries(nb, [{"entity_id": "ex:person-dana", "summary": "Dana runs the LUT budget."},
                                  {"entity_id": "ex:nobody", "summary": "x"},
                                  {"entity_id": "ex:project-lut", "summary": "   "}])
    assert out["stored"] == ["ex:person-dana"] and len(out["warnings"]) == 2
    assert [x["entity_id"] for x in cons._stale(nb)] == ["ex:project-lut"]
    history = nb.entity("ex:person-dana")["summary_history"]
    assert [h["summary"] for h in history] == ["Dana runs the budget.", "Dana runs the LUT budget."]
    r = nb.revision(out["revision_id"])
    assert r["action"] == "entity.summary" and r["source"]["client"] == "consolidation"


def test_stale_packets_carry_statements_and_note_excerpts_and_are_offered_once_per_run(tmp_path):
    _, nb_ = _bound(tmp_path)
    world(nb_)
    first = cons.stale_summaries(nb_, 3)
    assert first["count"] == 11 and len(first["items"]) == 3  # no consolidation open: nothing is remembered
    p = next(i for i in first["items"] if i["entity_id"] == "ex:person-dana") if any(
        i["entity_id"] == "ex:person-dana" for i in first["items"]) else first["items"][0]
    assert set(p) >= {"entity_id", "reason", "since_summary", "summary_revision_count", "name", "kind", "aliases",
                      "summary", "claims", "notes"}
    assert p["claims"] and all(isinstance(c, str) for c in p["claims"]) and len(p["notes"]) <= cons.PACKET_NOTES
    cons.begin(nb_)
    a = cons.stale_summaries(nb_, 8)
    b = cons.stale_summaries(nb_, 8)
    assert len(a["items"]) == 8 and a["count"] == 11 and b["count"] == 3
    assert not {i["entity_id"] for i in a["items"]} & {i["entity_id"] for i in b["items"]}


# --------------------------------------------------------------------------- progress and ETA


def test_progress_measures_seconds_per_item_and_the_next_run_starts_from_it(nb, monkeypatch):  # noqa: F811
    clock = {"t": 1_000.0}
    monkeypatch.setattr(cons, "_now_ts", lambda: clock["t"])
    cons.begin(nb)
    assert cons.progress(nb, "summaries", 0, 10)["eta_s"] is None  # nothing measured yet
    clock["t"] += 4
    p = cons.progress(nb, "summaries", 2, 10)
    assert (p["seconds_per_item"], p["eta_s"], p["elapsed_s"]) == (2.0, 16, 4)
    view = cons.run_view(nb)
    assert (view["stage"], view["stage_done"], view["stage_total"], view["eta_s"]) == ("summaries", 2, 10, 16)
    cons.end(nb, "completed", {"communities": 3})
    assert cons.run_view(nb) is None and cons.last(nb)["status"] == "completed"
    assert cons.last(nb)["summaries_refreshed"] == 2 and cons.last(nb)["communities"] == 3
    cons.begin(nb)
    assert cons.progress(nb, "summaries", 0, 5)["eta_s"] == 10  # 5 x the last run's 2.0 s
    with pytest.raises(ValueError, match="stage must be one of"):
        cons.progress(nb, "dreaming", 0, 1)
    cons.begin(nb)  # a row left open is closed as interrupted
    assert cons.history(nb)[0]["status"] == "interrupted"


def test_tools_refuse_without_an_open_consolidation(nb):  # noqa: F811
    with pytest.raises(ValueError, match="No consolidation is running"):
        cons.progress(nb, "labels", 0, 1)
    with pytest.raises(ValueError, match="No consolidation is running"):
        cons.end(nb, "completed")


# --------------------------------------------------------------------------- the flow


def test_one_consolidation_fills_summaries_names_groups_and_reports_every_stage(tmp_path):
    tools, nb_ = _bound(tmp_path)
    world(nb_)
    nb_.recompute_communities()
    before = cons.pending(nb_)
    assert before == {"stale_labels": 0, "unlabelled": 2, "stale_summaries": 11, "reinterpret": 0}
    log = []
    _recording(tools, "note_consolidation_progress", log)
    llm = _llm()
    out = NoteRunner.with_llm(llm, tools).run("note-consolidate", {})
    assert out["response"] == "Consolidated: 0 merges, 2 groups named, 11 summaries refreshed, 0 insights."
    assert sorted(c["label"] for c in nb_.communities()["items"]) == ["Group 0", "Group 1"]
    assert cons.pending(nb_) == {"stale_labels": 0, "unlabelled": 0, "stale_summaries": 0, "reinterpret": 0}
    assert [c["schema"] for c in llm.calls] == ["CommunityLabels", "EntitySummaries", "EntitySummaries",
                                                "Reflections"]  # 8 + 3 entries; labels already done for reflect
    # progress rows advance through every stage, in order
    stages = [kw["stage"] for _, kw in log]
    assert [s for i, s in enumerate(stages) if i == 0 or stages[i - 1] != s] == list(cons.STAGES)
    assert ("summaries", 8, 11) in [(kw["stage"], kw["done"], kw["total"]) for _, kw in log]
    last = cons.last(nb_)
    assert last["status"] == "completed" and (last["labelled"], last["summaries_refreshed"]) == (2, 11)
    row = nb_.store.q1("SELECT * FROM consolidation_runs WHERE consolidation_id=?", (last["consolidation_id"],))
    measured = json.loads(row["stages_json"])
    assert set(measured) == set(cons.STAGES)
    assert all(isinstance(measured[s]["seconds_per_item"], float) for s in ("labels", "summaries", "reflect"))
    # every refreshed summary is the run's: one revision per batch, the consolidation id as its run
    runs = nb_.store.q("SELECT run_id, ops_json FROM revisions WHERE action='entity.summary'")
    assert len(runs) == 2 and {r["run_id"] for r in runs} == {last["consolidation_id"]}
    # the then-and-now caches of the touched entries are fresh
    assert nb_.store.q1("SELECT COUNT(*) FROM then_now_cache")[0] == 11
    again = NoteRunner.with_llm(llm, tools).run("note-consolidate", {})  # nothing left: no labelling or summary call
    assert again["response"].startswith("Consolidated: 0 merges, 0 groups named, 0 summaries refreshed")
    assert [c["schema"] for c in llm.calls][4:] == ["Reflections"]


def test_an_entry_the_model_skips_is_asked_once_more_then_skipped_and_the_loop_goes_on(tmp_path):
    tools, nb_ = _bound(tmp_path)
    world(nb_)

    def first_only(prompt):
        view = json.loads(prompt.split("Entries:\n", 1)[1])
        return {"summaries": [{"index": view[0]["index"], "summary": "Only this one."}]}

    llm = _llm(EntitySummaries=first_only)
    out = NoteRunner.with_llm(llm, tools).run("note-consolidate", {})
    # 8 offered (asked twice: 7 missing), then the other 3 (asked twice: 2 missing), then done
    assert [c["schema"] for c in llm.calls].count("EntitySummaries") == 4
    last = cons.last(nb_)
    assert last["summaries_refreshed"] == 2 and cons.pending(nb_)["stale_summaries"] == 9
    assert last["status"] == "completed_with_gaps" and last["left_out"]["summaries"] == 9
    assert out["response"].startswith("Consolidated with gaps: ")
    assert out["response"].endswith("9 summaries could not be refreshed this time; they will be tried again next time.")


def test_the_budget_bounds_the_summary_loop_and_says_what_it_did_not_reach(tmp_path):
    tools, nb_ = _bound(tmp_path)
    world(nb_)
    llm = _llm()
    NoteRunner.with_llm(llm, tools).run("note-consolidate", {"budget_s": 0.001})
    assert [c["schema"] for c in llm.calls].count("EntitySummaries") == 1
    last = cons.last(nb_)
    assert cons.pending(nb_)["stale_summaries"] == 3
    assert last["status"] == "completed_with_gaps" and last["budget_spent"] is True
    assert last["left_out"] == {"summaries": 3, "labels": 0, "reinterpret": 0, "reasons": [cons.BUDGET_REASON]}


def test_a_tool_failure_inside_the_summary_loop_ends_the_run_and_closes_it_as_failed(tmp_path):
    tools, nb_ = _bound(tmp_path)
    world(nb_)
    calls = []

    def failing(**kw):
        calls.append(kw)
        raise ValueError(PINNED)

    failing.__name__ = "note_entities_set_summaries"
    tools["note_entities_set_summaries"] = failing
    with pytest.raises(FlowRunError, match=r"^This notebook is pinned to the embedding model x"):
        NoteRunner.with_llm(_llm(), tools).run("note-consolidate", {})
    assert len(calls) == 1  # no spinning
    last = cons.last(nb_)
    assert last["status"] == "failed" and last["error"].startswith("This notebook is pinned")
    assert cons.run_view(nb_) is None


def test_a_step_that_breaks_inside_a_loop_ends_the_run_with_a_sentence(tmp_path):
    # inside a while body a raising node only ends the iteration; the round's in-flight flag catches it
    tools, nb_ = _bound(tmp_path)
    world(nb_)
    real = tools["note_entities_stale_summaries"]

    def odd_count(**kw):
        out = real(**kw)
        return dict(out, count="many") if kw.get("limit") else out  # the round's bookkeeping cannot add it up

    odd_count.__name__ = "note_entities_stale_summaries"
    tools["note_entities_stale_summaries"] = odd_count
    llm = _llm()
    with pytest.raises(FlowRunError, match="^Refreshing the summaries did not finish.$"):
        NoteRunner.with_llm(llm, tools).run("note-consolidate", {})
    assert [c["schema"] for c in llm.calls].count("EntitySummaries") == 1
    assert cons.last(nb_)["status"] == "failed"
    assert cons.last(nb_)["error"] == "Refreshing the summaries did not finish."


def test_a_failing_model_call_ends_the_run_and_the_row_reads_as_interrupted_later(tmp_path):
    tools, nb_ = _bound(tmp_path)
    world(nb_)

    def broken(prompt):
        raise RuntimeError("model unavailable")

    with pytest.raises(FlowRunError, match="model unavailable"):
        NoteRunner.with_llm(_llm(EntitySummaries=broken), tools).run("note-consolidate", {})
    assert cons.run_view(nb_)["stage"] == "summaries"  # the run died with its row open (the gateway view says so)
    cons.begin(nb_)
    assert cons.history(nb_)[0]["status"] == "interrupted"


def test_normalize_merges_duplicates_first(tmp_path):
    tools, nb_ = _bound(tmp_path)
    nb_.apply_revision(rev("rv_a", t(1), create("ex:person-dana", "Dana", summary="Dana.")))
    # a legacy duplicate (recorded before canonical names): replayed rows can share a name
    nb_.store.conn.execute("INSERT INTO entities(entity_id, kind, name, summary, created_at, created_revision_id,"
                           " recorded_at, name_key, display_name, key_clash_with) SELECT 'ex:person-dana-2', kind,"
                           " name, summary, created_at, created_revision_id, recorded_at, name_key, display_name,"
                           " 'ex:person-dana' FROM entities WHERE entity_id='ex:person-dana'")
    assert nb_.normalize_entities(None, dry_run=True)["groups"]
    out = NoteRunner.with_llm(_llm(), tools).run("note-consolidate", {})
    assert out["consolidation"]["merges"] == 1
    assert nb_.store.entity_row("ex:person-dana-2")["merged_into"] == "ex:person-dana"


# --------------------------------------------------------------------------- settings


def test_the_consolidate_schedule_setting(nb):  # noqa: F811
    assert nb.settings()["consolidate_schedule"] == {"every": "1d"}
    for every in ("12h", "7d", "off", "1D"):
        assert nb.update_settings({"consolidate_schedule": {"every": every}})["consolidate_schedule"] == {
            "every": every.lower()}
    for bad in ({"every": "2m"}, {"every": "soon"}, {"every": "0d"}, {"every": "1d", "x": 1}, "1d"):
        with pytest.raises(ValueError, match="consolidation"):
            nb.update_settings({"consolidate_schedule": bad})


def test_the_consolidation_automation_defaults_match_the_setting():
    from abstractnote.flows.build import AUTOMATION_DEFAULTS, CONSOLIDATE_DEFAULTS, NOTE_TOOLS
    from abstractnote.store.notebook import DEFAULT_SETTINGS
    from abstractnote.tools import TOOL_NAMES

    d = AUTOMATION_DEFAULTS["note-consolidate"]
    assert d["trigger"]["config"]["every"] == DEFAULT_SETTINGS["consolidate_schedule"]["every"] == "1d"
    assert d["input_data"]["budget_s"] == CONSOLIDATE_DEFAULTS["budget_s"] == 3600
    assert set(d["input_data"]["_runtime"]["allowed_tools"]) == set(NOTE_TOOLS) == set(TOOL_NAMES)
