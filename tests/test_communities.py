"""Communities of the knowledge graph (backlog 0030): Louvain over live entities, stored with stable ids,
labels kept while membership changes little, loose ends, merges followed, read-only refusal, rebuild."""

from __future__ import annotations

import pytest

from test_store_support import claim, create, nb, rev, t  # noqa: F401

from abstractnote.recall import communities as cm

A = ["ex:project-lut", "ex:person-dana", "ex:person-ben", "ex:concept-pareto"]
B = ["ex:software-gateway", "ex:software-runtime", "ex:software-memory", "ex:person-eve"]
LOOSE = ["ex:place-paris", "ex:thing-umbrella", "ex:thing-kite"]


def mention(eid: str, note_id: str):
    return {"op": "entity.mention", "payload": {"entity_id": eid, "note_id": note_id}}


def _clique(prefix: str, members, at: str, note_id: str):
    ops = []
    for i, s in enumerate(members):
        for o in members[i + 1:]:
            for k in range(2):  # two statements per pair: a strong link
                ops.append(claim(f"cl_{prefix}_{s[3:]}_{o[3:]}_{k}", s, f"ex:rel_{k}", o, at=at, note_id=note_id))
    return ops


def world(nb):
    """Two tight groups (A: the LUT project, B: the framework software) joined by one weak statement, a pair
    (Paris, umbrella) and a lone kite: the last three are loose ends."""
    n1 = nb.capture("LUT with Dana, Ben and the Pareto.", occurred_at=t(1)).note_id
    n2 = nb.capture("Gateway, runtime, memory; Eve.", occurred_at=t(2)).note_id
    n3 = nb.capture("Paris umbrella; a kite.", occurred_at=t(3)).note_id
    nb.apply_revision(rev("rv_a", t(1), *[create(e, e.split("-", 1)[1].title()) for e in A],
                          *[mention(e, n1) for e in A], *_clique("a", A, t(1), n1), note_id=n1))
    nb.apply_revision(rev("rv_b", t(2), *[create(e, e.split("-", 1)[1].title()) for e in B],
                          *[mention(e, n2) for e in B], *_clique("b", B, t(2), n2),
                          claim("cl_bridge", "ex:person-dana", "ex:uses", "ex:software-gateway", at=t(2), note_id=n2),
                          note_id=n2))
    nb.apply_revision(rev("rv_c", t(3), *[create(e, e.split("-", 1)[1].title()) for e in LOOSE],
                          *[mention(e, n3) for e in LOOSE[:2]],
                          claim("cl_rain", "ex:place-paris", "ex:needs", "ex:thing-umbrella", at=t(3), note_id=n3),
                          note_id=n3))
    return {"n1": n1, "n2": n2, "n3": n3}


def _groups(nb):
    out = {}
    for c in nb.communities()["items"]:
        out[c["community_id"]] = set(cm.members(nb, c["community_id"]))
    return out


def test_recompute_finds_the_two_groups_and_leaves_the_rest_as_loose_ends(nb):
    world(nb)
    assert nb.communities() == {"computed_at": None, "resolution": None, "networkx": None,
                                "revision_count_at_compute": 0, "revisions_since": 0, "loose_ends": 0,
                                "items": []}  # never computed: nothing
    counts = nb.recompute_communities()
    assert (counts["communities"], counts["loose_ends"], counts["entities"]) == (2, 3, 11)
    assert counts["labels_kept"] == 0 and counts["new"] == 2
    assert sorted(map(frozenset, _groups(nb).values()), key=sorted) == sorted([frozenset(A), frozenset(B)], key=sorted)
    got = nb.communities()
    assert got["loose_ends"] == 3 and got["revisions_since"] == 0 and got["resolution"] == 1.0
    for c in got["items"]:
        members = sorted(cm.members(nb, c["community_id"]))
        assert c["community_id"] == cm.community_id_for(members)  # deterministic from the sorted member ids
        assert c["size"] == 4 and c["label"] is None and c["stale_label"] is False and c["labelled_at"] is None
        assert [e["entity_id"] for e in c["top_entities"]][:1] and all(
            set(e) == {"entity_id", "name", "kind", "note_count"} for e in c["top_entities"])
    assert nb.store.q1("SELECT COUNT(*) FROM entity_communities")[0] == 8


def test_detection_is_deterministic(nb):
    world(nb)
    nb.recompute_communities()
    first = _groups(nb)
    nb.recompute_communities()
    assert _groups(nb) == first


def test_a_label_survives_a_small_change_and_goes_stale_after_a_larger_one(nb):
    w = world(nb)
    nb.recompute_communities()
    (lut,) = [c for c, m in _groups(nb).items() if "ex:project-lut" in m]
    res = cm.label(nb, [{"community_id": lut, "label": "LUT budget work", "summary": "The LUT project.", "kind": "project"}])
    assert res == {"labelled": [lut], "warnings": []}

    def grow(name: str, day: int):
        eid = f"ex:person-{name}"
        ops = [create(eid, name.title()), mention(eid, w["n1"])]
        ops += [claim(f"cl_{name}_{i}", eid, "ex:works_with", m, at=t(day), note_id=w["n1"]) for i, m in enumerate(A)]
        nb.apply_revision(rev(f"rv_{name}", t(day), *ops, note_id=w["n1"]))

    grow("cleo", 4)  # 5 members, Jaccard with the labelled 4 = 0.8: same id, label kept, not stale (20 % change)
    counts = nb.recompute_communities()
    assert counts["labels_kept"] == 1 and counts["stale_labels"] == 0
    item = {c["community_id"]: c for c in nb.communities()["items"]}[lut]
    assert (item["label"], item["kind"], item["stale_label"], item["size"]) == ("LUT budget work", "project", False, 5)
    assert nb.communities()["revisions_since"] == 0

    grow("dov", 5)  # 6 members, Jaccard 4/6 = 0.67 >= 0.6: same id and label, but stale (more than 20 % changed)
    assert nb.communities()["revisions_since"] == 1  # the graph moved since the last computation
    counts = nb.recompute_communities()
    item = {c["community_id"]: c for c in nb.communities()["items"]}[lut]
    assert (item["label"], item["stale_label"], item["size"]) == ("LUT budget work", True, 6)
    assert counts["stale_labels"] == 1
    (waiting,) = [i for i in cm.pending(nb)["items"] if i["community_id"] == lut]
    assert (waiting["stale_label"], waiting["previous_label"], waiting["previous_summary"]) == (
        True, "LUT budget work", "The LUT project.")
    # labelling again clears the stale mark and measures the next change from this membership
    cm.label(nb, [{"community_id": lut, "label": "LUT team", "summary": "The LUT team.", "kind": "people"}])
    item = {c["community_id"]: c for c in nb.communities()["items"]}[lut]
    assert (item["label"], item["stale_label"]) == ("LUT team", False)


def test_a_community_that_changed_a_lot_gets_a_new_id_and_no_label(nb):
    w = world(nb)
    nb.recompute_communities()
    (lut,) = [c for c, m in _groups(nb).items() if "ex:project-lut" in m]
    cm.label(nb, [{"community_id": lut, "label": "LUT", "summary": "x.", "kind": "project"}])
    ops = []
    for name in ("fay", "gus", "hal"):  # 7 members, Jaccard 4/7 = 0.57 < 0.6
        eid = f"ex:person-{name}"
        ops += [create(eid, name.title()), mention(eid, w["n1"])]
        ops += [claim(f"cl_{name}_{i}", eid, "ex:works_with", m, at=t(6), note_id=w["n1"]) for i, m in enumerate(A)]
    nb.apply_revision(rev("rv_more", t(6), *ops, note_id=w["n1"]))
    nb.recompute_communities()
    (now,) = [c for c in nb.communities()["items"] if "ex:project-lut" in cm.members(nb, c["community_id"])]
    assert now["community_id"] != lut and now["label"] is None and now["size"] == 7
    assert now["community_id"] == cm.community_id_for(cm.members(nb, now["community_id"]))


def test_merged_entities_count_for_the_survivor(nb):
    world(nb)
    # Benjamin has no statement and no note of his own: Ben's, merged into him, are what place him with LUT
    nb.apply_revision(rev("rv_bj", t(7), create("ex:person-benjamin", "Benjamin")))
    nb.apply_revision(rev("rv_m", t(7), {"op": "entity.merge", "payload": {
        "from_id": "ex:person-ben", "into_id": "ex:person-benjamin", "reason": "same"}}))
    counts = nb.recompute_communities()
    assert counts["entities"] == 11
    groups = list(_groups(nb).values())
    assert all("ex:person-ben" not in g for g in groups)
    assert {"ex:project-lut", "ex:person-dana", "ex:concept-pareto", "ex:person-benjamin"} in groups
    # two statements LUT -> Ben, plus the note they share with two others (1/(4-1)), now Benjamin's
    assert cm.entity_graph(nb)["ex:person-benjamin"]["ex:project-lut"]["weight"] == pytest.approx(2 + 1 / 3)


def test_detection_passes_its_fixed_seed_and_the_resolution(nb, monkeypatch):
    import networkx.algorithms.community as nxc

    seen = {}
    real = nxc.louvain_communities

    def spy(g, **kw):
        seen.update(kw)
        return real(g, **kw)

    monkeypatch.setattr(nxc, "louvain_communities", spy)
    world(nb)
    nb.recompute_communities(resolution=1.5)
    assert seen == {"weight": "weight", "resolution": 1.5, "seed": cm.SEED}


def test_co_mentions_alone_link_entities(nb):
    n = nb.capture("Ann, Bo and Cy met.", occurred_at=t(1)).note_id
    ents = ["ex:person-ann", "ex:person-bo", "ex:person-cy"]
    nb.apply_revision(rev("rv_x", t(1), *[create(e, e[10:]) for e in ents], *[mention(e, n) for e in ents], note_id=n))
    assert nb.recompute_communities()["communities"] == 1  # no statement at all: the shared note links them
    g = cm.entity_graph(nb)
    assert g["ex:person-ann"]["ex:person-bo"]["weight"] == pytest.approx(0.5)  # 1/(k-1) per shared note, k = 3


def test_resolution_is_a_parameter(nb):
    world(nb)
    assert nb.recompute_communities(resolution=1.0)["communities"] == 2
    many = nb.recompute_communities(resolution=8.0)
    assert many["communities"] < 2 and many["loose_ends"] > 3  # a fine resolution splits the groups into pairs
    assert nb.communities()["resolution"] == 8.0
    with pytest.raises(ValueError, match="resolution"):
        nb.recompute_communities(resolution=0)


def test_a_read_only_notebook_reads_communities_and_refuses_to_recompute(tmp_path):
    from abstractnote.store.notebook import Notebook
    from test_store_support import CountingEmbedder

    path = tmp_path / "pinned"
    with Notebook.open(path, embedder=CountingEmbedder(model_id="pinned-model")) as nb1:
        world(nb1)
        nb1.recompute_communities()
    with Notebook.open(path) as ro:
        assert ro.read_only
        assert len(ro.communities()["items"]) == 2
        with pytest.raises(ValueError, match="embedding model"):
            ro.recompute_communities()
        with pytest.raises(ValueError, match="embedding model"):
            cm.label(ro, [{"community_id": "cm_x", "label": "x", "summary": "x", "kind": "other"}])


def test_rebuild_keeps_communities_and_their_labels(nb):
    world(nb)
    nb.recompute_communities()
    first = nb.communities()["items"][0]["community_id"]
    cm.label(nb, [{"community_id": first, "label": "Kept", "summary": "Kept.", "kind": "theme"}])
    nb.rebuild()
    items = {c["community_id"]: c for c in nb.communities()["items"]}
    assert items[first]["label"] == "Kept" and len(items) == 2
    assert nb.store.q1("SELECT COUNT(*) FROM entity_communities")[0] == 8


def test_a_retracted_statement_links_nothing(nb):
    world(nb)
    assert cm.entity_graph(nb).has_edge("ex:person-dana", "ex:software-gateway")
    nb.retract_claim("cl_bridge", "alice", "never true")
    assert not cm.entity_graph(nb).has_edge("ex:person-dana", "ex:software-gateway")


# --------------------------------------------------------------------------- labelling (item 2)


def test_pending_hands_the_model_the_top_entities_the_strongest_statements_and_notes(nb, monkeypatch):
    monkeypatch.setattr(cm, "PENDING_CLAIMS", 100)
    w = world(nb)
    nb.recompute_communities()
    got = cm.pending(nb, limit=5)
    assert got["count"] == 2 and [i["index"] for i in got["items"]] == [0, 1]
    by_first = {i["top_entities"][0]["entity_id"]: i for i in got["items"]}
    item = [i for i in got["items"] if any(e["entity_id"] == "ex:project-lut" for e in i["top_entities"])][0]
    assert set(item) == {"index", "community_id", "size", "stale_label", "previous_label", "previous_summary",
                         "top_entities", "claims", "excerpts"}
    assert {e["entity_id"] for e in item["top_entities"]} == set(A)
    assert set(item["top_entities"][0]) == {"entity_id", "name", "kind", "note_count", "summary"}
    assert len(item["claims"]) == 12 and all(c["text"] and c["claim_id"] for c in item["claims"])
    assert "cl_bridge" not in {c["claim_id"] for c in item["claims"]}  # Dana -> gateway leaves the community
    assert [n["note_id"] for n in item["excerpts"]] == [w["n1"]]
    assert by_first and item["previous_label"] is None
    cm.label(nb, [{"community_id": i["community_id"], "label": "x", "summary": "y", "kind": "theme"}
                  for i in got["items"]])
    assert cm.pending(nb)["items"] == []  # every community is named


def test_label_keeps_four_words_and_refuses_what_is_not_there(nb):
    world(nb)
    nb.recompute_communities()
    cid = nb.communities()["items"][0]["community_id"]
    res = cm.label(nb, [
        {"community_id": cid, "label": "The   framework software and its people", "summary": " Tools  of it. ",
         "kind": "galaxy"},
        {"community_id": "cm_nothere", "label": "x", "summary": "y", "kind": "other"},
        {"community_id": cid, "label": "  ", "summary": "y", "kind": "other"},
        "nonsense",
    ])
    assert res["labelled"] == [cid] and len(res["warnings"]) == 5
    item = {c["community_id"]: c for c in nb.communities()["items"]}[cid]
    assert (item["label"], item["summary"], item["kind"]) == ("The framework software and", "Tools of it.", "other")
    assert item["labelled_at"]
    with pytest.raises(ValueError):
        cm.label(nb, "not a list")


def test_graph_nodes_and_entity_cards_carry_the_community(nb):
    world(nb)
    nodes = {n["id"]: n for n in nb.graph()["nodes"]}
    assert nodes["ex:project-lut"]["community_id"] is None  # never computed
    assert nb.entity("ex:project-lut")["community"] is None
    nb.recompute_communities()
    (lut,) = [c for c, m in _groups(nb).items() if "ex:project-lut" in m]
    cm.label(nb, [{"community_id": lut, "label": "LUT", "summary": "s.", "kind": "project"}])
    nodes = {n["id"]: n for n in nb.graph()["nodes"]}
    assert nodes["ex:project-lut"]["community_id"] == lut and nodes["ex:person-dana"]["community_id"] == lut
    assert nodes["ex:thing-kite"]["community_id"] is None  # a loose end
    assert nb.entity("ex:person-ben")["community"] == {"community_id": lut, "label": "LUT", "kind": "project",
                                                       "size": 4, "stale_label": False}
    assert nb.entity("ex:place-paris")["community"] is None


def test_the_community_tools_are_bound(tmp_path):
    from abstractnote.tools import TOOL_NAMES, bind, bound_notebook

    tools = bind(tmp_path / "nb")
    for name in ("note_communities_recompute", "note_communities_pending", "note_communities_label"):
        assert name in TOOL_NAMES and tools[name]._tool_definition.name == name
    world(bound_notebook(tools))
    counts = tools["note_communities_recompute"]()
    assert counts["communities"] == 2
    pending = tools["note_communities_pending"](limit=1)
    assert pending["count"] == 1
    cid = pending["items"][0]["community_id"]
    out = tools["note_communities_label"](items=[{"community_id": cid, "label": "Software", "summary": "s.",
                                                  "kind": "tooling"}])
    assert out == {"labelled": [cid], "warnings": []}
    assert tools["note_communities_pending"]()["count"] == 1


# --------------------------------------------------------------------------- networkx version (gate 04 M1)


def _set_meta_networkx(nb, version):
    meta = nb.store.meta_get("communities")
    if version is None:
        meta.pop("networkx", None)
    else:
        meta["networkx"] = version
    nb.store.meta_set("communities", meta)


def _grow_lut(nb, note_id, names, day):
    ops = []
    for name in names:
        eid = f"ex:person-{name}"
        ops += [create(eid, name.title()), mention(eid, note_id)]
        ops += [claim(f"cl_{name}_{i}", eid, "ex:works_with", m, at=t(day), note_id=note_id) for i, m in enumerate(A)]
    nb.apply_revision(rev(f"rv_grow_{day}", t(day), *ops, note_id=note_id))


def test_the_networkx_version_is_recorded_and_reported(nb):
    import networkx

    world(nb)
    counts = nb.recompute_communities()
    assert counts["networkx"] == networkx.__version__ and counts["carry_over"] is None
    assert nb.store.meta_get("communities")["networkx"] == networkx.__version__
    assert nb.communities()["networkx"] == networkx.__version__


def test_a_networkx_change_carries_labels_over_once_and_never_drops_one_silently(nb):
    w = world(nb)
    nb.recompute_communities()
    (lut,) = [c for c, m in _groups(nb).items() if "ex:project-lut" in m]
    cm.label(nb, [{"community_id": lut, "label": "LUT", "summary": "The LUT project.", "kind": "project"}])
    _grow_lut(nb, w["n1"], ("fay", "gus", "hal"), 6)  # 7 members, Jaccard 4/7 = 0.57: below 0.6, above 0.5
    _set_meta_networkx(nb, "3.5.0")  # the last computation ran under another networkx
    counts = nb.recompute_communities()
    item = {c["community_id"]: c for c in nb.communities()["items"]}.get(lut)
    assert item is not None and (item["label"], item["stale_label"], item["size"]) == ("LUT", True, 7)
    carry = counts["carry_over"]
    assert (carry["from_networkx"], carry["floor"]) == ("3.5.0", cm.KEEP_JACCARD_VERSION_CHANGE)
    assert [(k["community_id"], k["label"]) for k in carry["kept"]] == [(lut, "LUT")]
    assert carry["kept"][0]["jaccard"] == pytest.approx(4 / 7, abs=1e-3)
    assert carry["stale"] == [] and carry["dropped"] == []
    # once: the next computation runs under the recorded version again, with the usual floor
    assert nb.recompute_communities()["carry_over"] is None


def test_a_networkx_change_keeps_a_far_label_as_stale_rather_than_losing_it(nb):
    w = world(nb)
    nb.recompute_communities()
    (lut,) = [c for c, m in _groups(nb).items() if "ex:project-lut" in m]
    cm.label(nb, [{"community_id": lut, "label": "LUT", "summary": "The LUT project.", "kind": "project"}])
    _grow_lut(nb, w["n1"], ("fay", "gus", "hal", "ivy", "jo"), 6)  # 9 members, Jaccard 4/9 = 0.44 < 0.5
    _set_meta_networkx(nb, None)  # computed before the version was recorded: treated as a change
    counts = nb.recompute_communities()
    (now,) = [c for c in nb.communities()["items"] if "ex:project-lut" in cm.members(nb, c["community_id"])]
    assert now["community_id"] != lut  # a new id: the group changed too much to keep it
    assert (now["label"], now["stale_label"]) == ("LUT", True)  # but the label follows it, to be renamed
    carry = counts["carry_over"]
    assert carry["from_networkx"] is None and carry["kept"] == []
    assert [(s["community_id"], s["previous_community_id"], s["label"]) for s in carry["stale"]] == [
        (now["community_id"], lut, "LUT")]
    (waiting,) = [i for i in cm.pending(nb)["items"] if i["community_id"] == now["community_id"]]
    assert waiting["previous_label"] == "LUT"


def test_without_a_networkx_change_a_far_label_is_not_carried(nb):
    w = world(nb)
    nb.recompute_communities()
    (lut,) = [c for c, m in _groups(nb).items() if "ex:project-lut" in m]
    cm.label(nb, [{"community_id": lut, "label": "LUT", "summary": "x.", "kind": "project"}])
    _grow_lut(nb, w["n1"], ("fay", "gus", "hal"), 6)
    counts = nb.recompute_communities()
    assert counts["carry_over"] is None
    (now,) = [c for c in nb.communities()["items"] if "ex:project-lut" in cm.members(nb, c["community_id"])]
    assert now["community_id"] != lut and now["label"] is None


def test_networkx_is_pinned_below_the_untested_release():
    import pathlib

    tomllib = pytest.importorskip("tomllib")  # Python 3.11+
    Requirement = pytest.importorskip("packaging.requirements").Requirement

    deps = tomllib.loads((pathlib.Path(__file__).parents[1] / "pyproject.toml").read_text())["project"]["dependencies"]
    (nx_req,) = [Requirement(d) for d in deps if Requirement(d).name == "networkx"]
    assert "3.6.1" in nx_req.specifier and "3.7" not in nx_req.specifier and "3.2" in nx_req.specifier
