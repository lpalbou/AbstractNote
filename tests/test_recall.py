"""Recall: quick search, entity cards as of a moment, then-versus-now, relations, evidence chains,
reflect candidates, insights, cues, since-you-last-looked and the pinned read shapes."""

from __future__ import annotations

import time

import pytest

from abstractnote.tools.context import ToolContext
from abstractnote.tools.recall import (note_anniversaries, note_changes, note_entity, note_expand,
                                       note_insight_write, note_open_loops, note_resurface, note_search)
from test_process import cand_index, claim_index, extraction, ml, mention, run, stmt
from test_store_support import nb  # noqa: F401


def history(nb):
    """Dana at Acme (Jan), on LUT (Jan), moves to Beta (Mar); a task; an attachment."""
    ext1 = extraction([mention("m1", "Dana", "person"), mention("m2", "Acme", "org"), mention("m3", "LUT", "project")],
                      [stmt("m1", "ex:works_at", "m2", evidence="Dana from Acme"),
                       stmt("m1", "schema:participant", "m3", evidence="about the LUT project")],
                      [{"text": "Send Dana the Pareto", "status": "open", "due": None}], ["lut"])
    n1, _, _, o1 = run(nb, "Met Dana from Acme about the LUT project budget.", "2025-01-05T10:00:00Z", ext1,
                       lambda p, c: {"mentions": [ml("m1", "create", name="Dana", kind="person",
                                                     summary="Works at Acme."),
                                                  ml("m2", "create", name="Acme", kind="org"),
                                                  ml("m3", "create", name="LUT", kind="project")],
                                     "claims": [], "tasks": [], "revision_summary": "Met Dana."},
                       attachments=[{"data": b"Minutes: Dana from Acme leads the LUT budget review.",
                                     "filename": "minutes.txt"}])
    works = [c for c in o1["claims_added"] if nb.store.claim_row(c)["predicate"] == "ex:works_at"][0]
    ext2 = extraction([mention("m1", "Dana", "person"), mention("m2", "Beta", "org")],
                      [stmt("m1", "ex:works_at", "m2", stance="revise", evidence="Dana joined Beta")])
    n2, _, _, o2 = run(nb, "Dana joined Beta; she left Acme.", "2025-03-01T10:00:00Z", ext2,
                       lambda p, c: {"mentions": [ml("m1", "reuse", cand_index(c, "m1", "ex:person-dana"),
                                                     summary="Works at Beta since March."),
                                                  ml("m2", "create", name="Beta", kind="org")],
                                     "claims": [{"index": 0, "relation_to_existing": "supersedes",
                                                 "existing": claim_index(p, c, works), "reason": "moved"}],
                                     "tasks": [], "revision_summary": "Dana moved to Beta."})
    return {"n1": n1, "n2": n2, "works": works, "beta": o2["claims_added"][0]}


def test_quick_search_fuses_fulltext_and_vectors_with_filters_and_facets(nb):
    h = history(nb)
    out = note_search(ToolContext(nb=nb), "LUT budget")
    assert out["notes"][0]["note_id"] == h["n1"]
    assert "fts" in out["notes"][0]["matched_by"]
    assert {"note_id", "title", "kind", "occurred_at", "excerpt"} <= set(out["notes"][0])
    assert any(e["entity_id"] == "ex:project-lut" for e in out["entities"])
    assert out["facets"]["topics"][0]["topic"] == "lut"
    assert out["facets"]["months"][0]["month"] == "2025-01"
    # attachment text is searchable
    assert note_search(ToolContext(nb=nb), "minutes review")["notes"][0]["note_id"] == h["n1"]
    # filters
    assert note_search(ToolContext(nb=nb), "Dana", since="2025-02-01")["notes"][0]["note_id"] == h["n2"]
    assert [n["note_id"] for n in note_search(ToolContext(nb=nb), "Dana", until="2025-02-01")["notes"]] == [h["n1"]]
    assert note_search(ToolContext(nb=nb), "Dana", topics=["lut"])["notes"][0]["note_id"] == h["n1"]
    assert {n["note_id"] for n in note_search(ToolContext(nb=nb), "", entities=["ex:org-beta"])["notes"]} == {h["n2"]}
    # as_of hides what did not exist yet, including entities and claims
    early = note_search(ToolContext(nb=nb), "Beta Dana", as_of="2025-02-01")
    assert all(n["occurred_at"] <= "2025-02-01T00:00:00Z" for n in early["notes"])
    assert all(e["entity_id"] != "ex:org-beta" for e in early["entities"])
    assert all(c["observed_at"] <= "2025-02-01T00:00:00Z" for c in early["claims"])
    # empty strings from flow pins are "not set"
    assert note_search(ToolContext(nb=nb), "Dana", since="", until="", entities=[], topics=[])["notes"]


def test_quick_search_over_5k_notes_is_fast(nb):
    for i in range(5000):
        nb.capture(f"Note {i} about project {i % 37} and the release train {i % 11}", importer="bench",
                   source_key=str(i), occurred_at=f"2025-{1 + i % 12:02d}-{1 + i % 28:02d}T10:00:00Z", priority=0)
    note_search(ToolContext(nb=nb), "release train 7")  # warm caches
    t0 = time.perf_counter()
    out = note_search(ToolContext(nb=nb), "project 12 release")
    elapsed = time.perf_counter() - t0
    assert out["notes"] and elapsed < 0.3, elapsed


def test_entity_card_as_of_a_moment(nb):
    history(nb)
    feb = note_entity(ToolContext(nb=nb), "ex:person-dana", at="2025-02-01")
    assert [c["object_id"] for c in feb["beliefs"] if c["predicate"] == "ex:works_at"] == ["ex:org-acme"]
    assert feb["summary"] == "Works at Acme." and feb["note_count"] == 1
    now = nb.entity("ex:person-dana")
    assert [c["object_id"] for c in now["beliefs"] if c["predicate"] == "ex:works_at"] == ["ex:org-beta"]
    assert now["summary"] == "Works at Beta since March." and len(now["summary_history"]) == 2
    assert now["open_tasks"] and now["open_tasks"][0]["text"] == "Send Dana the Pareto"
    assert {r["other_id"] for r in now["relations"]} == {"ex:org-acme", "ex:org-beta", "ex:project-lut"}
    assert [s["month"] for s in now["sparkline"]] == ["2025-01", "2025-03"]
    with pytest.raises(ValueError, match="not known yet"):
        nb.entity("ex:org-beta", at="2025-02-01")
    with pytest.raises(ValueError):
        nb.entity("ex:person-nobody")


def test_then_now_turning_points_and_cache(nb):
    h = history(nb)
    tn = nb.then_now("ex:person-dana")
    assert tn["earliest"]["summary"] == "Works at Acme." and tn["latest"]["summary"] == "Works at Beta since March."
    sup = [t for t in tn["turning_points"] if t["kind"] == "supersede"]
    assert sup and sup[0]["note_id"] == h["n2"] and sup[0]["claim_id"] == h["works"]
    assert nb.then_now("ex:person-dana") == tn  # cached
    nb.retract_claim(h["beta"], "user:1", "wrong company", occurred_at="2025-04-01T00:00:00Z")
    tn2 = nb.then_now("ex:person-dana")
    assert any(t["kind"] == "retract" for t in tn2["turning_points"])


def test_relation_timeline_and_claim_chain(nb):
    h = history(nb)
    rel = nb.relation("ex:person-dana", "ex:org-acme")
    assert [c["claim_id"] for c in rel["claims"]] == [h["works"]]
    assert rel["supersessions"][0]["superseded_by"] == h["beta"]
    assert rel["then_now"]["latest"] == [] and rel["then_now"]["turning_points"][0]["kind"] == "supersede"
    chain = nb.claim(h["beta"])
    assert [c["claim_id"] for c in chain["supersedes"]] == [h["works"]]
    old = nb.claim(h["works"])
    assert old["superseded_by"]["claim_id"] == h["beta"]
    assert old["notes"][0]["note_id"] == h["n1"]
    assert "Dana from Acme" in old["notes"][0]["attachments"][0]["excerpt"]


def test_expand_returns_cards_timelines_relations_neighbours_then_now(nb):
    h = history(nb)
    out = note_expand(ToolContext(nb=nb), note_ids=[h["n1"]], entity_ids=[], as_of="")
    ids_ = {c["entity_id"] for c in out["entities"]}
    assert ids_ == {"ex:person-dana", "ex:org-acme", "ex:project-lut"}
    assert out["relation_timelines"] and out["then_now"] and out["claims_timeline"]
    assert any(n["entity_id"] == "ex:org-beta" for n in out["neighbours"])
    early = note_expand(ToolContext(nb=nb), entity_ids=["ex:person-dana"], as_of="2025-02-01")
    tl = early["claims_timeline"][0]["claims"]
    assert all(c["observed_at"] <= "2025-02-01T00:00:00Z" for c in tl)


def test_reflect_candidates_carry_their_rule(nb):
    h = history(nb)
    loops = note_open_loops(ToolContext(nb=nb), limit=10)["items"]
    assert loops and loops[0]["rule"].startswith("open_task_older_than") and loops[0]["note_ids"] == [h["n1"]]
    q = nb.capture("Should we move the LUT review to Q2?", occurred_at="2025-01-06T10:00:00Z")
    nb.file_note(q.note_id, kind="question")
    rules = {i["rule"] for i in nb.open_loops(limit=20)["items"]}
    assert "question_without_followup" in rules
    # Acme went quiet but relates to Dana, who is active again "now"
    ext = extraction([mention("m1", "Dana", "person")])
    run(nb, "Lunch with Dana.", "2025-06-01T10:00:00Z", ext,
        lambda p, c: {"mentions": [ml("m1", "reuse", cand_index(c, "m1", "ex:person-dana"))], "claims": [],
                      "tasks": [], "revision_summary": "Lunch."})
    res = nb.resurface(limit=10, now="2025-06-03T00:00:00Z")["items"]
    acme = [i for i in res if i.get("entity_id") == "ex:org-acme"]
    assert acme and acme[0]["rule"] == "silent_but_related" and "ex:person-dana" in acme[0]["related_to"]
    ann = nb.anniversaries(now="2026-01-05T08:00:00Z")["items"]
    assert ann[0]["rule"] == "same_day_previous_year" and ann[0]["note_id"] == h["n1"]
    assert note_anniversaries(ToolContext(nb=nb), limit=5, since="")["items"] is not None
    assert note_resurface(ToolContext(nb=nb), limit=5, since="")["items"] is not None


def test_insights_cues_and_ack(nb):
    h = history(nb)
    ctx = ToolContext(nb=nb)
    out = note_insight_write(ctx, "reminder", "Pareto still not sent", "You promised Dana the Pareto in January.",
                             evidence_note_ids=[h["n1"], "nt_missing"], entity_ids=["ex:person-dana"])
    assert out["status"] == "new" and "nt_missing" in out["warnings"][0]
    assert note_insight_write(ctx, "reminder", "Pareto still not sent", "You promised Dana the Pareto in January.",
                              evidence_note_ids=[h["n1"]], entity_ids=["ex:person-dana"])["note_id"] == out["note_id"]
    [ins] = nb.insights()["items"]
    assert set(ins) == {"note_id", "title", "body", "insight_kind", "occurred_at", "acked_at", "evidence_note_ids",
                        "entity_ids"}
    cues = nb.cues()["items"]
    assert cues == [{"cue_id": out["note_id"], "text": "You promised Dana the Pareto in January.", "kind": "reminder",
                     "subject": {"kind": "entity", "id": "ex:person-dana", "title": "Dana"}}]
    dossier = note_insight_write(ctx, "dossier", "Dana", "A dossier.", [h["n1"]], [])
    assert nb.store.note_row(dossier["note_id"])["kind"] == "synthesis"
    assert all(c["cue_id"] != dossier["note_id"] for c in nb.cues(limit=10)["items"])
    nb.ack_insight(out["note_id"])
    assert nb.cues()["items"] == []
    with pytest.raises(ValueError):
        note_insight_write(ctx, "gossip", "x", "y")
    # replying to a cue links the new note to it
    reply = nb.capture("Sent it today.", about={"kind": "cue", "id": out["note_id"]})
    assert nb.note_links(reply.note_id)["outgoing"][0]["predicate"] == "dcterms:references"
    with pytest.raises(ValueError):
        nb.capture("x", about={"kind": "cue", "id": "nt_nope"})


def test_changes_lists_supersessions_and_retractions_in_plain_words(nb):
    """Review 02 M3: the delta reaches the watch screen verbatim, so its text carries names and the
    predicate's plain words, never ids or CURIEs; a retraction is its own kind."""
    import re

    first = nb.changes("")
    h = history(nb)
    participant = [c for c in nb.store.q("SELECT claim_id FROM claims WHERE predicate='schema:participant'")][0][0]
    nb.retract_claim(participant, "user:1", "Dana was never on LUT.")
    delta = nb.changes(first["cursor"])["superseded_claims"]
    by_id = {c["claim_id"]: c for c in delta}
    assert set(by_id) == {h["works"], participant}
    assert by_id[h["works"]] == {"claim_id": h["works"], "kind": "superseded", "text": "Dana works at Acme",
                                 "superseded_by": h["beta"], "note_id": h["n2"]}
    assert by_id[participant]["kind"] == "retracted" and by_id[participant]["superseded_by"] is None
    assert by_id[participant]["text"] == "Dana participant LUT"
    curie = re.compile(r"\b[a-z][a-z0-9]*:[A-Za-z]")
    assert not [c["text"] for c in delta if curie.search(c["text"])]


def test_changes_cursor_never_loses_or_repeats(nb):
    first = note_changes(ToolContext(nb=nb), since="", limit=50)
    assert set(first) == {"cursor", "notes", "entities", "superseded_claims", "closed_loops", "failed_notes",
                          "insights"}
    h = history(nb)
    delta = nb.changes(first["cursor"])
    assert {n["note_id"] for n in delta["notes"]} == {h["n1"], h["n2"]}
    assert {e["entity_id"] for e in delta["entities"]} >= {"ex:person-dana", "ex:org-beta"}
    assert [c["claim_id"] for c in delta["superseded_claims"]] == [h["works"]]
    assert delta["superseded_claims"][0]["note_id"] == h["n2"]
    assert nb.changes(delta["cursor"]) == {**nb.changes(delta["cursor"]), "notes": [], "entities": [],
                                           "superseded_claims": [], "closed_loops": [], "failed_notes": [],
                                           "insights": []}
    tid = nb.tasks()["items"][0]["task_id"]
    nb.close_task(tid, "user:1")
    f = nb.capture("will fail", occurred_at="2025-05-01T00:00:00Z")
    for _ in range(3):
        nb.queue.lease("w", now="2030-01-01T00:00:00Z")
        nb.queue.ack(f.note_id, "error", "model unavailable", now="2030-01-01T00:00:00Z")
    nb.write_insight("warning", "Watch the budget", "Two budgets disagree.", [h["n1"]], [])
    later = nb.changes(delta["cursor"])
    assert [c["task_id"] for c in later["closed_loops"]] == [tid]
    assert later["failed_notes"] == [{"note_id": f.note_id, "title": "", "error": "model unavailable"}]
    assert later["insights"][0]["title"] == "Watch the budget"
    # paging with a small limit visits every item exactly once
    seen, cur = [], first["cursor"]
    for _ in range(50):
        page = nb.changes(cur, limit=2)
        items = [x for k in ("notes", "entities", "superseded_claims", "closed_loops", "failed_notes", "insights")
                 for x in page[k]]
        if not items:
            break
        seen.extend(str(sorted(x.items())) for x in items)
        cur = page["cursor"]
    everything = nb.changes(first["cursor"], limit=500)
    total = sum(len(everything[k]) for k in everything if k != "cursor")
    assert len(seen) == total == len(set(seen))
    with pytest.raises(ValueError):
        nb.changes("garbage-cursor")


def test_pinned_shapes_of_note_tasks_overview(nb):
    h = history(nb)
    note = nb.note(h["n1"])
    assert set(note) == {"note_id", "title", "text", "kind", "occurred_at", "received_at", "status", "topics",
                         "summary", "source", "location", "attachments", "revision", "entities", "claims", "revisits"}
    assert note["revision"]["note_id"] == h["n1"] and note["revisits"] == []
    assert nb.note(nb.capture("unprocessed").note_id)["revision"] is None
    [task] = nb.tasks()["items"]
    assert set(task) == {"task_id", "note_id", "text", "status", "due", "closed_at"}
    ov = nb.overview(days=7, now="2025-03-03T00:00:00Z")
    assert set(ov) == {"recent", "entities", "insights", "resurfacing", "activity"}
    assert len(ov["activity"]) == 8 and set(ov["activity"][0]) == {"day", "notes", "revisions"}
    assert set(ov["entities"][0]) == {"entity_id", "name", "kind", "note_count", "last_seen"}
    assert set(ov["recent"][0]) == {"note_id", "title", "kind", "occurred_at", "excerpt"}
