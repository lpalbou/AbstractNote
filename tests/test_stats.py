"""GET /note/stats and Notebook.stats() (backlog 0030 item 3): what the notebook has accumulated, every number
counted by the store (no model)."""

from __future__ import annotations

import pytest

from test_store_support import claim, create, nb, rev, t  # noqa: F401

NOW = "2026-04-30T12:00:00Z"


def mention(eid: str, note_id: str):
    return {"op": "entity.mention", "payload": {"entity_id": eid, "note_id": note_id}}


def stats_world(nb):
    """Five notes over January to March 2026 (web, watch, an import), one attachment, entities and statements,
    a superseded statement and one whose window simply ended, tasks, an insight, Paris gone quiet since January
    and a kite mentioned once."""
    n1 = nb.capture("Met Dana about LUT in Paris.", occurred_at=t(5), source={"client": "web"},
                    attachments=[{"data": b"hello", "filename": "a.txt", "mime": "text/plain"}]).note_id
    n2 = nb.capture("Dana again.", occurred_at=t(7), source={"client": "watch"}).note_id
    n3 = nb.capture("Imported chat.", occurred_at=t(12), importer="claude-sessions", source_key="s1").note_id
    n4 = nb.capture("LUT budget moved.", occurred_at=t(2, 3), source={"client": "web"}).note_id
    n5 = nb.capture("Paris again.", occurred_at=t(6), source={"client": "web"}).note_id
    nb.store.conn.execute("UPDATE notes SET kind='event' WHERE note_id IN (?, ?)", (n1, n2))
    nb.store.conn.execute("UPDATE notes SET kind='decision' WHERE note_id=?", (n4,))
    nb.apply_revision(rev("rv_1", t(5), create("ex:person-dana", "Dana"), create("ex:project-lut", "LUT"),
                          create("ex:place-paris", "Paris"), create("ex:thing-kite", "Kite"), *[mention(e, n1) for e in
                          ("ex:person-dana", "ex:project-lut", "ex:place-paris", "ex:thing-kite")],
                          claim("cl_1", "ex:person-dana", "schema:participant", "ex:project-lut", at=t(5), note_id=n1),
                          claim("cl_2", "ex:project-lut", "ex:budget", literal="50k", at=t(5), note_id=n1),
                          {"op": "claim.add", "payload": {**claim("cl_5", "ex:thing-kite", "ex:lent_to", "ex:person-dana",
                                                                  at=t(5), note_id=n1)["payload"], "valid_until": t(9)}},
                          {"op": "task.open", "payload": {"task_id": "tk_1", "text": "Send the Pareto"}},
                          {"op": "task.open", "payload": {"task_id": "tk_2", "text": "Book the room"}},
                          note_id=n1))
    nb.apply_revision(rev("rv_2", t(7), mention("ex:person-dana", n2), mention("ex:place-paris", n5),
                          claim("cl_3", "ex:person-dana", "schema:participant", "ex:project-lut", at=t(7), note_id=n2),
                          {"op": "task.close", "payload": {"task_id": "tk_1"}}, note_id=n2))
    nb.apply_revision(rev("rv_3", t(2, 3), mention("ex:project-lut", n4), mention("ex:person-dana", n4),
                          claim("cl_4", "ex:project-lut", "ex:budget", literal="55k", at=t(2, 3), note_id=n4),
                          {"op": "claim.supersede", "payload": {"claim_id": "cl_2", "by_claim_id": "cl_4",
                                                                "reason": "moved", "valid_until": t(2, 3)}},
                          note_id=n4))
    nb.store.conn.execute("UPDATE notes SET status='processed' WHERE note_id IN (?, ?, ?)", (n1, n2, n4))
    nb.store.conn.execute("UPDATE notes SET status='failed' WHERE note_id=?", (n5,))
    nb.store.conn.execute("UPDATE notes SET status='queued' WHERE note_id=?", (n3,))
    nb.write_insight("reminder", "Pareto", "Still open.", [n1], ["ex:person-dana"])
    return {"n1": n1, "n2": n2, "n3": n3, "n4": n4, "n5": n5}


def test_stats_counts_what_the_notebook_holds(nb):
    stats_world(nb)
    s = nb.stats(now=NOW)
    tot = s["totals"]
    assert (tot["notes"], tot["insights"], tot["attachments"], tot["entities"], tot["claims"]) == (5, 1, 1, 4, 5)
    assert tot["notes_by_kind"] == {"event": 2, "decision": 1, "unfiled": 2}
    assert tot["notes_by_source"] == {"web": 3, "watch": 1, "importer:claude-sessions": 1}
    assert tot["attachments_by_mime"] == {"text/plain": 1} and tot["attachment_bytes"] == 5
    assert s["entities_by_kind"] == {"person": 1, "project": 1, "place": 1, "thing": 1}
    assert s["tasks"] == {"open": 1, "closed": 1, "done": 1, "dropped": 0}
    assert s["insights_by_kind"] == {"reminder": 1}
    assert s["processing"] == {"understood": 3, "queued": 1, "processing": 0, "failed": 1, "duplicate": 0}
    assert s["claims_by_predicate"] == [  # most used first, ties by predicate
        {"predicate": "ex:budget", "words": "budget", "count": 2, "active": 1},
        {"predicate": "schema:participant", "words": "participant", "count": 2, "active": 2},
        {"predicate": "ex:lent_to", "words": "lent to", "count": 1, "active": 1}]
    top = s["top_entities"]
    assert [e["entity_id"] for e in top] == ["ex:person-dana", "ex:project-lut", "ex:place-paris", "ex:thing-kite"]
    assert top[0] == {"entity_id": "ex:person-dana", "name": "dana", "display_name": "Dana", "kind": "person",
                      "note_count": 3, "first_seen": t(5), "last_seen": t(2, 3)}
    assert s["communities"] == 0
    assert s["span"] == {"first_note_at": t(5), "last_note_at": t(2, 3)}


def test_weekly_activity_covers_the_whole_span_with_empty_weeks(nb):
    stats_world(nb)
    weeks = nb.stats(now=NOW)["activity_weekly"]
    assert weeks[0]["week"] == "2026-01-05" and weeks[-1]["week"] == "2026-03-02"  # Mondays
    assert len(weeks) == 9 and [w["week"] for w in weeks] == sorted(w["week"] for w in weeks)
    by = {w["week"]: w for w in weeks}
    assert by["2026-01-05"] == {"week": "2026-01-05", "notes": 3, "revisions": 2, "new_entities": 4,
                                "claims_added": 4, "claims_superseded": 0}  # cl_5's window ended: not superseded
    assert by["2026-01-12"]["notes"] == 1 and by["2026-01-19"]["notes"] == 0  # an empty week is listed
    assert by["2026-03-02"] == {"week": "2026-03-02", "notes": 1, "revisions": 1, "new_entities": 0,
                                "claims_added": 1, "claims_superseded": 1}
    assert sum(w["notes"] for w in weeks) == 5


def test_monthly_growth_is_cumulative(nb):
    stats_world(nb)
    growth = nb.stats(now=NOW)["growth_monthly"]
    assert growth == [{"month": "2026-01", "entities": 4, "claims": 4, "new_entities": 4, "new_claims": 4},
                      {"month": "2026-02", "entities": 4, "claims": 4, "new_entities": 0, "new_claims": 0},
                      {"month": "2026-03", "entities": 4, "claims": 5, "new_entities": 0, "new_claims": 1}]


def test_quiet_entities_were_active_and_have_been_silent_for_sixty_days(nb):
    stats_world(nb)
    quiet = nb.stats(now=NOW)["quiet_entities"]
    # Paris: two notes, the last on Jan 6; Dana and LUT were mentioned on Mar 2 (59 days before NOW); the kite
    # was mentioned once (never active): not quiet
    assert quiet == [{"entity_id": "ex:place-paris", "name": "paris", "kind": "place", "note_count": 2,
                      "last_seen": t(6), "days_silent": 114}]
    assert nb.stats(now="2026-05-02T12:00:00Z")["quiet_entities"][0]["entity_id"] in {"ex:person-dana", "ex:place-paris"}
    assert len(nb.stats(now="2026-05-02T12:00:00Z")["quiet_entities"]) == 3


def test_stats_count_communities_and_read_only_notebooks_answer(tmp_path):
    from test_communities import world
    from test_store_support import CountingEmbedder

    from abstractnote.store.notebook import Notebook

    path = tmp_path / "pinned"
    with Notebook.open(path, embedder=CountingEmbedder(model_id="pinned-model")) as nb1:
        world(nb1)
        nb1.recompute_communities()
        assert nb1.stats()["communities"] == 2
    with Notebook.open(path) as ro:
        assert ro.read_only and ro.stats()["communities"] == 2


def test_an_empty_notebook_has_empty_stats(nb):
    s = nb.stats(now=NOW)
    assert s["activity_weekly"] == [] and s["growth_monthly"] == [] and s["top_entities"] == []
    assert s["span"] == {"first_note_at": None, "last_note_at": None} and s["totals"]["notes"] == 0
    with pytest.raises(ValueError):
        nb.stats(now="not a date")
