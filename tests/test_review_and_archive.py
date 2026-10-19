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
