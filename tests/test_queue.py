"""Queue: ordering, leases, expiry, retries with backoff, failure, retry, and two concurrent leasers."""

from __future__ import annotations

import threading

import pytest

from abstractnote.store.notebook import Notebook
from abstractnote.store.queue import LEASE_S, RETRY_BACKOFF_S, STOPPED_ERROR, _plus
from abstractnote.tools.context import ToolContext
from abstractnote.tools.queue import note_queue_ack, note_queue_lease, note_queue_stats
from test_store_support import nb  # noqa: F401

NOW = "2026-10-19T12:00:00Z"


def _drain_order(nb, worker="w"):
    order = []
    while True:
        items = nb.queue.lease(worker, 7, now=NOW)["items"]
        if not items:
            return order
        for it in items:
            order.append(it["note_id"])
            nb.queue.ack(it["note_id"], "ok", now=NOW)


def test_100_mixed_notes_drain_in_priority_then_time_then_size_order(nb):
    expected = []
    for i in range(100):
        prio = [3, 2, 1, 0][i % 4]
        occurred = f"2026-0{1 + (i % 9)}-{10 + (i % 17):02d}T00:00:00Z"
        r = nb.capture("x" * (1 + (i * 37) % 50) + f" note {i}", occurred_at=occurred, priority=prio)
        size = nb.store.note_row(r.note_id)["size"]
        expected.append((-prio, occurred, size, r.note_id))
    expected.sort()
    assert _drain_order(nb) == [e[3] for e in expected]
    stats = nb.queue.stats()
    assert stats["queued"] == 0 and stats["processing"] == 0 and stats["done"] == 100


def test_lease_ack_and_note_status(nb):
    r = nb.capture("hello", occurred_at=NOW)
    ctx = ToolContext(nb=nb)
    assert note_queue_stats(ctx)["queued"] == 1
    [item] = note_queue_lease(ctx, "drain-1", 5)["items"]
    assert item == {"note_id": r.note_id, "priority": 1, "attempts": 1}
    assert nb.store.note_row(r.note_id)["status"] == "processing"
    assert note_queue_stats(ctx)["processing"] == 1
    assert note_queue_lease(ctx, "drain-2", 5)["items"] == []
    assert note_queue_ack(ctx, r.note_id, "ok") == {"note_id": r.note_id, "status": "processed"}
    assert nb.store.note_row(r.note_id)["status"] == "processed"
    with pytest.raises(ValueError):
        note_queue_ack(ctx, r.note_id, "maybe")
    with pytest.raises(ValueError):
        note_queue_ack(ctx, r.note_id, "ok", revision_id="rv_not_applied")


def test_failures_back_off_then_fail_and_retry(nb):
    r = nb.capture("flaky", occurred_at=NOW)
    t0 = NOW
    nb.queue.lease("w", now=t0)
    first = nb.queue.ack(r.note_id, "error", "model timed out", now=t0)
    assert first["status"] == "queued" and first["next_attempt_at"] == _plus(t0, RETRY_BACKOFF_S[0])
    assert nb.queue.lease("w", now=t0)["items"] == []  # not before the backoff
    t1 = first["next_attempt_at"]
    assert nb.queue.lease("w", now=t1)["items"][0]["attempts"] == 2
    second = nb.queue.ack(r.note_id, "error", "bad json", now=t1)
    assert second["next_attempt_at"] == _plus(t1, RETRY_BACKOFF_S[1])
    t2 = second["next_attempt_at"]
    nb.queue.lease("w", now=t2)
    third = nb.queue.ack(r.note_id, "error", "still bad", now=t2)
    assert third == {"note_id": r.note_id, "status": "failed"}
    assert nb.store.note_row(r.note_id)["status"] == "failed"
    summary = nb.queue.summary()
    assert summary["failed"][0]["last_error"] == "still bad" and summary["stats"]["failed"] == 1
    assert nb.queue.lease("w", now=_plus(t2, 10 ** 6))["items"] == []
    assert nb.queue.retry(r.note_id)["status"] == "queued"
    with pytest.raises(ValueError):
        nb.queue.retry(r.note_id)
    assert nb.queue.lease("w", now=_plus(t2, 1))["items"][0]["note_id"] == r.note_id


def test_expired_lease_is_taken_over_and_counts_as_a_failure(nb):
    r = nb.capture("crashy", occurred_at=NOW)
    nb.queue.lease("dead-drain", now=NOW)
    later = _plus(NOW, LEASE_S - 1)
    assert nb.queue.lease("drain-2", now=later)["items"] == []  # still leased
    expired = _plus(NOW, LEASE_S)
    assert nb.queue.lease("drain-2", now=expired)["items"] == []  # failed once, backing off
    item = nb.queue.item(r.note_id)
    assert item["failures"] == 1 and item["last_error"] == STOPPED_ERROR
    [again] = nb.queue.lease("drain-2", now=_plus(expired, RETRY_BACKOFF_S[0]))["items"]
    assert again["attempts"] == 2 and nb.queue.attempt_for(r.note_id) == 2


def test_two_leasers_never_take_the_same_note(tmp_path):
    root = tmp_path / "nb"
    with Notebook.open(root) as nb:
        for i in range(60):
            nb.capture(f"note {i}", occurred_at=NOW)
    taken = {}
    errors = []

    def worker(name):
        try:
            with Notebook.open(root) as mine:  # separate connections, like two drains
                while True:
                    items = mine.queue.lease(name, 3)["items"]
                    if not items:
                        return
                    for it in items:
                        taken.setdefault(it["note_id"], []).append(name)
                        mine.queue.ack(it["note_id"], "ok")
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert not errors
    assert len(taken) == 60
    assert all(len(v) == 1 for v in taken.values())
