"""W10 server gaps over HTTP: processor throughput on the queue, the review list and its confirm action,
merged entries readable as themselves, cue evidence, and the archive snapshot axis (``as_received``)."""

from __future__ import annotations

import pytest

pytest.importorskip("abstractgateway", reason="abstractgateway is not installed in this environment")
pytest.importorskip("fastapi")

from test_gateway_router import _plane, gw  # noqa: E402,F401  (the scratch-gateway fixture)


def test_the_queue_reports_the_processor_throughput_from_the_settings(gw):
    c, h = gw["client"], gw["alice"]
    q = c.get("/api/gateway/note/queue", headers=h)
    assert q.status_code == 200, q.text
    assert q.json()["processor"] == {"max_items": 20, "interval": "2m"}
    r = c.put("/api/gateway/note/settings", headers=h, json={"processor_schedule": {"every": "5m", "max_items": 7}})
    assert r.status_code == 200 and r.json()["processor_schedule"] == {"every": "5m", "max_items": 7}
    assert c.get("/api/gateway/note/queue", headers=h).json()["processor"] == {"max_items": 7, "interval": "5m"}
    bad = c.put("/api/gateway/note/settings", headers=h, json={"processor_schedule": {"every": "soon", "max_items": 7}})
    assert bad.status_code == 400 and "interval" in bad.json()["detail"]
    ids = c.post("/api/gateway/note/processor/ensure", headers=h).json()
    definition = c.get(f"/api/gateway/automations/{ids['processor_id']}", headers=h).json()["definition"]
    assert definition["trigger"]["config"]["every"] == "5m"
    assert definition["target"]["input_data"]["max_items"] == 7
    # a later change of the setting does not change the automation already made: the queue says what runs
    c.put("/api/gateway/note/settings", headers=h, json={"processor_schedule": {"every": "10m", "max_items": 3}})
    assert c.get("/api/gateway/note/queue", headers=h).json()["processor"] == {"max_items": 7, "interval": "5m"}


def test_merged_entries_list_and_read_as_themselves(gw):
    from test_review_and_archive import merged_world

    from abstractnote.gateway.router import notebook_for

    c, h = gw["client"], gw["alice"]
    merged_world(notebook_for(_plane(gw, "alice")))
    ids = lambda r: {e["entity_id"] for e in r.json()["items"]}  # noqa: E731
    assert "ex:person-d-ng" not in ids(c.get("/api/gateway/note/entities", headers=h))
    assert "ex:person-d-ng" in ids(c.get("/api/gateway/note/entities", params={"include_merged": "true"}, headers=h))
    followed = c.get("/api/gateway/note/entities/ex:person-d-ng", headers=h).json()
    assert followed["entity_id"] == "ex:person-dana" and followed["resolved_from"] == "ex:person-d-ng"
    own = c.get("/api/gateway/note/entities/ex:person-d-ng", params={"as_merged": "true"}, headers=h)
    assert own.status_code == 200, own.text
    assert (own.json()["name"], own.json()["merged_into"], own.json()["merge"]["revision_id"]) == (
        "D. Ng", "ex:person-dana", "rv_w3")
    both = c.get("/api/gateway/note/entities/ex:person-d-ng", params={"as_merged": "true", "at": "2026-01-01"},
                 headers=h)
    assert both.status_code == 400
    assert c.get("/api/gateway/note/entities/ex:nobody", params={"as_merged": "true"}, headers=h).status_code == 404


def test_the_review_list_and_confirm_over_http(gw):
    from test_review_and_archive import review_world

    from abstractnote.gateway.router import notebook_for

    c, h = gw["client"], gw["alice"]
    nb = notebook_for(_plane(gw, "alice"))
    review_world(nb)
    r = c.get("/api/gateway/note/review", headers=h)
    assert r.status_code == 200, r.text
    assert set(r.json()) == {"items", "next_cursor"} and r.json()["next_cursor"] is None
    items = {i["kind"]: i for i in r.json()["items"]}
    assert set(items) == {"close_match", "merge", "contradiction"}
    assert c.get("/api/gateway/note/review", headers=gw["bob"]).json()["items"] == []  # bob's own notebook
    cm = items["close_match"]
    assert c.post(f"/api/gateway/note/review/{cm['review_id']}/confirm", headers=h, json={"client": "watch"}
                  ).status_code == 422
    ok = c.post(f"/api/gateway/note/review/{cm['review_id']}/confirm",
                headers={**h, "X-AbstractFramework-App-Proxy": "abstractnote"})
    assert ok.status_code == 200, ok.text
    assert nb.revision(ok.json()["revision_id"])["source"] == {"client": "web", "account": "alice"}
    again = c.post(f"/api/gateway/note/review/{cm['review_id']}/confirm", headers=h, json={})
    assert again.status_code == 404 and again.json()["detail"] == "That match is not waiting for review."
    ct = items["contradiction"]
    refused = c.post(f"/api/gateway/note/review/{ct['review_id']}/confirm", headers=h, json={})
    assert refused.status_code == 400
    mg = items["merge"]
    undo = c.post(mg["actions"][1]["path"].replace("/note/", "/api/gateway/note/", 1), headers=h,
                  json=mg["actions"][1]["body"])
    assert undo.status_code == 200, undo.text
    assert {i["kind"] for i in c.get("/api/gateway/note/review", headers=h).json()["items"]} == {"contradiction"}
