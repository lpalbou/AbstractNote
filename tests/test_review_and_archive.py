"""W10 store and recall gaps: processor throughput settings, the review list (unsure matches, merges made by the
model, disagreements) and its confirm action, merged entries read as themselves, and the archive snapshot axis
(``as_received``: what the archive held on a date, excluding later imports)."""

from __future__ import annotations

import pytest

from test_store_support import nb  # noqa: F401


def test_processor_schedule_defaults_follow_the_drain_automation_and_are_checked(nb):
    from abstractnote.flows.build import AUTOMATION_DEFAULTS, DRAIN_DEFAULTS

    sched = nb.settings()["processor_schedule"]
    assert sched == {"every": AUTOMATION_DEFAULTS["note-drain"]["trigger"]["config"]["every"],
                     "max_items": DRAIN_DEFAULTS["max_items"]}
    assert nb.processor_throughput() == {"max_items": sched["max_items"], "interval": sched["every"]}
    nb.update_settings({"processor_schedule": {"every": "90s", "max_items": 5}})
    assert nb.processor_throughput() == {"max_items": 5, "interval": "90s"}
    for bad in ({"every": "2 minutes", "max_items": 5}, {"every": "2m", "max_items": 0},
                {"every": "2m", "max_items": True}, {"every": "2m", "colour": 1}, "2m"):
        with pytest.raises(ValueError):
            nb.update_settings({"processor_schedule": bad})
    # once the processor automation exists, the throughput it was created with is what runs
    nb.set_meta("processor_automation_id", "au_x")
    nb.set_meta("processor_schedule_applied", {"every": "3m", "max_items": 9})
    nb.update_settings({"processor_schedule": {"every": "1h", "max_items": 50}})
    assert nb.processor_throughput() == {"max_items": 9, "interval": "3m"}


# --------------------------------------------------------------------------- a small notebook with a merge

def mention(eid: str, note_id: str):
    return {"op": "entity.mention", "payload": {"entity_id": eid, "note_id": note_id}}


def merged_world(nb):
    """Dana (n1, Jan 5) on LUT; D. Ng (n2, Jan 6); n3 (Jan 7) joins D. Ng into Dana."""
    from test_store_support import claim, create, rev, t

    n1 = nb.capture("Met Dana about LUT.", occurred_at=t(5)).note_id
    n2 = nb.capture("D. Ng called.", occurred_at=t(6)).note_id
    n3 = nb.capture("Dana (D. Ng) wrote back.", occurred_at=t(7)).note_id
    nb.apply_revision(rev("rv_w1", t(5), create("ex:person-dana", "Dana", summary="A colleague."),
                          create("ex:project-lut", "LUT"), mention("ex:person-dana", n1), mention("ex:project-lut", n1),
                          claim("cl_w1", "ex:person-dana", "schema:participant", "ex:project-lut", at=t(5), note_id=n1),
                          note_id=n1, summary="Met Dana."))
    nb.apply_revision(rev("rv_w2", t(6), create("ex:person-d-ng", "D. Ng", aliases=["Ng"], summary="Called once."),
                          mention("ex:person-d-ng", n2), note_id=n2, summary="A call."))
    nb.apply_revision(rev("rv_w3", t(7), {"op": "entity.merge", "payload": {
        "from_id": "ex:person-d-ng", "into_id": "ex:person-dana", "reason": "the same person"}},
        mention("ex:person-dana", n3), note_id=n3, summary="D. Ng is Dana."))
    return {"n1": n1, "n2": n2, "n3": n3}


def test_a_merged_entry_reads_as_itself_and_lists_on_request(nb):
    w = merged_world(nb)
    assert nb.entity("ex:person-d-ng")["entity_id"] == "ex:person-dana"  # the card follows the merge
    own = nb.entity_record("ex:person-d-ng")
    assert (own["entity_id"], own["name"], own["aliases"], own["summary"]) == ("ex:person-d-ng", "D. Ng", ["Ng"],
                                                                                "Called once.")
    assert own["merged_into"] == "ex:person-dana" and own["merged_at"] == own["merge"]["at"]
    assert own["merge"]["revision_id"] == "rv_w3" and own["merge"]["reason"] == "the same person"
    assert own["merge"]["note_id"] == w["n3"] and own["merge"]["summary"] == "D. Ng is Dana."
    assert [n["note_id"] for n in own["notes"]] == [w["n2"]] and own["note_count"] == 1
    assert nb.entity_record("ex:person-dana")["merge"] is None
    listed = {e["entity_id"]: e for e in nb.entities(include_merged=True)["items"]}
    assert listed["ex:person-d-ng"]["merged_into"] == "ex:person-dana"
    assert "ex:person-d-ng" not in {e["entity_id"] for e in nb.entities()["items"]}
    with pytest.raises(ValueError):
        nb.entity_record("ex:person-nobody")


# --------------------------------------------------------------------------- review (W9 gaps 1 and 2)

def review_world(nb):
    """Through the real processing steps (process/revise.py): an unsure match (Acme Corp ~ Acme), a merge the
    model made (D. Ng into Dana) and a disagreement (LUT budget 40k vs 55k)."""
    from test_process import cand_index, claim_index, extraction, ml, run, seed, stmt
    from test_process import mention as m

    seed(nb)
    unsure_note, _, _, o = run(nb, "Acme Corp sent an invoice.", "2026-01-13T10:00:00Z",
                               extraction([m("m1", "Acme Corp", "org")]),
                               lambda p, c: {"mentions": [ml("m1", "unsure", cand_index(c, "m1", "ex:org-acme"),
                                                             name="Acme Corp", kind="org")],
                                             "claims": [], "tasks": [], "revision_summary": "Maybe Acme."})
    [close_claim] = o["claims_added"]
    run(nb, "D. Ng called.", "2026-01-02T10:00:00Z", extraction([m("m1", "D. Ng", "person")]),
        lambda p, c: {"mentions": [ml("m1", "create", name="D. Ng", kind="person")], "claims": [], "tasks": [],
                      "revision_summary": "A call."})
    merge_note, _, _, _ = run(
        nb, "Dana (D. Ng) confirmed by mail.", "2026-01-07T10:00:00Z",
        extraction([m("m1", "Dana", "person", ["D. Ng"])]),
        lambda p, c: {"mentions": [ml("m1", "merge", merge=[cand_index(c, "m1", "ex:person-dana"),
                                                            cand_index(c, "m1", "ex:person-d-ng")], name="Dana")],
                      "claims": [], "tasks": [], "revision_summary": "D. Ng is Dana."})
    _, _, _, o1 = run(nb, "LUT budget is 40k.", "2026-01-10T10:00:00Z",
                      extraction([m("m1", "LUT", "project")], [stmt("m1", "ex:budget", "40k", literal=True)]),
                      lambda p, c: {"mentions": [ml("m1", "reuse", cand_index(c, "m1", "ex:project-lut"))],
                                    "claims": [], "tasks": [], "revision_summary": "Budget."})
    budget = o1["claims_added"][0]
    dispute_note, _, _, o2 = run(
        nb, "Finance says the LUT budget is 55k.", "2026-01-11T10:00:00Z",
        extraction([m("m1", "LUT", "project")], [stmt("m1", "ex:budget", "55k", literal=True)]),
        lambda p, c: {"mentions": [ml("m1", "reuse", cand_index(c, "m1", "ex:project-lut"))],
                      "claims": [{"index": 0, "relation_to_existing": "contradicts",
                                  "existing": claim_index(p, c, budget), "reason": "numbers differ"}],
                      "tasks": [], "revision_summary": "Conflicting budget."})
    return {"unsure_note": unsure_note, "close_claim": close_claim, "merge_note": merge_note,
            "dispute_note": dispute_note, "budget": budget, "budget2": o2["claims_added"][0]}


def _by_kind(nb):
    return {it["kind"]: it for it in nb.review()["items"]}


def test_review_lists_unsure_matches_merges_and_disagreements(nb):
    w = review_world(nb)
    out = nb.review()
    assert out["next_cursor"] is None
    items = _by_kind(nb)
    assert set(items) == {"close_match", "merge", "contradiction"}
    for it in out["items"]:
        assert set(it) == {"review_id", "kind", "why", "reason", "at", "sides", "evidence_note_ids", "actions"}
        assert all(set(s) == {"type", "id", "name", "kind", "note_ids"} for s in it["sides"]) and len(it["sides"]) == 2
        assert all(set(a) == {"action", "method", "path", "body"} for a in it["actions"])
    cm = items["close_match"]
    assert cm["why"] == "unsure_match"
    assert [(s["id"], s["name"]) for s in cm["sides"]] == [("ex:org-acme-corp", "Acme Corp"), ("ex:org-acme", "Acme")]
    assert cm["evidence_note_ids"][0] == w["unsure_note"]
    assert [a["action"] for a in cm["actions"]] == ["confirm", "keep_apart"]
    assert cm["actions"][1]["path"] == f"/note/claims/{w['close_claim']}/retract"
    mg = items["merge"]
    assert (mg["why"], mg["reason"]) == ("merged_by_model", "test")  # ml()'s scripted reason
    assert [(s["id"], s["name"]) for s in mg["sides"]] == [("ex:person-d-ng", "D. Ng"), ("ex:person-dana", "Dana")]
    assert mg["evidence_note_ids"][0] == w["merge_note"]
    assert mg["actions"][1] == {"action": "unmerge", "method": "POST", "path": "/note/entities/ex:person-dana/unmerge",
                                "body": {"from_id": "ex:person-d-ng", "reason": ""}}
    ct = items["contradiction"]
    assert ct["why"] == "disputed_pair" and [s["id"] for s in ct["sides"]] == [w["budget"], w["budget2"]]
    assert [s["name"] for s in ct["sides"]] == ["LUT budget 40k", "LUT budget 55k"]
    assert ct["evidence_note_ids"][0] == w["dispute_note"]
    assert [a["action"] for a in ct["actions"]] == ["retract", "retract"]
    assert [i["at"] for i in out["items"]] == sorted((i["at"] for i in out["items"]), reverse=True)
    assert nb.review(limit=1)["items"] == out["items"][:1]


def test_confirming_a_close_match_merges_and_records_the_account(nb):
    w = review_world(nb)
    cm = _by_kind(nb)["close_match"]
    res = nb.confirm_match(cm["review_id"], "alice", client="web")
    assert res["review_id"] == cm["review_id"]
    assert res["entities_merged"] == [{"from_id": "ex:org-acme-corp", "into_id": "ex:org-acme"}]
    rev = nb.revision(res["revision_id"])
    assert rev["source"] == {"client": "web", "account": "alice"} and rev["note_id"] is None
    assert nb.store.q1("SELECT action, target FROM revisions WHERE revision_id=?", (res["revision_id"],))[:] == (
        "match.confirm", cm["review_id"])
    assert nb.store.canonical("ex:org-acme-corp") == "ex:org-acme"
    assert nb.store.claim_row(w["close_claim"])["status"] == "superseded"
    assert "close_match" not in _by_kind(nb) and "merge" in _by_kind(nb)  # your merge is not up for review
    assert not [e for e in nb.graph()["edges"] if e["source"] == e["target"]]
    # undoing it later does not ask the same question again (the close match was settled)
    nb.unmerge_entity("ex:org-acme", "ex:org-acme-corp", "alice", "not the same")
    assert "close_match" not in _by_kind(nb)
    with pytest.raises(ValueError):
        nb.confirm_match(cm["review_id"], "alice")


def test_confirming_a_merge_keeps_it_and_survives_a_rebuild(nb):
    review_world(nb)
    mg = _by_kind(nb)["merge"]
    res = nb.confirm_match(mg["review_id"], "alice", client="api")
    assert res["entities_merged"] == [] and nb.store.canonical("ex:person-d-ng") == "ex:person-dana"
    assert "merge" not in _by_kind(nb)
    nb.rebuild()
    assert set(_by_kind(nb)) == {"close_match", "contradiction"}
    ct = _by_kind(nb)["contradiction"]
    with pytest.raises(ValueError, match="retracting"):
        nb.confirm_match(ct["review_id"], "alice")
    nb.retract_claim(ct["sides"][0]["id"], "alice", "the 55k figure is right")
    nb.retract_claim(_by_kind(nb)["close_match"]["actions"][1]["path"].split("/")[3], "alice", "different companies")
    assert nb.review()["items"] == []


def test_an_unmerged_model_merge_leaves_the_review(nb):
    review_world(nb)
    mg = _by_kind(nb)["merge"]
    nb.unmerge_entity("ex:person-dana", "ex:person-d-ng", "alice", "")
    assert "merge" not in _by_kind(nb) and nb.review_item(mg["review_id"]) is None
