"""The consolidation automation in the gateway (backlog 0032): ensure creates and repairs the third automation,
the ``consolidate_schedule`` setting re-creates it (``off`` = paused), ``GET /note/status`` lists it, and
``GET /note/consolidation`` / ``GET /note/processing`` serve the pinned ``consolidation`` object."""

from __future__ import annotations

import pytest

pytest.importorskip("abstractgateway", reason="abstractgateway is not installed in this environment")
pytest.importorskip("fastapi")

from test_gateway_router import _plane, gw  # noqa: E402,F401  (the scratch-gateway fixture)
from test_processor_repair import _apply_pending, _commands, _definition, _svc  # noqa: E402

ENSURE = "/api/gateway/note/processor/ensure"
SETTINGS = "/api/gateway/note/settings"


def _put(c, h, every):
    r = c.put(SETTINGS, headers=h, json={"consolidate_schedule": {"every": every}})
    assert r.status_code == 200, r.text
    return r.json()


def test_ensure_creates_the_consolidation_one_interval_later_and_status_lists_it(gw):
    from datetime import datetime, timedelta, timezone

    from abstractnote import __version__

    c, h = gw["client"], gw["alice"]
    before = datetime.now(timezone.utc)
    out = c.post(ENSURE, headers=h).json()
    assert out["consolidate"]["action"] == "created" and out["consolidate_id"] == out["consolidate"]["id"]
    d = _definition(c, h, out["consolidate_id"])
    assert d["target"]["workflow_id"] == f"abstractnote@{__version__}:note-consolidate"
    assert d["trigger"]["config"]["every"] == "1d"
    first = datetime.fromisoformat(d["trigger"]["config"]["start_at"].replace("Z", "+00:00"))
    assert first >= before + timedelta(days=1) - timedelta(minutes=1)  # never a model call on opening
    assert d["target"]["input_data"]["budget_s"] == 3600
    status = c.get("/api/gateway/note/status", headers=h).json()["automations"]
    assert status["consolidate_id"] == out["consolidate_id"]
    assert status["consolidate"]["healthy"] is True and status["consolidate"]["schedule"] == {"every": "1d"}
    again = c.post(ENSURE, headers=h).json()
    assert again["consolidate"]["action"] == "kept" and "note-consolidate" not in again["created"]


def test_a_schedule_change_recreates_it_and_off_pauses_it(gw):
    c, h = gw["client"], gw["alice"]
    svc = _svc(gw)
    first = c.post(ENSURE, headers=h).json()["consolidate_id"]
    assert _put(c, h, "12h")["consolidate_schedule"] == {"every": "12h"}
    second = c.get("/api/gateway/note/status", headers=h).json()["automations"]["consolidate_id"]
    assert second != first and "automation.archive" in _commands(svc, first)
    assert _definition(c, h, second)["trigger"]["config"]["every"] == "12h"
    assert c.get("/api/gateway/note/consolidation", headers=h).json()["automation"]["schedule"] == {"every": "12h"}

    _put(c, h, "off")
    status = c.get("/api/gateway/note/status", headers=h).json()["automations"]
    assert status["consolidate_id"] == second  # same interval: kept, and paused
    assert "automation.pause" in _commands(svc, second)
    _apply_pending(svc, second)
    view = c.get("/api/gateway/note/consolidation", headers=h).json()
    assert view["automation"]["paused"] is True and view["automation"]["schedule"] == {"every": "off"}
    assert c.post(ENSURE, headers=h).json()["consolidate"]["action"] == "kept"  # off and paused is healthy

    _put(c, h, "12h")
    assert "automation.resume" in _commands(svc, second)
    _apply_pending(svc, second)
    assert c.get("/api/gateway/note/consolidation", headers=h).json()["automation"]["paused"] is False
    bad = c.put(SETTINGS, headers=h, json={"consolidate_schedule": {"every": "5m"}})
    assert bad.status_code == 400 and "hours or days" in bad.json()["detail"]


def test_a_user_pause_survives_a_repair(gw):
    c, h = gw["client"], gw["alice"]
    svc = _svc(gw)
    first = c.post(ENSURE, headers=h).json()["consolidate_id"]
    c.post(f"/api/gateway/automations/{first}/commands", headers=h,
           json={"command_id": "user-pause", "type": "automation.pause"})
    _apply_pending(svc, first)
    _put(c, h, "7d")  # a new interval: recreated, and paused again (the user paused it)
    second = c.get("/api/gateway/note/status", headers=h).json()["automations"]["consolidate_id"]
    assert second != first and "automation.pause" in _commands(svc, second)
    assert "automation.resume" not in _commands(svc, second)


def test_the_consolidation_view_on_the_page_and_its_own_route(gw):
    from abstractnote.gateway.router import notebook_for
    from abstractnote.recall import consolidation as cons

    c, h = gw["client"], gw["alice"]
    c.post(ENSURE, headers=h)
    nb = notebook_for(_plane(gw, "alice"))
    view = c.get("/api/gateway/note/consolidation", headers=h).json()
    assert set(view) == {"automation", "run", "last", "pending"}
    assert set(view["automation"]) == {"id", "state", "paused", "healthy", "reason", "schedule"}
    assert view["run"] is None and view["last"] is None
    assert view["pending"] == {"stale_labels": 0, "unlabelled": 0, "stale_summaries": 0}
    cons.begin(nb)
    cons.progress(nb, "labels", 1, 4)
    page = c.get("/api/gateway/note/processing", headers=h).json()
    run = page["consolidation"]["run"]
    assert set(run) == {"run_id", "started_at", "stage", "stage_done", "stage_total", "eta_s", "elapsed_s"}
    assert (run["stage"], run["stage_done"], run["stage_total"], run["run_id"]) == ("labels", 1, 4, None)
    assert isinstance(run["eta_s"], int)
    cons.end(nb, "completed", {"communities": 2, "insights": 1})
    last = c.get("/api/gateway/note/consolidation", headers=h).json()["last"]
    assert last["status"] == "completed" and (last["labelled"], last["communities"], last["insights"]) == (1, 2, 1)
    assert {"finished_at", "status", "merges", "communities", "labelled", "summaries_refreshed",
            "insights"} <= set(last)
