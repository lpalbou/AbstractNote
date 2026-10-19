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
    assert (own["entity_id"], own["name"], own["aliases"], own["summary"]) == ("ex:person-d-ng", "d_ng", ["Ng"],
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
    assert [(s["id"], s["name"]) for s in cm["sides"]] == [("ex:org-acme-corp", "acme_corp"), ("ex:org-acme", "acme")]
    assert cm["evidence_note_ids"][0] == w["unsure_note"]
    assert [a["action"] for a in cm["actions"]] == ["confirm", "keep_apart"]
    assert cm["actions"][1]["path"] == f"/note/claims/{w['close_claim']}/retract"
    mg = items["merge"]
    assert (mg["why"], mg["reason"]) == ("merged_by_model", "test")  # ml()'s scripted reason
    assert [(s["id"], s["name"]) for s in mg["sides"]] == [("ex:person-d-ng", "d_ng"), ("ex:person-dana", "dana")]
    assert mg["evidence_note_ids"][0] == w["merge_note"]
    assert mg["actions"][1] == {"action": "unmerge", "method": "POST", "path": "/note/entities/ex:person-dana/unmerge",
                                "body": {"from_id": "ex:person-d-ng", "reason": ""}}
    ct = items["contradiction"]
    assert ct["why"] == "disputed_pair" and [s["id"] for s in ct["sides"]] == [w["budget"], w["budget2"]]
    assert [s["name"] for s in ct["sides"]] == ["lut budget 40k", "lut budget 55k"]
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


# --------------------------------------------------------------------------- archive snapshot (as_received)

FEB, MAR11, NOW_ = "2026-02-15T00:00:00Z", "2026-03-11T00:00:00Z", None


def archive_world(nb):
    """Notes with controlled arrival times. Received 1 Feb: n1 (Jan 5, Dana works at Acme) and n2 (Jan 6, D. and a
    phone number). Imported 10 Mar: n_old (written 1 Dec 2025, Dana knows Beta, a new summary). Received 12 Mar:
    n_sup (Jan 20, Dana works at Gamma, superseding Acme). Received 15 Mar: n_merge (D. is Dana). The phone number
    is retracted by the user (recorded today)."""
    from test_store_support import claim, create, rev

    def note(text, occurred, received):
        nid = nb.capture(text, occurred_at=occurred).note_id
        with nb.store.tx() as c:
            c.execute("UPDATE notes SET received_at=? WHERE note_id=?", (received, nid))
        return nid

    n1 = note("Dana works at Acme.", "2026-01-05T09:00:00Z", "2026-02-01T09:00:00Z")
    n2 = note("D. called from 555.", "2026-01-06T09:00:00Z", "2026-02-01T09:05:00Z")
    old = note("Old notes: Dana knows Beta.", "2025-12-01T09:00:00Z", "2026-03-10T09:00:00Z")
    sup = note("Dana now works at Gamma.", "2026-01-20T09:00:00Z", "2026-03-12T09:00:00Z")
    mrg = note("D. is Dana.", "2026-01-21T09:00:00Z", "2026-03-15T09:00:00Z")
    nb.apply_revision(rev("rv_a1", "2026-01-05T09:00:00Z", create("ex:person-dana", "Dana", summary="Works at Acme."),
                          create("ex:org-acme", "Acme"), mention("ex:person-dana", n1), mention("ex:org-acme", n1),
                          claim("cl_works", "ex:person-dana", "ex:works_at", "ex:org-acme", at="2026-01-05T09:00:00Z",
                                note_id=n1), note_id=n1))
    nb.apply_revision(rev("rv_a2", "2026-01-06T09:00:00Z", create("ex:person-d", "D."), mention("ex:person-d", n2),
                          claim("cl_phone", "ex:person-d", "ex:phone", literal="555", at="2026-01-06T09:00:00Z",
                                note_id=n2), note_id=n2))
    nb.apply_revision(rev("rv_a3", "2025-12-01T09:00:00Z", create("ex:org-beta", "Beta"), mention("ex:org-beta", old),
                          mention("ex:person-dana", old),
                          {"op": "entity.summary", "payload": {"entity_id": "ex:person-dana",
                                                               "summary": "Knew Beta in 2025.", "previous": None}},
                          {"op": "entity.alias", "payload": {"entity_id": "ex:person-dana", "alias": "Dee"}},
                          claim("cl_knows", "ex:person-dana", "schema:knows", "ex:org-beta",
                                at="2025-12-01T09:00:00Z", note_id=old), note_id=old))
    nb.apply_revision(rev("rv_a4", "2026-01-20T09:00:00Z", create("ex:org-gamma", "Gamma"),
                          mention("ex:org-gamma", sup), mention("ex:person-dana", sup),
                          claim("cl_new", "ex:person-dana", "ex:works_at", "ex:org-gamma", at="2026-01-20T09:00:00Z",
                                note_id=sup),
                          {"op": "claim.supersede", "payload": {"claim_id": "cl_works", "by_claim_id": "cl_new",
                                                                "reason": "moved", "valid_until": "2026-01-20T09:00:00Z"}},
                          note_id=sup))
    nb.apply_revision(rev("rv_a5", "2026-01-21T09:00:00Z", {"op": "entity.merge", "payload": {
        "from_id": "ex:person-d", "into_id": "ex:person-dana", "reason": "same"}}, mention("ex:person-dana", mrg),
        note_id=mrg))
    nb.retract_claim("cl_phone", "alice", "wrong number")
    return {"n1": n1, "n2": n2, "old": old, "sup": sup, "mrg": mrg}


def _edges(g):
    return {(e["source"], e["predicate"], e["target"]): e["active"] for e in g["edges"]}


def test_the_graph_as_the_archive_held_it(nb):
    archive_world(nb)
    now = nb.graph()
    assert {n["id"] for n in now["nodes"]} == {"ex:person-dana", "ex:org-acme", "ex:org-beta", "ex:org-gamma"}
    assert _edges(now)[("ex:person-dana", "ex:works_at", "ex:org-acme")] is False
    feb = nb.graph(as_received=FEB)
    assert feb["as_received"] == FEB
    assert {n["id"] for n in feb["nodes"]} == {"ex:person-dana", "ex:org-acme", "ex:person-d"}  # merge not in yet
    assert _edges(feb) == {("ex:person-dana", "ex:works_at", "ex:org-acme"): True}  # supersession not in yet
    dana = {n["id"]: n for n in feb["nodes"]}["ex:person-dana"]
    assert (dana["note_count"], dana["summary"], dana["first_seen"]) == (1, "Works at Acme.", "2026-01-05T09:00:00Z")
    mar = nb.graph(as_received=MAR11)  # the old import is in; the move to Gamma is not
    assert {n["id"] for n in mar["nodes"]} == {"ex:person-dana", "ex:org-acme", "ex:person-d", "ex:org-beta"}
    assert _edges(mar)[("ex:person-dana", "ex:works_at", "ex:org-acme")] is True
    assert {n["id"]: n for n in mar["nodes"]}["ex:person-dana"]["first_seen"] == "2025-12-01T09:00:00Z"
    # at and as_received compose: as of 5 Jan noon, from what the archive held on 15 Feb
    jan5 = nb.graph(at="2026-01-05T12:00:00Z", as_received=FEB)
    assert {n["id"] for n in jan5["nodes"]} == {"ex:person-dana", "ex:org-acme"}
    assert nb.graph(at="2026-01-05T12:00:00Z")["nodes"] and {n["id"] for n in nb.graph(
        at="2026-01-05T12:00:00Z")["nodes"]} == {"ex:person-dana", "ex:org-acme", "ex:org-beta"}
    with pytest.raises(ValueError):
        nb.graph(as_received="last week")


def test_the_entity_card_as_the_archive_held_it(nb):
    w = archive_world(nb)
    now = nb.entity("ex:person-dana")
    assert now["merged_from"] == ["ex:person-d"] and "Dee" in now["aliases"]
    feb = nb.entity("ex:person-dana", as_received=FEB)
    assert feb["as_received"] == FEB and feb["merged_from"] == [] and feb["aliases"] == []
    assert [c["claim_id"] for c in feb["beliefs"]] == ["cl_works"]
    assert [n["note_id"] for n in feb["notes"]] == [w["n1"]] and feb["note_count"] == 1
    assert feb["summary"] == "Works at Acme." and [h["summary"] for h in feb["summary_history"]] == ["Works at Acme."]
    assert {r["other_id"] for r in feb["relations"]} == {"ex:org-acme"}
    mar = nb.entity("ex:person-dana", as_received=MAR11)
    assert {c["claim_id"] for c in mar["beliefs"]} == {"cl_works", "cl_knows"} and mar["aliases"] == ["Dee"]
    assert mar["first_seen"] == "2025-12-01T09:00:00Z"
    # the merged entry is still itself in February; today it resolves to Dana
    d_feb = nb.entity("ex:person-d", as_received=FEB)
    assert d_feb["entity_id"] == "ex:person-d" and [c["claim_id"] for c in d_feb["beliefs"]] == ["cl_phone"]
    assert nb.entity("ex:person-d")["entity_id"] == "ex:person-dana"
    with pytest.raises(ValueError, match="not in your notebook yet"):
        nb.entity("ex:org-beta", as_received=FEB)
    with pytest.raises(ValueError, match="not known yet"):
        nb.entity("ex:person-dana", at="2026-01-01T00:00:00Z", as_received=FEB)
    assert [c["claim_id"] for c in nb.entity("ex:person-dana", at="2026-01-10T00:00:00Z",
                                             as_received=FEB)["beliefs"]] == ["cl_works"]


def test_changes_and_day_as_the_archive_held_them(nb):
    w = archive_world(nb)
    feb = nb.changes(None, 100, as_received=FEB)
    assert {n["note_id"] for n in feb["notes"]} == {w["n1"], w["n2"]}
    assert {e["entity_id"] for e in feb["entities"]} == {"ex:person-dana", "ex:org-acme", "ex:person-d"}
    assert feb["superseded_claims"] == []
    assert set(feb) == set(nb.changes(None, 100))  # the pinned keys, with or without the axis
    later = nb.changes(None, 100)
    assert {c["claim_id"] for c in later["superseded_claims"]} == {"cl_works", "cl_phone"}
    assert nb.day("2026-01-20")["notes"] and nb.day("2026-01-20", as_received=FEB)["notes"] == []
    assert [c["claim_id"] for c in nb.day("2026-01-20")["claims_superseded"]] == ["cl_works"]
    assert nb.day("2026-01-20", as_received=FEB)["claims_superseded"] == []
    assert nb.day("2025-12-01", as_received=FEB)["notes"] == []
    dec = nb.day("2025-12-01", as_received=MAR11)
    assert [n["note_id"] for n in dec["notes"]] == [w["old"]] and dec["entities_created"] == ["ex:org-beta"]
    assert dec["as_received"] == MAR11
