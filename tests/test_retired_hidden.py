"""W27 item 1 (ADR 0008 ``entity.retire``): an entry a re-understanding retired is hidden from communities, stats,
search, the graph, the timeline, the day view, the overview, the changes feed and cues; its own page says what
retired it and what the note now reads its words as; the archive as received before the retirement still shows it.
Scripted answers only (the retirement fixture of test_reinterpret). The mutation list is in untracked/reports/W27.md.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from abstractnote.recall import communities as cm
from abstractnote.recall.changes import cues
from abstractnote.store.notebook import Notebook
from test_reinterpret import D05, D20, Clock, extraction, link, mention, run


@pytest.fixture
def nb(tmp_path):
    notebook = Notebook.open(tmp_path / "nb")
    yield notebook
    notebook.close()


def retire(nb: Notebook) -> Dict[str, Any]:
    """Note A (day 20) makes "the calibration plan"; note B (day 5) makes "Helix calibration plan" with that alias;
    A understood again reuses Helix for its words: the first entry is retired. ``before`` is a time the archive
    held both notes, before the retirement was recorded."""
    clock = Clock()
    ext20 = extraction([mention("m1", "the calibration plan", "artifact")])
    a = run(nb, "Reviewed the calibration plan.", D20, ext20,
            link({"m1": {"name": "the calibration plan", "kind": "artifact"}}), now=clock.next())
    dup = a["out"]["entities_created"][0]
    b = run(nb, "The Helix calibration plan, also the calibration plan.", D05,
            extraction([mention("m1", "Helix calibration plan", "artifact", aliases=["the calibration plan"])]),
            link({"m1": {"name": "Helix calibration plan", "kind": "artifact", "aliases": ["the calibration plan"]}}),
            now=clock.next())
    hcp = b["out"]["entities_created"][0]
    before = clock.next()  # notes are received on the clock; the re-understanding is recorded later (system time)
    out = run(nb, "", D20, ext20, link({"m1": hcp}), reinterpret=True, note_id=a["note_id"], now=clock.next())["out"]
    assert out["entities_retired"] == [dup] and nb.store.entity_row(dup)["retired_revision_id"]
    return {"dup": dup, "hcp": hcp, "note_a": a["note_id"], "note_b": b["note_id"], "before": before}


def test_a_retired_entry_is_in_no_community_graph_and_stats_do_not_count_it(nb):
    """MUTATION: drop the retired test from communities._canonical_map (the entry is a node of the community
    graph) / put stats back on ``merged_into IS NULL`` (totals.entities, entities_by_kind and new_entities count it).
    """
    r = retire(nb)
    g = cm.entity_graph(nb)
    assert r["dup"] not in g.nodes and r["hcp"] in g.nodes
    st = nb.stats()
    assert st["totals"]["entities"] == 1 and st["totals"]["entities_retired"] == 1
    assert st["entities_by_kind"] == {"artifact": 1}
    assert sum(w["new_entities"] for w in st["activity_weekly"]) == 1
    assert st["growth_monthly"][-1]["entities"] == 1


def test_a_retired_entry_is_left_out_of_search_graph_timeline_day_overview_and_changes(nb):
    """MUTATION: drop the retired filter of store.notes.timeline (entities_created counts it) / of the day view
    (``entities_created`` lists it) / of changes.overview / of changes.since."""
    r = retire(nb)
    found = nb.search("calibration plan")
    assert r["dup"] not in {e["entity_id"] for e in found["entities"]}
    assert r["dup"] not in {n["id"] for n in nb.graph()["nodes"]}
    assert sum(b["entities_created"] for b in nb.timeline()["buckets"]) == 1
    day = nb.day(D20[:10])
    assert r["dup"] not in day["entities_created"]
    ov = nb.overview(days=366, now="2026-03-26T00:00:00Z")
    assert r["dup"] not in {e["entity_id"] for e in ov["entities"]}
    ch = nb.changes(since="2020-01-01T00:00:00Z")
    assert r["dup"] not in {e["entity_id"] for e in ch["entities"]}


def test_as_received_before_the_retirement_still_shows_it(nb):
    """The archive as it stood before the retirement was recorded holds the entry (day view and changes too).
    MUTATION: filter the day view or changes on ``retired_revision_id`` alone (no archive check)."""
    r = retire(nb)
    day = nb.day(D20[:10], as_received=r["before"])
    assert r["dup"] in day["entities_created"]
    ch = nb.changes(since="2020-01-01T00:00:00Z", as_received=r["before"])
    assert r["dup"] in {e["entity_id"] for e in ch["entities"]}
    card = nb.entity(r["dup"], as_received=r["before"])
    assert card["entity_id"] == r["dup"]


def test_a_cue_about_a_retired_entry_points_at_its_evidence_note(nb):
    """A reflect insight that names a retired entry links its first evidence note, not the retired entry.
    MUTATION: drop the retired test in changes.cues."""
    r = retire(nb)
    nb.write_insight("reminder", "The calibration plan is quiet", "Nothing since March.",
                     evidence_note_ids=[r["note_a"]], entity_ids=[r["dup"]])
    [item] = cues(nb)["items"]
    assert item["subject"]["kind"] == "note" and item["subject"]["id"] == r["note_a"]


def test_the_retired_entrys_page_says_what_retired_it_and_what_the_note_now_reads(nb):
    """``retired`` on the card: the revision, the note, and ``into`` = the entry the new reading uses for the same
    words. MUTATION: drop ``read_as`` from the entity.retire op (into is empty, the sentence says nothing refers to
    it) / drop ``retired`` from the card."""
    r = retire(nb)
    card = nb.entity(r["dup"])
    ret = card["retired"]
    assert ret["note_id"] == r["note_a"] and ret["revision_id"] == nb.store.entity_row(r["dup"])["retired_revision_id"]
    assert [i["entity_id"] for i in ret["into"]] == [r["hcp"]]
    assert ret["sentence"].startswith("Retired on ") and ret["sentence"].endswith(
        "was understood again and now reads it as Helix calibration plan.")
    assert nb.entity(r["hcp"])["retired"] is None


def test_a_retired_entry_gets_no_summary_refresh(nb):
    """The consolidation refreshes live entries only: a retired one is never offered to the model.
    MUTATION: put consolidation._stale back on ``merged_into IS NULL``."""
    from abstractnote.recall.consolidation import _stale

    r = retire(nb)
    with nb.store.tx() as c:  # its summary is a placeholder: the first kind of entry a refresh would take
        c.execute("UPDATE entities SET summary=NULL WHERE entity_id=?", (r["dup"],))
    assert r["dup"] not in {x["entity_id"] for x in _stale(nb)}
