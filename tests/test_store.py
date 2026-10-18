"""Notebook store: layout, ids, revisions as the only mutation path, merges, user actions, as-of graph."""

from __future__ import annotations

import random

import pytest

from abstractnote import ids
from abstractnote.store.notebook import Notebook
from test_store_support import claim, create, nb, rev, t  # noqa: F401  (nb is a fixture)


def test_open_creates_the_documented_layout(tmp_path):
    root = tmp_path / "plane" / "abstractnote"
    with Notebook.open(root):
        pass
    assert (root / "note.sqlite3").is_file()
    assert (root / "kg.sqlite3").is_file()
    assert (root / "blobs").is_dir()
    assert (root / "extracted").is_dir()


def test_ids_are_deterministic_and_documented():
    assert ids.note_id_for_key("k1") == ids.note_id_for_key("k1") != ids.note_id_for_key("k2")
    assert ids.note_id_for_key("k1").startswith("nt_") and len(ids.note_id_for_key("k1")) == 29
    assert ids.note_id_for_import("agora", "m1") != ids.note_id_for_key("m1")
    a = ids.note_id_fallback("call dana", ["h1"], "2026-10-19T09:12:31Z", "watch")
    assert a == ids.note_id_fallback("call dana", ["h1"], "2026-10-19T09:12:59Z", "watch")  # same minute
    assert a != ids.note_id_fallback("call dana", ["h1"], "2026-10-20T09:12:31Z", "watch")  # another day
    assert a != ids.note_id_fallback("call dana", ["h1"], "2026-10-19T09:12:31Z", "web")
    assert ids.claim_id("nt_a", "s", "p", "o").startswith("cl_")
    assert ids.revision_id_for_note("nt_a", 1) != ids.revision_id_for_note("nt_a", 2)
    assert ids.revision_id_for_action("task.close", "tk_1", "2026-01-01T00:00:00Z").startswith("rv_")
    assert ids.attachment_id("nt_a", "abc").startswith("at_")
    assert ids.mint_entity_id("person", "Dana Ng", set()) == "ex:person-dana-ng"
    assert ids.mint_entity_id("person", "Dana Ng", {"ex:person-dana-ng"}) == "ex:person-dana-ng-2"
    assert ids.mint_entity_id("person", "Dana Ng", {"ex:person-dana-ng", "ex:person-dana-ng-2"}) == "ex:person-dana-ng-3"
    assert ids.mint_entity_id("place", "Zürich", set()) == "ex:place-zurich"
    with pytest.raises(ValueError):
        ids.mint_entity_id("planet", "Mars", set())
    assert ids.normalize_time("2026-10-19") == "2026-10-19T00:00:00Z"
    assert ids.normalize_time("2026-10-19T11:12:00+02:00") == "2026-10-19T09:12:00Z"


def test_apply_revision_is_idempotent(nb):
    r = rev("rv_1", t(1), create("ex:person-dana", "Dana"), create("ex:org-acme", "Acme"),
            claim("cl_1", "ex:person-dana", "schema:worksFor", "ex:org-acme", at=t(1)))
    first = nb.apply_revision(r)
    counts = nb.store.counts()
    kg = nb.kg.count()
    second = nb.apply_revision(r)
    assert second["already_applied"] is True
    assert second["claims_added"] == first["claims_added"] == ["cl_1"]
    assert nb.store.counts() == counts and nb.kg.count() == kg


def test_invalid_revision_applies_nothing_and_is_set_aside(nb):
    bad = rev("rv_bad", t(1), create("ex:person-dana", "Dana"),
              claim("cl_1", "ex:person-dana", "schema:worksFor", "ex:org-missing", at=t(1)))
    with pytest.raises(ValueError):
        nb.apply_revision(bad)
    assert nb.store.entity_row("ex:person-dana") is None  # the whole transaction rolled back
    assert nb.store.counts()["revisions"] == 0
    assert nb.recover() == []  # set aside, not replayed
    with pytest.raises(ValueError):
        nb.apply_revision(bad)


def test_merge_resolves_and_unmerge_restores(nb):
    nb.apply_revision(rev("rv_1", t(1), create("ex:person-dana", "Dana"), create("ex:person-dana-ng", "Dana Ng"),
                          create("ex:project-lut", "LUT"),
                          claim("cl_1", "ex:person-dana-ng", "schema:participant", "ex:project-lut", at=t(1))))
    nb.apply_revision(rev("rv_2", t(5), {"op": "entity.merge", "payload": {
        "from_id": "ex:person-dana-ng", "into_id": "ex:person-dana", "reason": "same person"}}))
    assert nb.store.canonical("ex:person-dana-ng") == "ex:person-dana"
    g = nb.graph()
    assert {n["id"] for n in g["nodes"]} == {"ex:person-dana", "ex:project-lut"}
    assert [(e["source"], e["target"]) for e in g["edges"]] == [("ex:person-dana", "ex:project-lut")]
    # Before the merge the graph still shows both records
    g_before = nb.graph(at=t(2))
    assert {n["id"] for n in g_before["nodes"]} == {"ex:person-dana", "ex:person-dana-ng", "ex:project-lut"}
    out = nb.unmerge_entity("ex:person-dana", "ex:person-dana-ng", "user:1", "different people",
                            occurred_at=t(6))
    assert out["revision_id"] == ids.revision_id_for_action("entity.unmerge", "ex:person-dana|ex:person-dana-ng", t(6))
    assert nb.store.canonical("ex:person-dana-ng") == "ex:person-dana-ng"
    g2 = nb.graph()
    assert [(e["source"], e["target"]) for e in g2["edges"]] == [("ex:person-dana-ng", "ex:project-lut")]
    stored = nb.revision(out["revision_id"])
    assert stored["source"] == {"client": "web", "account": "user:1"} and stored["note_id"] is None


def test_supersession_closes_window_and_keeps_history(nb):
    nb.apply_revision(rev("rv_1", t(1), create("ex:person-dana", "Dana"), create("ex:org-acme", "Acme"),
                          create("ex:org-beta", "Beta"),
                          claim("cl_1", "ex:person-dana", "schema:worksFor", "ex:org-acme", at=t(1))))
    nb.apply_revision(rev("rv_2", t(10), claim("cl_2", "ex:person-dana", "schema:worksFor", "ex:org-beta", at=t(10)),
                          {"op": "claim.supersede", "payload": {"claim_id": "cl_1", "by_claim_id": "cl_2",
                                                                "reason": "moved", "valid_until": t(10)}}))
    old = nb.store.claim_dict(nb.store.claim_row("cl_1"))
    assert old["status"] == "superseded" and old["valid_until"] == t(10) and old["superseded_by"] == "cl_2"
    assert [c["claim_id"] for c in nb.beliefs_at(t(5))] == ["cl_1"]
    assert [c["claim_id"] for c in nb.beliefs_at(t(11))] == ["cl_2"]


def test_user_actions_are_revisions_with_account(nb):
    nb.apply_revision(rev("rv_1", t(1), create("ex:person-dana", "Dana"),
                          {"op": "task.open", "payload": {"task_id": "tk_1", "text": "Send the Pareto"}},
                          claim("cl_1", "ex:person-dana", "schema:jobTitle", literal="Lead", at=t(1))))
    closed = nb.close_task("tk_1", "user:7", note="sent", occurred_at=t(3))
    assert closed["tasks_closed"] == ["tk_1"]
    assert nb.tasks(status="done")[0]["closed_at"] == t(3)
    with pytest.raises(ValueError):
        nb.close_task("tk_1", "user:7")
    retracted = nb.retract_claim("cl_1", "user:7", "never true", occurred_at=t(4))
    assert retracted["claims_retracted"] == ["cl_1"]
    assert nb.beliefs_at(t(5)) == [] and nb.kg.active_claim_ids(t(5)) == []
    assert nb.revision(retracted["revision_id"])["source"]["account"] == "user:7"
    with pytest.raises(ValueError):
        nb.retract_claim("cl_missing", "user:7", "x")


def test_curie_case_is_kept_in_the_index(nb):
    nb.apply_revision(rev("rv_1", t(1), create("ex:person-dana", "Dana"), create("ex:concept-lut", "LUT"),
                          claim("cl_1", "ex:person-dana", "schema:knowsAbout", "ex:concept-lut", at=t(1))))
    assert nb.store.claim_row("cl_1")["predicate"] == "schema:knowsAbout"
    [a] = nb.kg.query(assertion_ids=("cl_1",))
    assert a.predicate == "schema:knowsabout" and a.owner_id == "abstractnote"


def _random_history(nb, rng, n_revisions=25):
    """Random revisions with random history times; returns nothing (state lives in nb)."""
    entities = []
    times = sorted({t(rng.randint(1, 28), rng.randint(1, 12), rng.choice([2024, 2025])) for _ in range(n_revisions)})
    rng.shuffle(times)  # processed out of history order, like an import
    for i, at in enumerate(times):
        ops = []
        new = f"ex:thing-e{i}"
        ops.append(create(new, f"E{i}"))
        ops.append({"op": "entity.mention", "payload": {"entity_id": new}})
        if entities:
            other = rng.choice(entities)
            ops.append(claim(f"cl_{i}", new, "skos:related", other, at=at))
            ops.append({"op": "entity.mention", "payload": {"entity_id": other}})
        if len(entities) > 3 and rng.random() < 0.2:
            a, b = rng.sample(entities, 2)
            if nb.store.canonical(a) != nb.store.canonical(b) and nb.store.canonical(b) == b:
                ops.append({"op": "entity.merge", "payload": {"from_id": b, "into_id": nb.store.canonical(a),
                                                              "reason": "same"}})
        nb.apply_revision(rev(f"rv_{i}", at, *ops))
        entities.append(new)
    return times


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_property_graph_as_of_never_contains_the_future(nb, seed):
    rng = random.Random(seed)
    times = _random_history(nb, rng)
    probes = sorted(times) + [t(15, 6, 2023), t(31, 12, 2026)]
    for at in probes:
        g = nb.graph(at=at, limit=2000)
        node_ids = {n["id"] for n in g["nodes"]}
        for n in g["nodes"]:
            assert n["first_seen"] <= at, (seed, at, n)
        for e in g["edges"]:
            assert e["first_seen"] <= at
            assert e["source"] in node_ids and e["target"] in node_ids
        # and every entity first seen by then is present (unless merged away by then)
        expected = {r["entity_id"] for r in nb.store.q(
            "SELECT entity_id FROM entities WHERE first_seen<=? AND (merged_into IS NULL OR merged_at>?)", (at, at))}
        assert node_ids == expected


def test_crash_between_index_and_kg_recovers_to_exactly_one_revision(tmp_path):
    """S6: stop after the index commit; recovery applies the revision once in both stores."""
    root = tmp_path / "nb"
    nb = Notebook.open(root)
    r = rev("rv_1", t(1), create("ex:person-dana", "Dana"), create("ex:org-acme", "Acme"),
            claim("cl_1", "ex:person-dana", "schema:worksFor", "ex:org-acme", at=t(1)))
    with pytest.raises(RuntimeError):
        nb.apply_revision(r, _fail_after_index=True)
    row = nb.store.revision_row("rv_1")
    assert row["applied_index"] == 1 and row["applied_kg"] == 0
    assert nb.kg.query(assertion_ids=("cl_1",)) == []
    nb.close()
    nb = Notebook.open(root, recover=False)
    assert nb.recover() == ["rv_1"]
    row = nb.store.revision_row("rv_1")
    assert row["applied_index"] == 1 and row["applied_kg"] == 1
    assert nb.store.counts()["revisions"] == 1 and nb.store.counts()["claims"] == 1
    assert len(nb.kg.query(assertion_ids=("cl_1",))) == 1
    assert nb.recover() == []
    assert nb.apply_revision(r)["already_applied"] is True
    nb.close()


def test_embedding_pin_mismatch_refuses_to_open(tmp_path):
    from test_store_support import CountingEmbedder

    root = tmp_path / "nb"
    Notebook.open(root, embedder=CountingEmbedder(64, model_id="model-a")).close()
    with pytest.raises(ValueError, match="pinned"):
        Notebook.open(root, embedder=CountingEmbedder(64, model_id="model-b"))
    with pytest.raises(ValueError, match="model-a"):
        Notebook.open(root)  # no embedder and the pin is not the hashing one
