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
