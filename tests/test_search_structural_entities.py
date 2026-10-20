"""Search leaves out entity hits whose name is a value, not a thing (eval2 quick-recall finding: entity hits were
junk): an entity stored before the structural demotion (``process.demote.literal_reason``: a version, a number, an
id, a token, a generic word) is never an entity hit; its notes still are note hits."""

from __future__ import annotations

from test_store_support import create, nb, rev  # noqa: F401

AT = "2026-01-02T09:00:00Z"


def test_value_named_entities_are_not_entity_hits(nb):
    n = nb.capture("Shipped v15 of the gateway today.", occurred_at=AT).note_id
    nb.apply_revision(rev("rv_1", AT, create("ex:artifact-v15", "v15"), create("ex:software-gw", "gateway"),
                          {"op": "entity.mention", "payload": {"entity_id": "ex:artifact-v15", "note_id": n}},
                          {"op": "entity.mention", "payload": {"entity_id": "ex:software-gw", "note_id": n}},
                          note_id=n))
    out = nb.search("v15 gateway")
    assert out["notes"][0]["note_id"] == n
    assert [e["entity_id"] for e in out["entities"]] == ["ex:software-gw"]
    assert nb.search("v15")["notes"][0]["note_id"] == n and nb.search("v15")["entities"] == []
