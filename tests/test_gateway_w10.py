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
