"""INT2 seam (W27): ``refile --missing`` through the gateway. ``POST /note/refile {missing: true}`` opens the repair
(202, the pinned ``refile`` row), starts one ``note-file`` run per note on the caller's own runtime (the served bundle
version, the processor's tool list) and follows it; ``GET /note/refile`` reads the row. One repair at a time (409);
``missing`` must be true (400). The scratch gateway runs no runner (``ABSTRACTGATEWAY_RUNNER=0``), so the started run
never ends: the note is skipped with the time-budget sentence and the rest is not reached, which is the shape a
slow model gives. MUTATIONS: start the run under another flow or without the note id; answer before the row is
open; drop the 409 guard; drop the skip on a run that did not end."""

from __future__ import annotations

import time

import pytest

pytest.importorskip("abstractgateway", reason="abstractgateway is not installed in this environment")
pytest.importorskip("fastapi")

from test_filing_imports import _understood_without_kind  # noqa: E402
from test_gateway_router import _plane, gw  # noqa: E402,F401  (the scratch-gateway fixture)
from test_processor_repair import _svc  # noqa: E402

URL = "/api/gateway/note/refile"
ROW_KEYS = {"refile_id", "started_at", "finished_at", "status", "total", "done", "filed", "skipped", "kinds",
            "not_reached", "max_items", "budget_s", "seconds", "model", "notice"}


def test_refile_missing_through_the_gateway_starts_note_file_runs_and_reports_the_row(gw, monkeypatch):
    from abstractnote.gateway import refile as gw_refile
    from abstractnote.gateway.router import notebook_for

    c, h = gw["client"], gw["alice"]
    nb = notebook_for(_plane(gw, "alice"))
    notes = _understood_without_kind(nb, 2)
    assert c.get(URL, headers=h).json() == {"refile": None, "missing": 2}
    bad = c.post(URL, headers=h, json={"missing": False})
    assert bad.status_code == 400 and "missing" in bad.json()["detail"]
    assert c.post(URL, headers=h, json={"missing": True, "colour": "red"}).status_code == 422

    monkeypatch.setattr(gw_refile, "POLL_S", 0.05)
    r = c.post(URL, headers=h, json={"missing": True, "max_items": 1, "budget_s": 1.5})
    assert r.status_code == 202, r.text
    row = r.json()["refile"]
    assert set(row) == ROW_KEYS and row["status"] == "running" and row["total"] == 2 and row["max_items"] == 1
    busy = c.post(URL, headers=h, json={"missing": True})
    assert busy.status_code == 409 and "already" in busy.json()["detail"]

    got = _ended(c, h)
    done = got["refile"]
    assert done["refile_id"] == row["refile_id"] and done["status"] == "completed_with_gaps"
    assert [s["note_id"] for s in done["skipped"]] == [notes[0]] and "time given" in done["skipped"][0]["reason"]
    assert done["filed"] == [] and done["done"] == 1 and done["not_reached"] == 1 and got["missing"] == 2
    assert done["notice"] == ("2 notes are still without a kind (1 could not be filed and 1 were not reached this"
                              " time); run it again to file them.")

    from abstractnote import __version__

    runs = [s for s in _svc(gw).host.run_store.list_runs(limit=50) if str(s.workflow_id).endswith(":note-file")]
    assert len(runs) == 1 and runs[0].workflow_id == f"abstractnote@{__version__}:note-file"
    assert runs[0].vars.get("note_id") == notes[0]
    assert "note_filing_context" in (runs[0].vars.get("_runtime") or {}).get("allowed_tools", [])
    again = c.post(URL, headers=h, json={"missing": True, "max_items": 1, "budget_s": 0.2})
    assert again.status_code == 202  # the first one ended: a new repair may start
    assert _ended(c, h)["refile"]["refile_id"] == again.json()["refile"]["refile_id"]


def _ended(c, h):
    deadline = time.monotonic() + 20
    while True:
        got = c.get(URL, headers=h).json()
        if got["refile"]["status"] != "running" or time.monotonic() > deadline:
            return got
        time.sleep(0.1)
