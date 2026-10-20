"""Communities and statistics over HTTP (backlog 0030): GET /note/communities, POST /note/communities/recompute,
community_id on graph nodes, community on the entity card, GET /note/stats."""

from __future__ import annotations

import pytest

pytest.importorskip("abstractgateway", reason="abstractgateway is not installed in this environment")
pytest.importorskip("fastapi")

from test_gateway_router import _plane, gw  # noqa: E402,F401  (the scratch-gateway fixture)


def test_communities_over_http(gw):
    from test_communities import world

    from abstractnote.gateway.router import notebook_for

    c, h = gw["client"], gw["alice"]
    empty = c.get("/api/gateway/note/communities", headers=h)
    assert empty.status_code == 200, empty.text
    assert empty.json()["items"] == [] and empty.json()["computed_at"] is None
    world(notebook_for(_plane(gw, "alice")))
    r = c.post("/api/gateway/note/communities/recompute", headers=h)
    assert r.status_code == 200, r.text
    assert (r.json()["communities"], r.json()["loose_ends"]) == (2, 3)
    items = c.get("/api/gateway/note/communities", headers=h).json()["items"]
    assert len(items) == 2
    assert set(items[0]) == {"community_id", "label", "summary", "kind", "size", "top_entities", "stale_label",
                             "labelled_at", "created_at"}
    assert set(items[0]["top_entities"][0]) == {"entity_id", "name", "kind", "note_count"}
    lut = [i for i in items if "ex:project-lut" in {e["entity_id"] for e in i["top_entities"]}][0]
    nodes = {n["id"]: n for n in c.get("/api/gateway/note/graph", headers=h).json()["nodes"]}
    assert nodes["ex:project-lut"]["community_id"] == lut["community_id"]
    card = c.get("/api/gateway/note/entities/ex:person-dana", headers=h).json()
    assert card["community"]["community_id"] == lut["community_id"]
    # bad input: 400; another account sees its own (empty) notebook
    bad = c.post("/api/gateway/note/communities/recompute", headers=h, json={"resolution": -1})
    assert bad.status_code == 400 and "resolution" in bad.json()["detail"]
    assert c.post("/api/gateway/note/communities/recompute", headers=h, json={"client": "x"}).status_code == 422
    assert c.get("/api/gateway/note/communities", headers=gw["bob"]).json()["items"] == []
    assert c.get("/api/gateway/note/communities").status_code == 401


def test_stats_over_http(gw):
    from test_stats import stats_world

    from abstractnote.gateway.router import notebook_for

    c, h = gw["client"], gw["alice"]
    empty = c.get("/api/gateway/note/stats", headers=h)
    assert empty.status_code == 200, empty.text
    assert empty.json()["totals"]["notes"] == 0
    stats_world(notebook_for(_plane(gw, "alice")))
    body = c.get("/api/gateway/note/stats", headers=h).json()
    assert set(body) == {"generated_at", "span", "totals", "activity_weekly", "entities_by_kind", "top_entities",
                         "claims_by_predicate", "tasks", "insights_by_kind", "communities", "processing",
                         "growth_monthly", "quiet_entities", "omitted"}
    assert body["omitted"] == {"top_entities": 0, "claims_by_predicate": 0, "quiet_entities": 0}
    assert body["totals"]["notes"] == 5 and body["tasks"]["open"] == 1
    assert c.get("/api/gateway/note/stats", headers=gw["bob"]).json()["totals"]["notes"] == 0
    assert c.get("/api/gateway/note/stats").status_code == 401
