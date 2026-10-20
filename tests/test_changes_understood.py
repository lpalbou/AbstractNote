"""``changes(since)`` shows notes understood after an import (eval2 D16): a note whose latest processing revision
was recorded at or after ``since`` is listed under ``notes`` with ``how: "understood"``; a note only received then
has ``how: "captured"``. Each note appears once per call, at its latest event."""

from __future__ import annotations

from test_store_support import create, nb, rev  # noqa: F401

LATER = "2030-01-01T00:00:00Z"


def _understand(nb, note_id, rid, recorded_at):
    r = rev(rid, "2026-01-02T09:00:00Z", create(f"ex:thing-{rid}", f"Thing {rid}"), note_id=note_id)
    r["recorded_at"] = recorded_at
    nb.apply_revision(r)


def test_a_note_understood_after_since_is_listed_with_how(nb):
    a = nb.capture("imported long ago", occurred_at="2025-01-01T00:00:00Z").note_id
    b = nb.capture("imported, still waiting", occurred_at="2025-01-02T00:00:00Z").note_id
    before = nb.changes()
    assert {(n["note_id"], n["how"]) for n in before["notes"]} == {(a, "captured"), (b, "captured")}
    _understand(nb, a, "rv_a1", LATER)
    later = nb.changes("2029-12-31T00:00:00Z")
    assert [(n["note_id"], n["how"]) for n in later["notes"]] == [(a, "understood")]
    # from the start: each note once, at its latest event
    everything = nb.changes()
    assert [(n["note_id"], n["how"]) for n in everything["notes"]] == [(b, "captured"), (a, "understood")]
    # the cursor of the earlier call sees the understanding as new, and then nothing more
    after = nb.changes(before["cursor"])
    assert [(n["note_id"], n["how"]) for n in after["notes"]] == [(a, "understood")]
    assert nb.changes(after["cursor"])["notes"] == []


def test_a_user_action_is_not_understanding(nb):
    a = nb.capture("a note", occurred_at="2025-01-01T00:00:00Z").note_id
    r = rev("rv_user", "2026-01-02T09:00:00Z", create("ex:thing-u", "U"))  # no note: a user/system revision
    r["recorded_at"] = LATER
    nb.apply_revision(r)
    assert nb.changes("2029-12-31T00:00:00Z")["notes"] == []
    assert [n["how"] for n in nb.changes()["notes"]] == ["captured"] and nb.changes()["notes"][0]["note_id"] == a
