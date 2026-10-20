"""Stale labels are named again at every reflect (eval2 D14): the pending list puts communities whose label went
stale before the unlabelled ones, so a long tail of new groups never starves them."""

from __future__ import annotations

from abstractnote.recall import communities as cm
from test_store_support import nb  # noqa: F401


def _row(nb, cid, size, label=None, stale=0):
    with nb.store.tx() as c:
        c.execute("INSERT INTO communities(community_id, label, size, member_hash, created_at, computed_at, stale_label)"
                  " VALUES (?,?,?,?,?,?,?)", (cid, label, size, cid, "2026-10-01T00:00:00Z", "2026-10-01T00:00:00Z", stale))


def test_stale_labels_come_before_larger_unlabelled_communities(nb):
    for i in range(cm.PENDING_LIMIT + 2):
        _row(nb, f"cm_new{i}", 50 + i)
    _row(nb, "cm_stale_small", 3, label="old name", stale=1)
    _row(nb, "cm_stale_big", 9, label="other name", stale=1)
    _row(nb, "cm_fresh", 100, label="kept", stale=0)
    items = cm.pending(nb)["items"]
    assert len(items) == cm.PENDING_LIMIT
    assert [it["community_id"] for it in items[:2]] == ["cm_stale_big", "cm_stale_small"]
    assert items[0]["previous_label"] == "other name" and items[0]["stale_label"] is True
    assert "cm_fresh" not in {it["community_id"] for it in items}
    assert [it["index"] for it in items] == list(range(cm.PENDING_LIMIT))
