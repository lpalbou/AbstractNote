"""One canonical name per entity (operator rule): ``ids.canonical_name``, the ``name_key`` column, alias keys,
apply-time reuse, the repair of existing notebooks and what clients show."""

from __future__ import annotations

import sqlite3

import pytest

from abstractnote import ids
from abstractnote.process.context import save_packet, empty_packet
from abstractnote.process.resolve import resolve_candidates
from abstractnote.store.notebook import Notebook
from test_store_support import create, nb, rev, t  # noqa: F401  (nb is a fixture)


@pytest.mark.parametrize("text, expected", [
    ("Memory", "memory"),
    ("  memory  ", "memory"),
    ("MEMORY.", "memory"),
    ("Laurent-Philippe  Albou", "laurent_philippe_albou"),
    ("laurent_philippe_albou", "laurent_philippe_albou"),
    ("Abstract   Gateway", "abstract_gateway"),
    ("abstract-gateway", "abstract_gateway"),
    ("abstract__gateway!!", "abstract_gateway"),
    ("Ａｂｃ", "abc"),  # NFKC: full-width letters
    ("Zürich", "zürich"),
    ("C++", "c++"),
    ("AbstractObserver", "abstractobserver"),
    ("...", "..."),  # only separators: the folded text itself, never empty
])
def test_canonical_name_rule(text, expected):
    assert ids.canonical_name(text) == expected
    assert ids.name_key(text) == expected


def test_entity_ids_are_minted_from_the_canonical_name():
    assert ids.entity_id_base("concept", "Memory") == ids.entity_id_base("concept", "memory") == "ex:concept-memory"
    assert ids.entity_id_base("person", "Laurent-Philippe Albou") == "ex:person-laurent-philippe-albou"


def test_projection_stores_the_canonical_name_and_the_surface_form(nb):
    nb.apply_revision(rev("rv_1", t(1), create("ex:person-laurent", "Laurent-Philippe  Albou", aliases=["LPA", "lpa"])))
    row = nb.store.entity_row("ex:person-laurent")
    assert (row["name"], row["name_key"], row["display_name"]) == (
        "laurent_philippe_albou", "laurent_philippe_albou", "Laurent-Philippe  Albou")
    keys = nb.store.q("SELECT alias, alias_key FROM entity_aliases WHERE entity_id='ex:person-laurent'")
    assert [(r[0], r[1]) for r in keys] == [("LPA", "lpa")]  # "lpa" folds to the same alias
    brief = nb.store.entity_brief("ex:person-laurent")
    assert (brief["name"], brief["display_name"], brief["aliases"]) == (
        "laurent_philippe_albou", "Laurent-Philippe  Albou", ["LPA"])


def test_alias_lookup_is_by_key(nb):
    nb.apply_revision(rev("rv_1", t(1), create("ex:software-gw", "AbstractGateway", aliases=["Abstract Gateway"])))
    note = nb.capture("abstract-gateway restarted", occurred_at=t(2))
    save_packet(nb, note.note_id, empty_packet())
    out = resolve_candidates(nb, note.note_id, [{"ref": "m1", "name": "abstract-gateway", "kind": "software"}])
    (m,) = out["mentions"]
    assert m["candidates"][0]["entity_id"] == "ex:software-gw"
    assert "alias" in m["candidates"][0]["matched_by"]


def test_migration_backfills_keys_and_marks_later_duplicates(tmp_path):
    root = tmp_path / "nb"
    with Notebook.open(root) as nb:
        nb.apply_revision(rev("rv_1", t(1), create("ex:concept-memory", "Memory")))
        nb.apply_revision(rev("rv_2", t(2), create("ex:software-gw", "Gateway", aliases=["gw"])))
    conn = sqlite3.connect(str(root / "note.sqlite3"))
    conn.execute("DROP INDEX idx_entities_name_key")
    conn.execute("UPDATE entities SET name=display_name, name_key=NULL, display_name=NULL")
    conn.execute("INSERT INTO entities(entity_id, kind, name, created_at, created_revision_id, recorded_at)"
                 " SELECT 'ex:concept-memory-2', 'concept', 'memory', ?, 'rv_2', recorded_at FROM entities"
                 " WHERE entity_id='ex:concept-memory'", (t(3),))
    conn.execute("UPDATE entity_aliases SET alias_key=NULL")
    conn.execute("UPDATE meta SET value='1' WHERE key='schema_version'")
    conn.commit()
    conn.close()
    with Notebook.open(root) as nb:
        rows = {r["entity_id"]: (r["name"], r["name_key"], r["display_name"], r["key_clash_with"])
                for r in nb.store.q("SELECT * FROM entities")}
        assert rows == {
            "ex:concept-memory": ("memory", "memory", "Memory", None),
            "ex:concept-memory-2": ("memory", "memory", "memory", "ex:concept-memory"),
            "ex:software-gw": ("gateway", "gateway", "Gateway", None),
        }
        assert nb.store.q1("SELECT alias_key FROM entity_aliases WHERE entity_id='ex:software-gw'")[0] == "gw"
        assert nb.store.meta_get("schema_version") == 2
        assert nb.store.meta_get("name_key_repair") == "pending"
        # The unique index holds for live, unmarked entities.
        with pytest.raises(sqlite3.IntegrityError):
            with nb.store.tx() as c:
                c.execute("UPDATE entities SET key_clash_with=NULL WHERE entity_id='ex:concept-memory-2'")


# ---------------------------------------------------------------------------- 2. apply-time guarantee

def _live(nb):
    return sorted((r["entity_id"], r["name"]) for r in nb.store.q("SELECT * FROM entities WHERE merged_into IS NULL"))


def test_a_create_of_a_taken_canonical_name_becomes_a_reuse(nb):
    from test_store_support import claim

    nb.apply_revision(rev("rv_1", t(1), create("ex:concept-memory", "Memory", summary="What the notebook keeps.")))
    out = nb.apply_revision(rev(
        "rv_2", t(2), create("ex:concept-memory-2", "memory"), create("ex:software-abstractgateway", "AbstractGateway"),
        {"op": "entity.mention", "payload": {"entity_id": "ex:concept-memory-2", "note_id": None}},
        claim("cl_1", "ex:software-abstractgateway", "ex:uses", "ex:concept-memory-2", at=t(2))))
    assert _live(nb) == [("ex:concept-memory", "memory"), ("ex:software-abstractgateway", "abstractgateway")]
    assert out["entities_created"] == ["ex:software-abstractgateway"]
    assert any("has the canonical name memory" in w and "ex:concept-memory" in w for w in out["warnings"])
    assert not any("kind_conflict" in w for w in out["warnings"])
    assert nb.store.claim_row("cl_1")["object_id"] == "ex:concept-memory"
    stored = nb.revision("rv_2")  # the stored revision is the rewritten one: a replay agrees
    assert all(op["payload"].get("entity_id") != "ex:concept-memory-2" for op in stored["ops"])
    before = _live(nb)
    nb.rebuild()
    assert _live(nb) == before


def test_a_kind_disagreement_never_makes_a_second_entity(nb):
    nb.apply_revision(rev("rv_1", t(1), create("ex:concept-memory", "Memory")))
    out = nb.apply_revision(rev("rv_2", t(2), create("ex:software-memory", "MEMORY", kind="software",
                                                      aliases=["Memory module"])))
    assert _live(nb) == [("ex:concept-memory", "memory")]
    assert nb.store.entity_row("ex:concept-memory")["kind"] == "concept"
    (w,) = [w for w in out["warnings"] if "kind_conflict" in w]
    assert "software" in w and "concept" in w
    assert nb.store.entity_brief("ex:concept-memory")["aliases"] == ["Memory module"]


def test_an_alias_key_already_held_is_a_reuse_and_two_creates_of_one_name_are_one(nb):
    nb.apply_revision(rev("rv_1", t(1), create("ex:software-observer", "observer")))
    out = nb.apply_revision(rev("rv_2", t(2), create("ex:software-abstractobserver", "AbstractObserver",
                                                      aliases=["Observer"]),
                                create("ex:person-dana", "Dana"), create("ex:person-dana-2", "dana.")))
    assert _live(nb) == [("ex:person-dana", "dana"), ("ex:software-observer", "observer")]
    assert "AbstractObserver" in nb.store.entity_brief("ex:software-observer")["aliases"]
    assert out["entities_created"] == ["ex:person-dana"]


def test_identical_statement_after_the_reuse_is_reinforced_and_a_rename_onto_a_taken_name_is_refused(nb):
    from test_store_support import claim

    nb.apply_revision(rev("rv_1", t(1), create("ex:person-dana", "Dana"), create("ex:org-acme", "Acme"),
                          claim("cl_1", "ex:person-dana", "ex:works_at", "ex:org-acme", at=t(1))))
    out = nb.apply_revision(rev("rv_2", t(2), create("ex:person-dana-2", "DANA"),
                                claim("cl_2", "ex:person-dana-2", "ex:works_at", "ex:org-acme", at=t(2)),
                                claim("cl_3", "ex:person-dana-2", "ex:knows", "ex:person-dana", at=t(2))))
    assert out["claims_added"] == [] and out["claims_reinforced"] == ["cl_1"]
    assert any("to itself" in w for w in out["warnings"])
    with pytest.raises(ValueError, match="already called acme"):
        nb.apply_revision(rev("rv_3", t(3), {"op": "entity.rename", "payload": {"entity_id": "ex:person-dana",
                                                                                "name": "ACME"}}))
    assert nb.store.revision_row("rv_3") is None


def test_the_model_creating_a_known_name_still_reuses_it(nb):
    from test_process import extraction, mention, ml, run

    run(nb, "Memory matters.", "2026-01-05T10:00:00Z", extraction([mention("m1", "Memory", "concept")]),
        lambda p, c: {"mentions": [ml("m1", "create", name="Memory", kind="concept")], "claims": [], "tasks": []})
    _, _, cands, out = run(nb, "memory again.", "2026-01-06T10:00:00Z", extraction([mention("m1", "memory", "concept")]),
                           lambda p, c: {"mentions": [ml("m1", "create", name="memory", kind="concept")],
                                         "claims": [], "tasks": []})
    assert cands["mentions"][0]["candidates"][0]["entity_id"] == "ex:concept-memory"
    assert out["entities_created"] == [] and _live(nb) == [("ex:concept-memory", "memory")]
    assert nb.store.entity_row("ex:concept-memory")["note_count"] == 2


def test_candidates_offer_the_key_identical_entity_first(nb):
    nb.apply_revision(rev("rv_1", t(1), create("ex:concept-a-memory-palace", "Memory Palace",
                                               summary="memory memory memory")))
    nb.apply_revision(rev("rv_2", t(2), create("ex:concept-memory", "MEMORY"), {"op": "entity.alias", "payload": {
        "entity_id": "ex:concept-a-memory-palace", "alias": "memory"}}))  # an alias is the model's call
    note = nb.capture("Memory, again", occurred_at=t(3))
    save_packet(nb, note.note_id, empty_packet())
    # The mention's other name ranks the palace higher by words and meaning; its own canonical name still wins.
    out = resolve_candidates(nb, note.note_id, [{"ref": "m1", "name": "Memory", "kind": "concept",
                                                 "aliases": ["Memory Palace"]}])
    assert [c["entity_id"] for c in out["mentions"][0]["candidates"]][:2] == ["ex:concept-memory",
                                                                            "ex:concept-a-memory-palace"]
