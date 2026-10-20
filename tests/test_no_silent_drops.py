"""Nothing is dropped silently outside the consolidation either (ADR 0007, root ADR-0026): a selection under a
bound says what it left out, a clamp of a caller's request is answered and logged, a lossy preview is marked."""

from __future__ import annotations

import json
import logging

from test_communities import world
from test_store_support import nb, rev  # noqa: F401

from abstractnote.recall import consolidation as cons
from abstractnote.recall import reflect, search, stats
from abstractnote.store.notes import TOPICS_PER_NOTE, clamp_limit, note_vector_text

AT = "2026-01-02T09:00:00Z"


def _assign(nb_, rid, note_id, *topics):
    return nb_.apply_revision(rev(rid, AT, *({"op": "topic.assign", "payload": {"topic": t}} for t in topics),
                                  note_id=note_id))


def test_topics_past_the_per_note_bound_are_named_in_the_revision(nb, caplog):  # noqa: F811
    n = nb.capture("about the gateway", occurred_at=AT).note_id
    with caplog.at_level(logging.WARNING, logger="abstractnote.limits"):
        _assign(nb, "rv_1", n, "Bug-Fix", "testing strategy", "Gateway", "fourth", "fifth")
    assert TOPICS_PER_NOTE == 3 and len(nb.store.note_topics(n)) == 3
    row = nb.store.revision_row("rv_1")
    assert json.loads(row["result_json"])["topics_not_assigned"] == ["fourth", "fifth"]
    warning = "topics_not_assigned: a note keeps the first 3 topics from processing; not assigned: fourth, fifth."
    assert json.loads(row["warnings_json"]) == [warning]
    assert row["summary"].endswith("Topics not kept (a note keeps 3): fourth, fifth.")
    assert any("rv_1" in r.getMessage() and "fourth, fifth" in r.getMessage() for r in caplog.records)
    nb.rebuild()  # replaying the same ops does not say it twice
    row = nb.store.revision_row("rv_1")
    assert json.loads(row["warnings_json"]) == [warning] and row["summary"].count("Topics not kept") == 1


def test_filing_topics_past_the_bound_are_answered_and_logged(nb, caplog):  # noqa: F811
    r = nb.capture("many topics", occurred_at=AT)
    with caplog.at_level(logging.WARNING, logger="abstractnote.capture"):
        out = nb.file_note(r.note_id, topics=["a", "b", "c", "d", "e", "f"])
    assert out["topics_not_assigned"] == ["e", "f"] and nb.note(r.note_id)["topics"] == ["a", "b", "c", "d"]
    assert any("not assigned: e, f" in rec.getMessage() for rec in caplog.records)


def test_a_clamped_limit_is_logged_with_its_caller_and_answered(nb, caplog):  # noqa: F811
    with caplog.at_level(logging.WARNING, logger="abstractnote.limits"):
        assert clamp_limit(5000, 50, 500) == 500
        assert clamp_limit(20, 50, 500) == 20
    msgs = [r.getMessage() for r in caplog.records if r.name == "abstractnote.limits"]
    assert len(msgs) == 1 and "the limit 5000 is outside 1..500; 500 was applied" in msgs[0]
    assert "test_no_silent_drops.py:test_a_clamped_limit" in msgs[0]
    assert "The limit 900 is outside 1 to 200; 200 was applied." in nb.search("x", limit=900)["warnings"]
    nb.capture("one", occurred_at=AT)
    lease = nb.queue.lease("w", 500)
    assert lease["limit_applied"] == 100 and len(lease["items"]) == 1


def test_the_note_vector_bound_is_logged(caplog):
    with caplog.at_level(logging.WARNING, logger="abstractnote.limits"):
        assert note_vector_text("t", "x" * 50, [], 20) == ("t\n" + "x" * 50)[:20]
        assert note_vector_text("t", "short", [], 20) == "t\nshort"
    msgs = [r.getMessage() for r in caplog.records]
    assert len(msgs) == 1 and "the text has 52 characters; the vector reads the first 20" in msgs[0]


def test_selections_count_what_they_leave_out(nb, monkeypatch):  # noqa: F811
    world(nb)
    entities = [r[0] for r in nb.store.q("SELECT entity_id FROM entities WHERE merged_into IS NULL ORDER BY 1")]
    # the graph: nodes past the limit and their edges
    g = nb.graph(limit=2)
    assert len(g["nodes"]) == 2 and g["omitted"]["nodes"] == len(entities) - 2 and g["omitted"]["edges"] > 0
    # the recall packet: focus entities past MAX_FOCUS_ENTITIES
    out = nb.expand([], entities[:7])
    assert out["omitted"]["entities"] >= 2 and set(out["omitted"]) == {
        "notes", "entities", "claims", "turning_points", "relation_timelines", "neighbours", "then_now"}
    # search facets and stats top lists
    all_facets = len(nb.search("")["facets"]["entities"])
    assert all_facets >= 2
    monkeypatch.setattr(search, "FACET_ENTITIES", 1)
    hits = nb.search("")
    assert len(hits["facets"]["entities"]) == 1 and hits["omitted"]["facet_entities"] == all_facets - 1
    monkeypatch.setattr(stats, "TOP_ENTITIES", 1)
    s = nb.stats()
    assert len(s["top_entities"]) == 1 and s["omitted"]["top_entities"] >= 1
    # the summary packets of the consolidation
    monkeypatch.setattr(cons, "PACKET_CLAIMS", 1)
    packets = cons.stale_summaries(nb, 50)["items"]
    dana = next(p for p in packets if p["entity_id"] == "ex:person-dana")
    assert len(dana["claims"]) == 1 and dana["omitted"]["claims"] >= 1


def test_reflect_candidate_lists_count_what_they_do_not_list(nb):  # noqa: F811
    for i, text in enumerate(("Should we buy a boat?", "Is the garden ready?", "Who owns the budget?")):
        n = nb.capture(text, occurred_at=f"2026-01-0{i + 1}T09:00:00Z").note_id
        nb.file_note(n, kind="question")
    every = reflect.open_loops(nb, 200, now="2026-03-01T00:00:00Z")
    assert len(every["items"]) >= 2
    one = reflect.open_loops(nb, 1, now="2026-03-01T00:00:00Z")
    assert len(one["items"]) == min(1, len(every["items"]))
    assert one["omitted"] == max(0, len(every["items"]) - 1) and every["omitted"] == 0
