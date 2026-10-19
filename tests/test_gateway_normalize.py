"""``POST /note/entities/normalize`` over HTTP: the dry run lists the groups, the call merges them."""

from __future__ import annotations

import pytest

pytest.importorskip("abstractgateway", reason="abstractgateway is not installed in this environment")
pytest.importorskip("fastapi")

from test_gateway_router import _plane, gw  # noqa: E402,F401  (the scratch-gateway fixture)


def test_normalize_endpoint_lists_then_merges(gw):
    from test_entity_names import _legacy_world

    from abstractnote.gateway.router import notebook_for

    c, h = gw["client"], gw["alice"]
    _legacy_world(notebook_for(_plane(gw, "alice")))
    dry = c.post("/api/gateway/note/entities/normalize", headers=h, json={"dry_run": True})
    assert dry.status_code == 200, dry.text
    assert dry.json()["merges"] == [] and [g["into_id"] for g in dry.json()["groups"]] == ["ex:concept-memory"]
    names = lambda: sorted(e["name"] for e in c.get("/api/gateway/note/entities", headers=h).json()["items"])  # noqa
    assert names().count("memory") == 3  # Memory, memory and MEMORY. before the repair
    done = c.post("/api/gateway/note/entities/normalize", headers=h)
    assert done.status_code == 200, done.text
    assert len(done.json()["merges"]) == 2
    assert names() == ["abstractobserver", "acme", "memory", "observer"]
    items = c.get("/api/gateway/note/entities", headers=h).json()["items"]
    memory = next(e for e in items if e["entity_id"] == "ex:concept-memory")
    assert memory["display_name"] == "Memory"
    assert c.post("/api/gateway/note/entities/normalize", headers=h, json={"dry": True}).status_code in (400, 422)
