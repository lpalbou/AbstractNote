"""Rebuild: replaying notes and revisions reproduces the projections without re-embedding."""

from __future__ import annotations

import pytest

from abstractnote import ids
from abstractnote.store.notebook import Notebook
from test_store_support import CountingEmbedder, claim, create, rev, t


def _snapshot(nb):
    q = nb.store.q
    return {
        "entities": [tuple(r) for r in q("SELECT entity_id, kind, name, summary, merged_into, first_seen, last_seen,"
                                         " note_count FROM entities ORDER BY entity_id")],
        "aliases": [tuple(r) for r in q("SELECT entity_id, alias FROM entity_aliases ORDER BY 1, 2")],
        "claims": [tuple(r) for r in q("SELECT claim_id, subject_id, predicate, object_id, object_literal, status,"
                                       " valid_from, valid_until, superseded_by FROM claims ORDER BY claim_id")],
        "relations": [tuple(r) for r in q("SELECT * FROM relations ORDER BY 1, 2, 3")],
        "tasks": [tuple(r) for r in q("SELECT task_id, status, closed_at FROM tasks ORDER BY 1")],
        "topics": [tuple(r) for r in q("SELECT * FROM topics ORDER BY 1")],
        "kg": sorted((a.assertion_id, a.valid_until) for a in nb.kg.query()),
    }


def _fill(nb, n):
    nb.apply_revision(rev("rv_root", t(1, 1, 2024), create("ex:project-lut", "LUT"),
                          {"op": "task.open", "payload": {"task_id": "tk_root", "text": "Ship"}}))
    for i in range(n):
        at = ids.normalize_time(f"2025-{1 + i % 12:02d}-{1 + i % 28:02d}T{i % 24:02d}:00:00Z")
        eid = f"ex:person-p{i % 50}"
        ops = []
        if nb.store.entity_row(eid) is None:
            ops.append(create(eid, f"P{i % 50}", aliases=[f"p{i % 50}-alias"]))
        ops.append(claim(f"cl_{i}", eid, "schema:participant", "ex:project-lut", at=at))
        if i and i % 7 == 0:
            ops.append({"op": "claim.supersede", "payload": {"claim_id": f"cl_{i - 1}", "by_claim_id": f"cl_{i}",
                                                             "reason": "update", "valid_until": at}})
        ops.append({"op": "entity.summary", "payload": {"entity_id": eid, "summary": f"summary {i}"}})
        nb.apply_revision(rev(f"rv_{i}", at, *ops))
    nb.apply_revision(rev("rv_merge", t(1, 1, 2026), {"op": "entity.merge", "payload": {
        "from_id": "ex:person-p1", "into_id": "ex:person-p0", "reason": "same"}}))
    nb.close_task("tk_root", "user:1", occurred_at=t(2, 1, 2026))


def test_rebuild_reproduces_identical_ids_and_counts_on_1k_revisions(tmp_path):
    emb = CountingEmbedder(32)
    with Notebook.open(tmp_path / "nb", embedder=emb) as nb:
        _fill(nb, 1000)
        before = _snapshot(nb)
        calls_before = len(emb.calls)
        out = nb.rebuild()
        assert out["replayed_revisions"] == 1003
        assert _snapshot(nb) == before
        assert len(emb.calls) == calls_before  # no re-embedding
        assert not (tmp_path / "nb" / "note.sqlite3.pre-rebuild").exists()
    with Notebook.open(tmp_path / "nb", embedder=emb) as nb:
        assert _snapshot(nb) == before


def test_rebuild_reembed_changes_the_pin(tmp_path):
    with Notebook.open(tmp_path / "nb", embedder=CountingEmbedder(32, model_id="old-model")) as nb:
        _fill(nb, 20)
        before = _snapshot(nb)
        new = CountingEmbedder(48, model_id="new-model")
        nb.rebuild(reembed=True, embedder=new)
        assert nb.store.meta_get("embedding_pin")["model_id"] == "new-model"
        assert nb.kg.embedding_pin()["model_id"] == "new-model"
        assert nb.kg.embedding_pin()["dimension"] == 48
        assert new.calls  # vectors recomputed
        assert _snapshot(nb) == before
    with Notebook.open(tmp_path / "nb", embedder=CountingEmbedder(48, model_id="new-model")) as nb:
        assert nb.status()["embedding_pin"]["model_id"] == "new-model"


def test_failed_rebuild_restores_the_previous_stores(tmp_path, monkeypatch):
    with Notebook.open(tmp_path / "nb") as nb:
        _fill(nb, 10)
        before = _snapshot(nb)

        def boom(self):
            raise RuntimeError("disk full")

        monkeypatch.setattr(Notebook, "_reindex_notes", boom)
        with pytest.raises(RuntimeError):
            nb.rebuild()
        monkeypatch.undo()
        assert _snapshot(nb) == before
