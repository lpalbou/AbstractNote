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


def test_two_creates_of_one_name_are_one_and_an_alias_never_forces_a_reuse(nb):
    nb.apply_revision(rev("rv_1", t(1), create("ex:software-observer", "observer")))
    out = nb.apply_revision(rev("rv_2", t(2), create("ex:software-abstractobserver", "AbstractObserver",
                                                      aliases=["Observer"]),
                                create("ex:person-dana", "Dana"), create("ex:person-dana-2", "dana.")))
    # observer versus AbstractObserver is an identity judgement (ADR 0004): the model's and the review's
    assert _live(nb) == [("ex:person-dana", "dana"), ("ex:software-abstractobserver", "abstractobserver"),
                         ("ex:software-observer", "observer")]
    assert out["entities_created"] == ["ex:software-abstractobserver", "ex:person-dana"]


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


# ---------------------------------------------------------------------------- 3. repair of existing notebooks

def _legacy(nb, r):
    """Store and apply a revision the way AbstractNote did before canonical names (no apply-time reuse)."""
    r = nb._rev_dict(r)
    nb.store.insert_revision(r)
    return nb._finish(r)


def _legacy_world(nb):
    from test_store_support import claim

    _legacy(nb, rev("rv_1", t(1), create("ex:concept-memory", "Memory", summary="What the notebook keeps."),
                    create("ex:software-abstractobserver", "AbstractObserver"), create("ex:org-acme", "Acme")))
    _legacy(nb, rev("rv_2", t(2), create("ex:concept-memory-2", "memory"),
                    create("ex:software-observer", "observer", aliases=["AbstractObserver"]),
                    claim("cl_1", "ex:concept-memory-2", "ex:part_of", "ex:org-acme", at=t(2))))
    _legacy(nb, rev("rv_3", t(3), create("ex:thing-memory", "MEMORY.", kind="thing")))


def test_legacy_duplicates_are_marked_and_normalize_merges_them(nb):
    _legacy_world(nb)
    clashes = {r[0]: r[1] for r in nb.store.q("SELECT entity_id, key_clash_with FROM entities WHERE key_clash_with"
                                               " IS NOT NULL")}
    assert clashes == {"ex:concept-memory-2": "ex:concept-memory", "ex:thing-memory": "ex:concept-memory"}
    dry = nb.normalize_entities("laurent", client="cli", dry_run=True)
    assert dry["merges"] == [] and [(g["into_id"], g["from_ids"]) for g in dry["groups"]] == [
        ("ex:concept-memory", ["ex:concept-memory-2", "ex:thing-memory"])]  # observer is only an alias
    assert dry["groups"][0]["names"] == ["Memory", "memory", "MEMORY."]
    assert len(nb.store.name_key_groups()) == 1  # a dry run changes nothing
    nb.then_now("ex:concept-memory")  # fills the cache
    out = nb.normalize_entities("laurent", client="cli")
    assert [(m["from_id"], m["into_id"]) for m in out["merges"]] == [
        ("ex:concept-memory-2", "ex:concept-memory"), ("ex:thing-memory", "ex:concept-memory")]
    for m in out["merges"]:
        r = nb.revision(m["revision_id"])
        assert r["action"] == "entity.merge" and r["source"] == {"client": "cli", "account": "laurent"}
    assert _live(nb) == [("ex:concept-memory", "memory"), ("ex:org-acme", "acme"),
                         ("ex:software-abstractobserver", "abstractobserver"), ("ex:software-observer", "observer")]
    card = nb.entity("ex:concept-memory")
    assert card["merged_from"] == ["ex:concept-memory-2", "ex:thing-memory"]
    assert [c["claim_id"] for c in card["beliefs"]] == ["cl_1"]  # the statement follows the merge
    assert nb.store.entity_brief("ex:concept-memory")["aliases"] == []  # memory and MEMORY. are not other names
    assert {a[0] for a in nb.store.q("SELECT alias FROM entity_aliases WHERE entity_id='ex:concept-memory'")} == {
        "MEMORY."}  # the surface forms are kept (memory folds to Memory, the display name)
    assert nb.store.q1("SELECT COUNT(*) FROM then_now_cache")[0] == 0
    assert nb.store.name_key_groups() == [] and nb.normalize_entities("laurent")["merges"] == []
    before = (_live(nb), nb.store.counts())
    nb.rebuild()  # the legacy creates replay as marked duplicates, then the merges join them again
    assert (_live(nb), nb.store.counts()) == before


def test_a_normalisation_merge_is_undone_with_unmerge_and_stays_apart(nb):
    _legacy_world(nb)
    nb.normalize_entities(None, client="api")
    nb.unmerge_entity("ex:concept-memory", "ex:concept-memory-2", "laurent", "they are different")
    row = nb.store.entity_row("ex:concept-memory-2")
    assert (row["merged_into"], row["name"], row["name_key"], row["key_clash_with"]) == (
        None, "memory_2", "memory_2", None)  # a person never sees two entities named memory
    assert nb.store.claim_row("cl_1")["subject_id"] == "ex:concept-memory-2"
    assert [c["claim_id"] for c in nb.entity("ex:concept-memory-2")["beliefs"]] == ["cl_1"]
    assert nb.normalize_entities(None)["merges"] == []  # separated by a person: not joined again


def test_an_upgraded_notebook_is_repaired_when_it_next_processes_a_note(tmp_path):
    from test_process import extraction, mention, ml, run

    root = tmp_path / "nb"
    with Notebook.open(root) as nb:
        _legacy_world(nb)
    conn = sqlite3.connect(str(root / "note.sqlite3"))
    conn.execute("DROP INDEX idx_entities_name_key")
    conn.execute("UPDATE entities SET name=display_name, name_key=NULL, display_name=NULL, key_clash_with=NULL")
    conn.execute("UPDATE meta SET value='1' WHERE key='schema_version'")
    conn.commit()
    conn.close()
    with Notebook.open(root) as nb:
        assert nb.store.meta_get("name_key_repair") == "pending"
        assert len(nb.store.name_key_groups()) == 1  # reads change nothing
        nb.capture("A new note.", occurred_at=t(4))  # a capture answers at once: no repair on its path
        assert len(nb.store.name_key_groups()) == 1
        run(nb, "Memory again.", t(5), extraction([mention("m1", "Memory", "concept")]),
            lambda p, c: {"mentions": [ml("m1", "create", name="Memory", kind="concept")], "claims": [], "tasks": []})
        assert nb.store.name_key_groups() == [] and nb.store.meta_get("name_key_repair") is None
        sources = [r["source_json"] for r in nb.store.q("SELECT source_json FROM revisions WHERE action='entity.merge'")]
        assert len(sources) == 2 and all('"migration"' in s for s in sources)


def test_cli_normalize_dry_run_and_merge(tmp_path, capsys):
    import json

    from abstractnote.cli import main

    root = tmp_path / "nb"
    with Notebook.open(root) as nb:
        _legacy_world(nb)
    assert main(["normalize", "--notebook", str(root), "--dry-run"]) == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["dry_run"] is True and len(dry["groups"]) == 1 and dry["merges"] == []
    assert main(["normalize", "--notebook", str(root), "--account", "laurent"]) == 0
    done = json.loads(capsys.readouterr().out)
    assert len(done["merges"]) == 2


def test_dry_run_of_a_notebook_pinned_to_an_absent_model_and_its_merge_refused(tmp_path, capsys):
    import json

    from abstractnote.cli import main
    from test_store_support import CountingEmbedder

    root = tmp_path / "nb"
    with Notebook.open(root, embedder=CountingEmbedder(model_id="some-gateway-model")) as nb:
        _legacy_world(nb)
    assert main(["normalize", "--notebook", str(root), "--dry-run"]) == 0
    assert len(json.loads(capsys.readouterr().out)["groups"]) == 1
    assert main(["normalize", "--notebook", str(root)]) == 2
    assert "some-gateway-model" in capsys.readouterr().err


# ---------------------------------------------------------------------------- 4. what clients see

def test_cards_lists_and_overview_show_the_canonical_name_and_keep_the_surface_form(nb):
    _legacy_world(nb)
    nb.apply_revision(rev("rv_4", t(4), {"op": "entity.alias", "payload": {"entity_id": "ex:concept-memory",
                                                                          "alias": "the memory"}}))
    nb.normalize_entities(None)
    card = nb.entity("ex:concept-memory")
    assert (card["name"], card["display_name"]) == ("memory", "Memory")
    assert card["aliases"] == ["the memory"]  # MEMORY., memory and the merged entries' names are not other names
    listed = {e["entity_id"]: e for e in nb.entities()["items"]}
    assert listed["ex:concept-memory"]["name"] == "memory" and "ex:concept-memory-2" not in listed
    ov = nb.overview(days=3650, now=t(5))
    ids_ = [e["entity_id"] for e in ov["entities"]]
    assert len(ids_) == len(set(ids_)) and [e["name"] for e in ov["entities"]].count("memory") == 1
    node = next(n for n in nb.graph()["nodes"] if n["id"] == "ex:concept-memory")
    assert (node["name"], node["display_name"]) == ("memory", "Memory")
    line = nb.expand(entity_ids=["ex:concept-memory"])["entities"][0]["line"]
    assert line.startswith("memory (concept; also the memory)")


# ---------------------------------------------------------------------------- 4. the echoed candidate format (gate 04 M4)


def test_a_name_that_echoes_its_kind_is_filed_under_the_name(nb):
    from test_process import extraction, mention, ml, run

    run(nb, "Memory matters.", "2026-01-05T10:00:00Z", extraction([mention("m1", "Memory", "software")]),
        lambda p, c: {"mentions": [ml("m1", "create", name="Memory", kind="software")], "claims": [], "tasks": []})
    # the model wrote the name the way the packet showed a candidate: "memory (software)"
    _, _, _, out = run(nb, "The memory keeps it.", "2026-01-06T10:00:00Z",
                       extraction([mention("m1", "memory (software)", "software")]),
                       lambda p, c: {"mentions": [ml("m1", "create", name="memory (software)", kind="software")],
                                     "claims": [], "tasks": []})
    assert out["entities_created"] == [] and _live(nb) == [("ex:software-memory", "memory")]
    assert nb.store.entity_row("ex:software-memory")["note_count"] == 2
    assert "memory (software)" not in {a[0] for a in nb.store.q("SELECT alias FROM entity_aliases")}


def test_an_echoed_kind_is_stripped_from_a_new_name_and_its_aliases(nb):
    from test_process import extraction, mention, ml, run

    _, _, _, out = run(nb, "Call fetch_url first.", "2026-01-05T10:00:00Z",
                       extraction([mention("m1", "fetch_url (concept)", "concept", aliases=["fetch_url"])]),
                       lambda p, c: {"mentions": [ml("m1", "create", name="fetch_url (Concept)", kind="concept",
                                                     aliases=["fetch_url (concept)"])], "claims": [], "tasks": []})
    assert _live(nb) == [("ex:concept-fetch-url", "fetch_url")]
    row = nb.store.entity_row("ex:concept-fetch-url")
    assert row["display_name"] == "fetch_url"
    assert {a[0] for a in nb.store.q("SELECT alias FROM entity_aliases")} == set()


def test_only_a_known_kind_in_parentheses_is_stripped():
    from abstractnote.process.revise import strip_kind_suffix

    assert strip_kind_suffix("memory (software)") == "memory"
    assert strip_kind_suffix("Tier0 Core (CONCEPT) ") == "Tier0 Core"
    for kept in ("Mercury (planet)", "Plan (v9)", "AbstractMemory (Future)", "(software)", "memory(software)",
                 "memory (software) notes"):
        assert strip_kind_suffix(kept) == kept


def _artefact_world(nb):
    _legacy(nb, rev("rv_1", t(1), create("ex:software-memory", "Memory", kind="software"),
                    create("ex:concept-fetch-url", "fetch_url"), create("ex:person-contact", "Contact", kind="person")))
    _legacy(nb, rev("rv_2", t(2), create("ex:software-memory-software", "memory (software)", kind="software",
                                         aliases=["memory"]),
                    create("ex:concept-fetch-url-concept", "fetch_url (concept)", aliases=["fetch_url"]),
                    create("ex:concept-contact-person", "contact person"),  # written as words: not the artefact
                    create("ex:concept-orphan-concept", "orphan (concept)")))  # no orphan to join


def test_normalize_joins_an_echoed_kind_into_the_entity_it_names(nb):
    _artefact_world(nb)
    dry = nb.normalize_entities(None, client="cli", dry_run=True)
    assert dry["merges"] == []
    assert sorted((g["into_id"], g["from_ids"], g["kind_suffix"]) for g in dry["groups"]) == [
        ("ex:concept-fetch-url", ["ex:concept-fetch-url-concept"], ["ex:concept-fetch-url-concept"]),
        ("ex:software-memory", ["ex:software-memory-software"], ["ex:software-memory-software"])]
    out = nb.normalize_entities(None, client="cli")
    assert sorted((m["from_id"], m["into_id"]) for m in out["merges"]) == [
        ("ex:concept-fetch-url-concept", "ex:concept-fetch-url"), ("ex:software-memory-software", "ex:software-memory")]
    r = nb.revision(out["merges"][0]["revision_id"])
    assert "kind" in r["summary"]
    live = dict(_live(nb))
    assert "ex:concept-contact-person" in live and "ex:concept-orphan-concept" in live
    aliases = {a[0] for a in nb.store.q("SELECT alias FROM entity_aliases WHERE entity_id='ex:software-memory'")}
    assert "memory (software)" not in aliases  # the echoed form is not another name
    assert nb.normalize_entities(None, dry_run=True)["groups"] == []


def test_an_echoed_kind_and_a_shared_canonical_name_join_one_survivor(nb):
    _legacy(nb, rev("rv_1", t(1), create("ex:software-memory", "Memory", kind="software")))
    _legacy(nb, rev("rv_2", t(2), create("ex:software-memory-2", "memory", kind="software"),
                    create("ex:software-memory-software", "memory (software)", kind="software"),
                    create("ex:software-memory-software-2", "Memory (Software)", kind="software")))
    (group,) = nb.normalize_entities(None, dry_run=True)["groups"]
    assert group["into_id"] == "ex:software-memory"
    assert group["from_ids"] == ["ex:software-memory-2", "ex:software-memory-software", "ex:software-memory-software-2"]
    nb.normalize_entities(None)
    assert _live(nb) == [("ex:software-memory", "memory")]
