"""AbstractMemory projection: active_at agrees with beliefs_at; closures; labels; vectors embedded once."""

from __future__ import annotations

import pytest

from abstractmemory import TripleQuery
from abstractnote.store.embeddings import vector_key
from abstractnote.store.kg import KIND_ATTR
from abstractnote.store.notebook import Notebook
from test_store_support import CountingEmbedder, claim, create, nb, rev, t  # noqa: F401


def _history(nb):
    nb.apply_revision(rev("rv_1", t(1), create("ex:person-dana", "Dana", aliases=["D. Ng"]),
                          create("ex:org-acme", "Acme"), create("ex:org-beta", "Beta"),
                          create("ex:project-lut", "LUT"),
                          claim("cl_1", "ex:person-dana", "schema:worksFor", "ex:org-acme", at=t(1)),
                          claim("cl_2", "ex:person-dana", "schema:participant", "ex:project-lut", at=t(1)),
                          claim("cl_3", "ex:project-lut", "ex:budget", literal="40k EUR", at=t(1))))
    nb.apply_revision(rev("rv_2", t(10), claim("cl_4", "ex:person-dana", "schema:worksFor", "ex:org-beta", at=t(10)),
                          {"op": "claim.supersede", "payload": {"claim_id": "cl_1", "by_claim_id": "cl_4",
                                                                "reason": "moved", "valid_until": t(10)}}))
    nb.apply_revision(rev("rv_3", t(20), claim("cl_5", "ex:project-lut", "ex:budget", literal="55k EUR", at=t(20),
                                               certainty="disputed", disputes="cl_3")))
    nb.retract_claim("cl_2", "user:1", "she never joined", occurred_at=t(25))
    # an explicit validity window in the past (time reference resolved by the model)
    nb.apply_revision(rev("rv_4", t(28), claim("cl_6", "ex:person-dana", "schema:alumniOf", "ex:org-acme", at=t(28),
                                               valid_from=t(1, 1, 2020))))


@pytest.mark.parametrize("probe", [t(1, 1, 2019), t(1, 6, 2020), t(1), t(5), t(10), t(15), t(25), t(26), t(29),
                                   t(1, 1, 2030)])
def test_active_at_agrees_with_beliefs_at(nb, probe):
    _history(nb)
    index = sorted(c["claim_id"] for c in nb.beliefs_at(probe))
    memory = nb.kg.active_claim_ids(probe)
    assert index == memory, probe


def test_agreement_goes_red_without_close_assertion(nb, monkeypatch):
    """The check above must fail when the AbstractMemory window is not closed."""
    monkeypatch.setattr(nb.kg.store, "close_assertion", lambda *a, **k: False)
    _history(nb)
    assert sorted(c["claim_id"] for c in nb.beliefs_at(t(15))) != nb.kg.active_claim_ids(t(15))


def test_supersede_and_retract_write_journal_closures(nb):
    _history(nb)
    [sup] = nb.kg.journal.closures(assertion_id="cl_1")
    assert sup.kind == "supersede" and sup.replacement_ids == ("cl_4",)
    [ret] = nb.kg.journal.closures(assertion_id="cl_2")
    assert ret.kind == "retract"
    [a] = nb.kg.query(assertion_ids=("cl_1",))
    assert a.valid_until == t(10)


def test_projection_fields_follow_the_data_model(nb):
    _history(nb)
    [a] = nb.kg.query(assertion_ids=("cl_1",))
    assert (a.scope, a.owner_id) == ("global", "abstractnote")
    assert a.observed_at == t(1) and a.valid_from == t(1)
    assert a.confidence == 0.9
    assert a.provenance["source"] == "abstractnote" and a.provenance["revision_id"] == "rv_1"
    assert a.attributes["subject_type"] == "person" and a.attributes["object_type"] == "org"
    assert a.attributes["certainty"] == "stated" and a.attributes["stance"] == "assert"
    [lit] = nb.kg.query(assertion_ids=("cl_3",))
    assert lit.object == "40k EUR" and lit.attributes["literal"] is True
    [disputed] = nb.kg.query(assertion_ids=("cl_5",))
    assert disputed.confidence == 0.3
    names = nb.kg.query(subject="ex:person-dana", predicate="schema:name")
    assert [n.object for n in names] == ["Dana"]
    aliases = nb.kg.query(subject="ex:person-dana", predicate="skos:altLabel")
    assert [n.object for n in aliases] == ["D. Ng"]
    assert all(x.attributes[KIND_ATTR] in ("name", "alias") for x in names + aliases)
    # disputes are recorded on both claims in the index
    assert nb.store.claim_dict(nb.store.claim_row("cl_3"))["disputed_with"] == ["cl_5"]


def test_merge_writes_same_as_and_unmerge_closes_it(nb):
    nb.apply_revision(rev("rv_1", t(1), create("ex:person-dana", "Dana"), create("ex:person-dana-2", "Dana N.")))
    nb.apply_revision(rev("rv_2", t(2), {"op": "entity.merge", "payload": {
        "from_id": "ex:person-dana-2", "into_id": "ex:person-dana", "reason": "same"}}))
    [same] = nb.kg.query(subject="ex:person-dana-2", predicate="schema:sameAs")
    assert same.object == "ex:person-dana" and same.valid_until is None
    nb.unmerge_entity("ex:person-dana", "ex:person-dana-2", "user:1", "not the same", occurred_at=t(3))
    [same] = nb.kg.query(subject="ex:person-dana-2", predicate="schema:sameAs")
    assert same.valid_until == t(3)


def test_vectors_are_computed_once_and_shared_with_the_triple_store(tmp_path):
    emb = CountingEmbedder(32)
    with Notebook.open(tmp_path / "nb", embedder=emb) as nb:
        _history(nb)
        assert len(emb.calls) == len(set(emb.calls))  # each distinct text embedded once
        [a] = nb.kg.query(assertion_ids=("cl_1",))
        from abstractmemory.canonical_text import canonical_text

        key = vector_key(canonical_text(a), nb.cache.model_id)
        assert nb.kg.store.stored_vector("cl_1") == pytest.approx(nb.cache.get(key))
        row = nb.store.claim_row("cl_1")
        assert row["vector_key"] == key
        assert nb.kg.embedding_pin()["model_id"] == emb.model_id
