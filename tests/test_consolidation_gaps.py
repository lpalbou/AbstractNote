"""Nothing stops or is left out silently in a consolidation (ADR 0007, root ADR-0026; the 2026-10-20 incident: an
empty summary answer ended the summaries stage and the run read "completed" with 536 summaries still stale).

An empty or partial answer is asked once more; what stays unanswered is skipped for this run (recorded on its stage,
logged with attribution, excluded from the next offer) and the stage goes on; the close says what was left out
(``completed_with_gaps``, ``left_out``, a one-sentence ``notice``) so ``last`` and ``pending`` agree."""

from __future__ import annotations

import json
import logging

from test_communities import world
from test_consolidation import _bound, _labels, _llm, _summaries
from test_store_support import nb  # noqa: F401

from abstractnote.flows.build import NO_LABEL_REASON, NO_SUMMARY_REASON
from abstractnote.flows.runner import NoteRunner
from abstractnote.recall import consolidation as cons

EMPTY_SUMMARIES = {"summaries": []}
EMPTY_LABELS = {"communities": []}


def _names(prompt):
    return {e["name"] for e in json.loads(prompt.split("Entries:\n", 1)[1])}


def _empty_first(n, empty, then):
    """A scripted answer that is ``empty`` for the first ``n`` calls, then ``then(prompt)``."""
    calls = {"n": 0}

    def answer(prompt):
        calls["n"] += 1
        return empty if calls["n"] <= n else then(prompt)

    return answer


def _stage(nb_, stage):
    row = nb_.store.q1("SELECT stages_json FROM consolidation_runs WHERE consolidation_id=?",
                       (cons.last(nb_)["consolidation_id"],))
    return json.loads(row[0])[stage]


# --------------------------------------------------------------------------- summaries


def test_an_empty_summary_answer_is_asked_again_and_never_ends_the_stage(tmp_path):
    # the incident: the model answered {"summaries": []} once; the run must not stop there
    tools, nb_ = _bound(tmp_path)
    world(nb_)
    llm = _llm(EntitySummaries=_empty_first(1, EMPTY_SUMMARIES, _summaries))
    out = NoteRunner.with_llm(llm, tools).run("note-consolidate", {})
    summary_calls = [c for c in llm.calls if c["schema"] == "EntitySummaries"]
    assert len(summary_calls) == 3  # empty, the same batch again, then the last 3
    assert summary_calls[0]["prompt"] == summary_calls[1]["prompt"]  # the retry is the same batch
    last = cons.last(nb_)
    assert last["summaries_refreshed"] == 11 and last["status"] == "completed" and last["notice"] is None
    assert last["left_out"] == {"summaries": 0, "labels": 0, "reasons": []}
    assert cons.pending(nb_)["stale_summaries"] == 0
    assert out["response"].startswith("Consolidated: ")


def test_entries_unanswered_twice_are_skipped_for_this_run_logged_and_the_stage_goes_on(tmp_path, caplog):
    tools, nb_ = _bound(tmp_path)
    world(nb_)
    llm = _llm(EntitySummaries=_empty_first(2, EMPTY_SUMMARIES, _summaries))
    with caplog.at_level(logging.WARNING, logger="abstractnote.consolidation"):
        out = NoteRunner.with_llm(llm, tools).run("note-consolidate", {})
    summary_calls = [c for c in llm.calls if c["schema"] == "EntitySummaries"]
    assert len(summary_calls) == 3
    skipped_names = _names(summary_calls[0]["prompt"])
    assert len(skipped_names) == 8 and not skipped_names & _names(summary_calls[2]["prompt"])  # the stage moved on
    last = cons.last(nb_)
    assert last["summaries_refreshed"] == 3 and last["status"] == "completed_with_gaps"
    assert last["left_out"] == {"summaries": 8, "labels": 0, "reasons": [NO_SUMMARY_REASON]}
    assert last["notice"] == "8 summaries could not be refreshed this time; they will be tried again next time."
    assert cons.pending(nb_)["stale_summaries"] == 8  # last and pending agree
    stage = _stage(nb_, "summaries")
    assert stage["skipped"] == 8 and stage["reason"] == NO_SUMMARY_REASON and len(stage["skipped_ids"]) == 8
    logged = [r.getMessage() for r in caplog.records if r.name == "abstractnote.consolidation"]
    assert any("stage summaries: skipped for this run (8)" in m and NO_SUMMARY_REASON in m for m in logged)
    assert any("completed with gaps: 8 summaries" in m for m in logged)
    assert out["response"].endswith("8 summaries could not be refreshed this time; they will be tried again next time.")
    # the next run tries them again
    NoteRunner.with_llm(_llm(), tools).run("note-consolidate", {})
    assert cons.last(nb_)["status"] == "completed" and cons.pending(nb_)["stale_summaries"] == 0


def test_a_long_summary_is_kept_whole_with_an_over_length_warning(tmp_path):
    tools, nb_ = _bound(tmp_path)
    world(nb_)
    eid = cons._stale(nb_)[0]["entity_id"]
    long = "A long description. " * 40
    out = cons.set_summaries(nb_, [{"entity_id": eid, "summary": long}])
    assert out["stored"] == [eid] and out["over_length"] == [eid] and out["left_out"] == []
    assert nb_.store.entity_row(eid)["summary"] == long.strip()
    assert out["warnings"][0].startswith(f"over_length: the summary of {eid} has {len(long.strip())} characters")
    assert nb_.revision(out["revision_id"])["warnings"] == out["warnings"]


# --------------------------------------------------------------------------- labels


def test_an_empty_naming_answer_is_asked_again(tmp_path):
    tools, nb_ = _bound(tmp_path)
    world(nb_)
    llm = _llm(CommunityLabels=_empty_first(1, EMPTY_LABELS, _labels))
    NoteRunner.with_llm(llm, tools).run("note-consolidate", {})
    assert [c["schema"] for c in llm.calls].count("CommunityLabels") == 2
    last = cons.last(nb_)
    assert last["labelled"] == 2 and last["status"] == "completed" and cons.pending(nb_)["unlabelled"] == 0


def test_groups_unnamed_twice_are_skipped_excluded_from_the_next_offer_and_reported(tmp_path):
    tools, nb_ = _bound(tmp_path)
    world(nb_)
    real, offers = tools["note_communities_pending"], []

    def one_at_a_time(**kw):  # one group per round, so the second round shows whether the skip list holds
        out = real(**{**kw, "limit": 1}) if "exclude" in kw else real(**kw)
        if "exclude" in kw:
            offers.append([i["community_id"] for i in out["items"]])
        return out

    one_at_a_time.__name__ = "note_communities_pending"
    tools["note_communities_pending"] = one_at_a_time
    llm = _llm(CommunityLabels=EMPTY_LABELS)
    NoteRunner.with_llm(llm, tools).run("note-consolidate", {})
    # each group is asked twice in its own round, the second round offers the other group; reflect asks once
    assert len(offers) == 2 and offers[0] != offers[1] and len({*offers[0], *offers[1]}) == 2
    assert [c["schema"] for c in llm.calls].count("CommunityLabels") == 5
    last = cons.last(nb_)
    assert last["status"] == "completed_with_gaps" and last["summaries_refreshed"] == 11
    assert last["left_out"] == {"summaries": 0, "labels": 2, "reasons": [NO_LABEL_REASON]}
    assert last["notice"] == "2 theme names could not be refreshed this time; they will be tried again next time."
    assert cons.pending(nb_)["unlabelled"] == 2
    assert _stage(nb_, "labels")["skipped"] == 2


def test_a_group_the_reflect_stage_names_after_a_skip_is_not_left_out(tmp_path):
    tools, nb_ = _bound(tmp_path)
    world(nb_)
    llm = _llm(CommunityLabels=_empty_first(2, EMPTY_LABELS, _labels))
    NoteRunner.with_llm(llm, tools).run("note-consolidate", {})
    last = cons.last(nb_)
    assert _stage(nb_, "labels")["skipped"] == 2  # recorded on the stage...
    assert last["status"] == "completed" and last["left_out"]["labels"] == 0  # ...but named by the end of the run


# --------------------------------------------------------------------------- the tools


def test_progress_records_skips_once_with_their_reasons(nb):  # noqa: F811
    cons.begin(nb)
    p = cons.progress(nb, "summaries", 0, 4, skipped=["ex:a", {"id": "ex:b", "reason": "Too thin."}], reason="None.")
    assert p["skipped"] == 2
    cons.progress(nb, "summaries", 1, 4, skipped=["ex:a"], reason="Again.")  # already skipped: counted once
    row = cons.open_row(nb)
    s = json.loads(row["stages_json"])["summaries"]
    assert s["skipped"] == 2 and s["reasons"] == ["None.", "Too thin."]
    assert [x["id"] for x in s["skipped_ids"]] == ["ex:a", "ex:b"]
    out = cons.end(nb, "completed")
    assert out["status"] == "completed"  # neither entry is in the notebook, so neither is still stale


def test_end_never_claims_more_gaps_than_pending_and_a_budget_stop_is_said(tmp_path):
    tools, nb_ = _bound(tmp_path)
    world(nb_)
    cons.begin(nb_)
    out = cons.end(nb_, "completed", {"budget_spent": True, "not_reached": {"summaries": 500, "labels": 0}})
    assert out["status"] == "completed_with_gaps" and out["budget_spent"] is True
    assert out["left_out"]["summaries"] == cons.pending(nb_)["stale_summaries"] == 11
    assert out["notice"] == "11 summaries could not be refreshed this time; they will be tried again next time."


def test_the_labels_pending_call_answers_its_clamp_and_what_waits(tmp_path):
    from abstractnote.recall import communities as cm

    _, nb_ = _bound(tmp_path)
    world(nb_)
    nb_.recompute_communities()
    every = cm.pending_ids(nb_)
    got = cm.pending(nb_, 500, exclude=[every[0]])
    assert got["limit_applied"] == 20 and got["waiting"] == len(every) - 1
    assert [i["community_id"] for i in got["items"]] == every[1:]
