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


def test_quick_search_entity_and_statement_hits_need_the_floor_and_a_returned_note(nb):
    """Eval REPORT 4.3 item 4: entity and claim hits had no relevance floor (irrelevant in all 15 questions)."""
    from abstractnote.recall.search import HIT_MIN_SCORE

    h = history(nb)
    out = note_search(ToolContext(nb=nb), "Acme")
    by_id = {n["note_id"]: n for n in out["notes"]}
    assert set(by_id) == {h["n1"], h["n2"]}
    # Acme is named and carried by n1; Dana and LUT are carried by n1 too but do not match the words
    assert [e["entity_id"] for e in out["entities"]] == ["ex:org-acme"]
    acme = out["entities"][0]
    assert acme["note_ids"] == [h["n1"]] and acme["score"] >= HIT_MIN_SCORE
    assert acme["first_at"] == acme["latest_at"] == "2025-01-05T10:00:00Z"
    assert by_id[h["n1"]]["matched_entities"] == ["ex:org-acme"] and by_id[h["n2"]]["matched_entities"] == []
    assert all(0 <= n["relevance"] <= 1 for n in out["notes"])
    # a statement hit is grouped under the note that made it
    beta = note_search(ToolContext(nb=nb), "Beta")
    assert [c["claim_id"] for c in beta["claims"]] == [h["beta"]]
    assert beta["claims"][0]["note_ids"] == [h["n2"]]
    assert {n["note_id"]: n["matched_claims"] for n in beta["notes"]}[h["n2"]] == [h["beta"]]
    # first and latest date over the matching notes that carry the entity
    dana = {e["entity_id"]: e for e in note_search(ToolContext(nb=nb), "Dana")["entities"]}["ex:person-dana"]
    assert (dana["first_at"], dana["latest_at"]) == ("2025-01-05T10:00:00Z", "2025-03-01T10:00:00Z")
    # an entity none of the returned notes carries is not a hit (Beta only appears after `until`)
    early = note_search(ToolContext(nb=nb), "Beta Dana", until="2025-02-01")
    assert [n["note_id"] for n in early["notes"]] == [h["n1"]]
    assert "ex:org-beta" not in {e["entity_id"] for e in early["entities"]}
    assert all(set(c["note_ids"]) <= {h["n1"]} for c in early["claims"])


def test_quick_search_earliest_first(nb, capsys):
    """Eval REPORT section 2: "when did we first mention X" had no answer."""
    h = history(nb)
    may = nb.capture("Kiwi launch planned with the board.", occurred_at="2025-05-01T10:00:00Z").note_id
    april = nb.capture("Kiwi prototype shown to Dana.", occurred_at="2025-04-01T10:00:00Z").note_id
    nb.capture("Quarterly numbers.", occurred_at="2024-12-01T10:00:00Z")
    out = note_search(ToolContext(nb=nb), "Kiwi", order="earliest")
    assert out["order"] == "earliest" and [n["note_id"] for n in out["notes"]] == [april, may]
    assert [n["note_id"] for n in note_search(ToolContext(nb=nb), "Kiwi launch", order="earliest")["notes"]] == [may]
    assert note_search(ToolContext(nb=nb), "Kiwi", order="")["order"] == "relevance"
    assert [n["note_id"] for n in nb.search("Dana", order="earliest")["notes"]] == [h["n1"], h["n2"], april]
    listed = [n["occurred_at"] for n in nb.search("", order="earliest")["notes"]]
    assert listed == sorted(listed) and listed[0].startswith("2024-12-01")
    with pytest.raises(ValueError):
        nb.search("Kiwi", order="newest")
    from abstractnote.cli import main

    root = str(nb.root)
    nb.close()
    assert main(["search", "Kiwi", "--order", "earliest", "--notebook", root]) == 0
    import json

    assert [n["note_id"] for n in json.loads(capsys.readouterr().out)["notes"]] == [april, may]


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
    out = note_expand(ToolContext(nb=nb), note_ids=[h["n1"]], entity_ids=["ex:person-dana", "ex:org-acme"], as_of="")
    assert [e["entity_id"] for e in out["entities"][:2]] == ["ex:person-dana", "ex:org-acme"]
    assert {e["entity_id"] for e in out["entities"]} == {"ex:person-dana", "ex:org-acme", "ex:project-lut"}
    dana = out["entities"][0]
    assert dana["focus"] is True and dana["line"].startswith("dana (person): Works at Beta since March.")
    assert {t["entity_id"] for t in out["claims_timeline"]} == {"ex:person-dana", "ex:org-acme"}  # focus only
    works = [c for c in out["claims_timeline"][0]["claims"] if c["claim_id"] == h["works"]][0]
    assert works["text"] == "dana works at acme" and works["closed"]["kind"] == "supersede"
    assert out["relation_timelines"] and out["then_now"][0]["entity_id"] == "ex:person-dana"
    assert any(n["entity_id"] == "ex:org-beta" for n in out["neighbours"])
    assert [n["note_id"] for n in out["notes"]] == [h["n1"]]
    early = note_expand(ToolContext(nb=nb), entity_ids=["ex:person-dana"], as_of="2025-02-01")
    tl = early["claims_timeline"][0]["claims"]
    assert tl and all(c["at"] <= "2025-02-01" for c in tl)
    assert note_expand(ToolContext(nb=nb), note_ids=[h["n1"]])["claims_timeline"] == []  # no focus, no timelines


def test_expand_selects_within_its_budget(nb):
    """Eval REPORT 4.3 item 7: the packet was a dump (80-167k characters); it is now chosen within a budget."""
    import json

    from abstractnote.recall.expand import EXCERPT_CHARS, EXPAND_BUDGET_CHARS

    h = history(nb)
    filler = " ".join(f"Line {i} of the weekly minutes about logistics and rooms." for i in range(400))
    long_id = nb.capture(filler + " The Pareto deadline moved to Friday. " + filler,
                         occurred_at="2025-02-01T10:00:00Z").note_id
    many = [nb.capture(f"Status {i}: " + filler, occurred_at=f"2025-02-{2 + i % 20:02d}T10:00:00Z").note_id
            for i in range(60)]
    out = nb.expand([long_id, *many, h["n1"]], ["ex:person-dana"], query="Pareto deadline")
    size = len(json.dumps(out))
    assert size <= EXPAND_BUDGET_CHARS and out["used_chars"] == size
    first = out["notes"][0]
    assert first["note_id"] == long_id and "Pareto deadline moved to Friday" in first["excerpt"]
    assert len(first["excerpt"]) <= EXCERPT_CHARS
    assert out["omitted"]["notes"] > 0 and 0 < len(out["notes"]) < 62
    assert out["claims_timeline"][0]["entity_id"] == "ex:person-dana"  # the graph part still got its share
    small = nb.expand([long_id, *many], ["ex:person-dana"], query="Pareto", budget_chars=5000)
    assert len(json.dumps(small)) <= 5000 and small["entities"][0]["entity_id"] == "ex:person-dana"
    # without a query the excerpt centres on the focus entity's name
    named = nb.expand([h["n2"]], ["ex:org-beta"])
    assert "Beta" in named["notes"][0]["excerpt"]


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


def test_contradictions_are_disputed_pairs_never_several_objects(nb):
    """Several current objects of one predicate (mentions, parts, a person on two projects) are ordinary;
    only a note's explicit contradiction (disputed_with) is a candidate (eval REPORT section 2)."""
    from test_store_support import claim, create, rev, t

    nb.apply_revision(rev("rv_a", t(1), create("ex:person-dana", "Dana"), create("ex:project-lut", "LUT"),
                          create("ex:project-kiwi", "Kiwi"),
                          claim("cl_a", "ex:person-dana", "schema:participant", "ex:project-lut", at=t(1)),
                          claim("cl_b", "ex:person-dana", "schema:participant", "ex:project-kiwi", at=t(1)),
                          claim("cl_c", "ex:project-lut", "ex:budget", literal="25k", at=t(1))))
    assert nb.contradictions()["items"] == []
    nb.apply_revision(rev("rv_b", t(2), claim("cl_d", "ex:project-lut", "ex:budget", literal="55k", at=t(2),
                                              certainty="disputed", disputes="cl_c")))
    [item] = nb.contradictions()["items"]
    assert item["rule"] == "disputed_pair" and {c["claim_id"] for c in item["claims"]} == {"cl_c", "cl_d"}


def test_open_loops_and_tasks_carry_age_and_stale(nb):
    from abstractnote.recall.reflect import STALE_DAYS

    h = history(nb)  # the task opened 2025-01-05
    [young] = nb.tasks(now="2025-01-20T10:00:00Z")["items"]
    assert young["age_days"] == 15 and young["stale"] is False
    [old] = nb.tasks(now="2025-06-01T10:00:00Z")["items"]
    assert old["age_days"] == 147 >= STALE_DAYS and old["stale"] is True
    loops = note_open_loops(ToolContext(nb=nb), limit=10)["items"]
    assert loops[0]["stale"] is True and loops[0]["age_days"] >= STALE_DAYS
    [fresh] = [i for i in nb.open_loops(now="2025-01-20T10:00:00Z")["items"] if i["kind"] == "task"]
    assert fresh["stale"] is False and fresh["age_days"] == 15
    nb.close_task(old["task_id"], "me", occurred_at="2025-07-01T10:00:00Z")
    [done] = nb.tasks(status="done", now="2026-01-01T00:00:00Z")["items"]
    assert done["age_days"] == 177 and done["stale"] is False
    assert h["n1"] == done["note_id"]


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
                     "subject": {"kind": "entity", "id": "ex:person-dana", "title": "dana"},
                     "evidence_note_ids": [h["n1"]], "entity_ids": ["ex:person-dana"]}]
    # the insight note's own read says what it rests on (W9 gap 5)
    assert nb.note(out["note_id"])["insight"] == {"kind": "reminder", "evidence_note_ids": [h["n1"]],
                                                  "entity_ids": ["ex:person-dana"], "acked_at": None}
    dossier = note_insight_write(ctx, "dossier", "Dana", "A dossier.", [h["n1"]], [])
    assert nb.store.note_row(dossier["note_id"])["kind"] == "synthesis"
    assert all(c["cue_id"] != dossier["note_id"] for c in nb.cues(limit=10)["items"])
    nb.ack_insight(out["note_id"])
    assert nb.cues()["items"] == []
    assert nb.note(out["note_id"])["insight"]["acked_at"]
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
    assert by_id[h["works"]] == {"claim_id": h["works"], "kind": "superseded", "text": "dana works at acme",
                                 "superseded_by": h["beta"], "note_id": h["n2"]}
    assert by_id[participant]["kind"] == "retracted" and by_id[participant]["superseded_by"] is None
    assert by_id[participant]["text"] == "dana participant lut"
    curie = re.compile(r"\b[a-z][a-z0-9]*:[A-Za-z]")
    assert not [c["text"] for c in delta if curie.search(c["text"])]


def test_a_user_retraction_means_never_true(nb):
    """Review 02 m3: the window closes where it opens, never at the (earlier) system time; beliefs
    and the AbstractMemory projection agree that it was never active."""
    history(nb)
    cid = nb.store.q("SELECT claim_id FROM claims WHERE predicate='schema:participant'")[0][0]
    assert cid in nb.kg.active_claim_ids("2025-02-01T00:00:00Z")
    nb.retract_claim(cid, "user:1", "Dana was never on LUT.")
    row = nb.store.claim_row(cid)
    assert row["status"] == "retracted" and row["valid_until"] == row["valid_from"] == "2025-01-05T10:00:00Z"
    assert cid not in {c["claim_id"] for c in nb.beliefs_at("2025-02-01T00:00:00Z")}
    assert cid not in nb.kg.active_claim_ids("2025-02-01T00:00:00Z")
    [a] = nb.kg.query(assertion_ids=(cid,))
    assert a.valid_until == a.valid_from


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
    assert set(note) == {"note_id", "title", "text", "kind", "occurred_at", "date_basis", "date_approximate",
                         "received_at", "status", "topics",
                         "summary", "source", "location", "attachments", "revision", "entities", "claims", "revisits",
                         "insight", "interpretations", "reinterpret_pending", "part_of", "children"}  # W30
    assert note["revision"]["note_id"] == h["n1"] and note["revisits"] == [] and note["insight"] is None
    # ADR 0008: the interpretation history (one understanding so far) and why it waits to be read again (it does not)
    assert [i["action"] for i in note["interpretations"]] == [None] and note["reinterpret_pending"] == []
    assert set(note["interpretations"][0]) == {"revision_id", "at", "recorded_at", "action", "summary", "context",
                                               "context_unknown", "superseded_by", "changes"}
    assert nb.note(nb.capture("unprocessed").note_id)["revision"] is None
    [task] = nb.tasks()["items"]
    assert set(task) == {"task_id", "note_id", "text", "status", "due", "closed_at", "age_days", "stale"}
    ov = nb.overview(days=7, now="2025-03-03T00:00:00Z")
    assert set(ov) == {"recent", "entities", "insights", "resurfacing", "activity"}
    assert len(ov["activity"]) == 8 and set(ov["activity"][0]) == {"day", "notes", "revisions"}
    assert set(ov["entities"][0]) == {"entity_id", "name", "display_name", "kind", "note_count", "last_seen"}
    assert set(ov["recent"][0]) == {"note_id", "title", "kind", "occurred_at", "excerpt"}
