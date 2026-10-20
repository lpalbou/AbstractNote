"""Failure causes survive (eval2 D15): every failed attempt is logged in ``queue_errors``, never cleared by a
later success, a retry or a rebuild; ``GET /note/queue`` failed items carry the last three."""

from __future__ import annotations

from abstractnote.store.queue import STOPPED_ERROR, _plus, LEASE_S
from test_store_support import nb  # noqa: F401

NOW = "2026-10-19T12:00:00Z"


def _fail(nb, note_id, error, now):
    nb.queue.lease("w", 1, now=now)
    return nb.queue.ack(note_id, "error", error, now=now)


def test_every_error_is_logged_and_failed_items_show_the_last_three(nb):
    r = nb.capture("hello", occurred_at=NOW)
    t1, t2, t3 = NOW, _plus(NOW, 61), _plus(NOW, 61 + 601)
    _fail(nb, r.note_id, "first cause", t1)
    _fail(nb, r.note_id, "second cause", t2)
    assert _fail(nb, r.note_id, "third cause", t3)["status"] == "failed"
    rows = nb.queue.errors(r.note_id)
    assert [(e["attempt"], e["error"]) for e in rows] == [(3, "third cause"), (2, "second cause"), (1, "first cause")]
    [failed] = nb.queue.summary()["failed"]
    assert [e["error"] for e in failed["errors"]] == ["third cause", "second cause", "first cause"]
    assert failed["errors"][0]["at"] == t3
    # a retry and a later success keep the log
    nb.queue.retry(r.note_id, now=t3)
    _fail(nb, r.note_id, "fourth cause", _plus(t3, 1))
    nb.queue.lease("w", 1, now=_plus(t3, 10_000))
    nb.queue.ack(r.note_id, "ok", now=_plus(t3, 10_000))
    assert len(nb.queue.errors(r.note_id, limit=10)) == 4
    nb.queue.lease("w", 1, now=NOW)  # nothing to lease; the log is untouched
    assert nb.queue.errors(r.note_id)[0]["error"] == "fourth cause"


def test_an_expired_lease_is_logged_too(nb):
    r = nb.capture("hello", occurred_at=NOW)
    nb.queue.lease("w", 1, now=NOW)
    nb.queue.lease("w2", 1, now=_plus(NOW, LEASE_S + 1))
    [e] = nb.queue.errors(r.note_id)
    assert e["error"] == STOPPED_ERROR and e["attempt"] == 1


def test_queue_errors_survive_a_rebuild(nb):
    r = nb.capture("hello", occurred_at=NOW)
    _fail(nb, r.note_id, "lost model", NOW)
    nb.rebuild()
    assert [e["error"] for e in nb.queue.errors(r.note_id)] == ["lost model"]
