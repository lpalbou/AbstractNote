"""GET /note/processing, POST /note/queue/{id}/priority, Notebook.processing / set_priority (backlog 0031).

Every number is read from the store (SQL) or the plane's runs and ledger; the active-run test drives a real
note-drain -> note-process run (scripted model, recording tools) over the scratch gateway's own run store and
ledger and stops it between two sections.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone

import pytest

from test_store_support import create, nb, rev, t  # noqa: F401

NOW = "2026-10-20T10:02:30Z"


def _at(minutes_ago: float) -> str:
    base = datetime(2026, 10, 20, 10, 2, 30, tzinfo=timezone.utc)
    return (base - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _processed(nb, text: str, minutes_ago: float, *, rid: str):
    """A captured note understood ``minutes_ago`` before NOW (one processing revision, recorded then)."""
    note_id = nb.capture(text, occurred_at=t(5), source={"client": "cli"}).note_id
    nb.apply_revision(rev(rid, t(5), create(f"ex:person-{rid}", f"P {rid}"),
                          {"op": "entity.mention", "payload": {"entity_id": f"ex:person-{rid}", "note_id": note_id}},
                          note_id=note_id, summary=f"Understood {rid}"))
    nb.store.conn.execute("UPDATE revisions SET recorded_at=? WHERE revision_id=?", (_at(minutes_ago), rid))
    nb.store.conn.execute("UPDATE queue SET state='done' WHERE note_id=?", (note_id,))
    return note_id


# --------------------------------------------------------------------------- notebook part (SQL)


def test_pace_series_eta_recent_and_failed_come_from_the_store(nb):
    for i, ago in enumerate((1, 2, 3, 30, 59, 61, 125)):
        _processed(nb, f"Note {i} about Dana.", ago, rid=f"rv_{i}")
    waiting = [nb.capture(f"Waiting {i}.", occurred_at=t(6), source={"client": "cli"}).note_id for i in range(10)]
    nb.store.conn.execute("UPDATE queue SET state='failed', last_error='The model did not answer.', failures=3,"
                          " attempts=3, failed_at=? WHERE note_id=?", (_at(4), waiting[0]))
    # a user action is not processing: it never counts as understood
    nb.store.conn.execute("UPDATE revisions SET action='claim.retract' WHERE revision_id='rv_2'")

    out = nb.processing(now=NOW)
    pace = out["pace"]
    assert pace["per_minute_5m"] == round(2 / 5, 2)          # rv_0, rv_1 (rv_2 is a user action)
    assert pace["per_minute_1h"] == round(4 / 60, 3)         # + rv_3, rv_4; rv_5 (61 min) is outside
    series = pace["series"]
    assert len(series) == 24 and series[-1]["t"] == "2026-10-20T10:00:00Z" and series[0]["t"] == "2026-10-20T08:05:00Z"
    assert sum(b["understood"] for b in series) == 5         # rv_6 (125 min) is older than the two hours
    assert series[-1]["understood"] == 2                     # 10:01:30 and 10:00:30 in the current bucket
    q = out["queue"]
    assert (q["queued"], q["failed"], q["done"]) == (9, 1, 7)
    assert q["eta_s"] == int(round(9 / (4 / 60) * 60))
    assert [r["revision_id"] for r in out["recent"]][:3] == ["rv_0", "rv_1", "rv_3"]
    first = out["recent"][0]
    assert first["entities_created"] == 1 and first["claims_added"] == 0 and first["duration_s"] is None
    assert set(first["note"]) == {"note_id", "title", "kind", "occurred_at", "excerpt"}
    assert out["failed"] == [{"note_id": waiting[0], "title": "", "error": "The model did not answer.",
                              "attempts": 3, "next_attempt_at": None, "failed_at": _at(4)}]
    assert out["active"] == [] and out["processor"]["schedule"] == {"every": "2m", "max_items": 20}
    assert out["model"] == {"provider": None, "model": None, "source": None, "thinking_bulk": "off",
                            "thinking_live": "on"}
    assert out["reflect"]["communities"] == {"count": 0, "unlabelled": 0, "stale_label": 0, "computed_at": None}


def test_eta_is_null_without_pace_and_the_notebook_model_pin_wins(nb):
    nb.capture("Waiting.", occurred_at=t(6), source={"client": "cli"})
    assert nb.processing(now=NOW)["queue"]["eta_s"] is None
    nb.update_settings({"filing_model": {"provider": "lmstudio", "model": "qwen3-4b"}})
    model = nb.processing(now=NOW)["model"]
    assert (model["provider"], model["model"], model["source"]) == ("lmstudio", "qwen3-4b", "notebook")


def test_set_priority_moves_a_waiting_note_and_records_no_revision(nb):
    a = nb.capture("First.", occurred_at=t(5), source={"client": "cli"}).note_id
    b = nb.capture("Second.", occurred_at=t(6), source={"client": "cli"}).note_id
    before = nb.store.q1("SELECT COUNT(*) FROM revisions")[0]
    assert nb.set_priority(b, 3) == {"note_id": b, "priority": 3}
    assert nb.queue.lease("w1")["items"][0]["note_id"] == b          # front of the line
    assert nb.store.q1("SELECT COUNT(*) FROM revisions")[0] == before
    assert nb.store.note_row(b)["priority_explicit"] == 1
    for bad in (4, -1, True, "3", 1.5):
        with pytest.raises(ValueError, match="0 to 3"):
            nb.set_priority(a, bad)
    with pytest.raises(ValueError, match="waiting"):
        nb.set_priority(b, 1)                                        # leased: being processed
    assert nb.processing(now=NOW)["queue"]["by_priority"]["3"] == 1


# --------------------------------------------------------------------------- the step table and the ledger fold


def test_every_effect_node_of_the_note_flows_has_a_place_in_the_step_table():
    from abstractnote.flows.runner import load_flow_documents
    from abstractnote.recall.processing import STEP_OF_NODE

    docs = load_flow_documents()
    assert set(docs) == set(STEP_OF_NODE)
    for flow_id, doc in docs.items():
        effect_nodes = {n["id"] for n in doc["nodes"]
                        if n.get("data", {}).get("nodeType") in ("llm_call", "tool_calls", "subflow")}
        assert effect_nodes, flow_id
        assert effect_nodes == set(STEP_OF_NODE[flow_id]), flow_id


def test_flow_of_reads_only_note_workflows():
    from abstractnote.recall.processing import flow_of

    assert flow_of("abstractnote@0.1.1:note-drain") == "note-drain"
    assert flow_of("note-process") == "note-process"
    assert flow_of("other@1.0:note-drain") is None
    assert flow_of("abstractnote@0.1.1:basic-agent") is None
    assert flow_of(None) is None


# --------------------------------------------------------------------------- in-process gateway

pytest.importorskip("fastapi")
try:  # the gateway checkout is optional for the notebook tests above
    import abstractgateway  # noqa: F401
    from test_gateway_router import _plane, gw  # noqa: F401
except ImportError:  # pragma: no cover
    gw = None

needs_gateway = pytest.mark.skipif(gw is None, reason="abstractgateway is not installed in this environment")
PROCESSING = "/api/gateway/note/processing"


def _svc(gw, who="alice"):
    from abstractgateway.service import get_gateway_service_for_principal

    principal = gw["registry"].authenticate(gw[who]["Authorization"].split(" ", 1)[1])
    return get_gateway_service_for_principal(principal)


@needs_gateway
def test_processing_answers_with_the_pinned_keys_and_an_etag(gw):
    c, h = gw["client"], gw["alice"]
    assert c.get(PROCESSING).status_code == 401
    r = c.get(PROCESSING, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) >= {"now", "processor", "reflect", "queue", "pace", "active", "recent", "failed", "model",
                         "consolidation"}
    assert set(body["processor"]) >= {"id", "state", "paused", "healthy", "reason", "schedule", "target"}
    assert set(body["reflect"]) >= {"id", "state", "paused", "healthy", "reason", "last_run_at", "last_status",
                                    "insights_last_run", "communities"}
    assert set(body["queue"]) >= {"queued", "processing", "failed", "done", "by_priority", "oldest_enqueued_at",
                                  "eta_s"}
    assert body["processor"]["state"] == "missing" and body["active"] == []
    etag = r.headers["etag"]
    again = c.get(PROCESSING, headers={**h, "If-None-Match": etag})
    assert again.status_code == 304 and again.headers["etag"] == etag and again.content == b""
    from abstractnote.gateway.router import notebook_for

    notebook_for(_plane(gw, "alice")).capture("A new note.", source={"client": "cli"})
    changed = c.get(PROCESSING, headers={**h, "If-None-Match": etag})
    assert changed.status_code == 200 and changed.headers["etag"] != etag
    assert changed.json()["queue"]["queued"] == 1


@needs_gateway
def test_consolidation_is_null_without_its_module_and_reported_when_it_fails(gw, monkeypatch):
    import importlib.util
    import sys
    import types

    from abstractnote.gateway import router as rt

    c, h = gw["client"], gw["alice"]
    monkeypatch.setattr(rt, "CONSOLIDATION_MODULE", "abstractnote.gateway.no_such_module")
    assert c.get(PROCESSING, headers=h).json()["consolidation"] is None
    fake = types.ModuleType("abstractnote_fake_consolidation")
    fake.consolidation_view = lambda nb, svc, principal: {"pending": {"stale_labels": 0}}
    monkeypatch.setitem(sys.modules, fake.__name__, fake)
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(rt, "CONSOLIDATION_MODULE", fake.__name__)
    assert c.get(PROCESSING, headers=h).json()["consolidation"] == {"pending": {"stale_labels": 0}}

    def boom(nb, svc, principal):
        raise RuntimeError("The consolidation table is missing.")

    fake.consolidation_view = boom
    r = c.get(PROCESSING, headers=h)
    assert r.status_code == 200 and r.json()["consolidation"] is None
    assert r.json()["consolidation_error"] == "The consolidation table is missing."


@needs_gateway
def test_priority_action_validates_and_is_isolated(gw):
    from abstractnote.gateway.router import notebook_for

    c, h = gw["client"], gw["alice"]
    note_id = notebook_for(_plane(gw, "alice")).capture("Move me.", source={"client": "cli"}).note_id
    url = f"/api/gateway/note/queue/{note_id}/priority"
    assert c.post(url, json={"priority": 3}).status_code == 401
    other = c.post(url, headers=gw["bob"], json={"priority": 3})
    assert other.status_code == 404 and other.json()["detail"] == "That note is not in your notebook."
    assert c.post("/api/gateway/note/queue/nt_nope/priority", headers=h, json={"priority": 3}).status_code == 404
    bad = c.post(url, headers=h, json={"priority": 7})
    assert bad.status_code == 400 and bad.json()["detail"] == "The priority must be a whole number from 0 to 3."
    assert c.post(url, headers=h, json={"priority": "3"}).status_code == 422
    assert c.post(url, headers=h, json={"priority": 3, "client": "x"}).status_code == 422
    ok = c.post(url, headers=h, json={"priority": 3})
    assert ok.status_code == 200 and ok.json() == {"note_id": note_id, "priority": 3}
    assert c.get(PROCESSING, headers=h).json()["queue"]["by_priority"]["3"] == 1
    notebook_for(_plane(gw, "alice")).queue.lease("w1")
    leased = c.post(url, headers=h, json={"priority": 1})
    assert leased.status_code == 400 and "waiting" in leased.json()["detail"]


def _runtime_on(svc, script, tools):
    """A Runtime over the plane's own run store and ledger (the gateway runner is off in the fixture)."""
    from abstractruntime import Runtime
    from abstractruntime.integrations.abstractcore.effect_handlers import build_effect_handlers
    from abstractruntime.integrations.abstractcore.tool_executor import MappingToolExecutor
    from abstractruntime.storage.artifacts import InMemoryArtifactStore

    from abstractnote.flows.runner import ScriptedLLM, build_registry

    rs, ls = svc.host.run_store, svc.host.ledger_store
    registry = build_registry()
    artifacts = InMemoryArtifactStore()
    handlers = build_effect_handlers(llm=ScriptedLLM(script), tools=MappingToolExecutor(tools),
                                     artifact_store=artifacts, run_store=rs)
    return Runtime(run_store=rs, ledger_store=ls, workflow_registry=registry, artifact_store=artifacts,
                   effect_handlers=handlers), registry


@needs_gateway
def test_an_active_drain_shows_its_note_step_and_sections_then_the_revision_duration(gw):
    from abstractruntime.core.models import RunStatus, WaitReason
    from test_flows_headless import EXTRACTION, SCRIPT, Doubles

    from abstractnote.flows.runner import drive_until_done
    from abstractnote.gateway.router import notebook_for

    c, h = gw["client"], gw["alice"]
    svc = _svc(gw)
    nb = notebook_for(_plane(gw, "alice"))
    note_id = nb.capture("Met Dana about the LUT budget; three parts.", occurred_at=t(5),
                         source={"client": "cli"}).note_id
    doubles = Doubles()
    doubles.queue = [note_id]
    tools = doubles.tools()
    tools["note_context"] = lambda **k: {"note": {"note_id": k["note_id"]}, "sections": ["one", "two", "three"],
                                         "similar_notes": [], "candidate_entities": [], "open_tasks": [],
                                         "topics": [], "vocabulary": []}

    def apply(**k):  # a real revision in alice's notebook, recorded while the note-process run is alive
        res = nb.apply_revision(rev("rv_live", t(5), create("ex:person-dana", "Dana"), note_id=k["note_id"],
                                    summary="Linked Dana."))
        return {**res, "warnings": []}

    tools["note_apply_revision"] = apply
    runtime, registry = _runtime_on(svc, dict(SCRIPT, Extraction=[copy.deepcopy(EXTRACTION)] * 3), tools)
    drain_wf = registry.get_or_raise("note-drain")
    drain_id = runtime.start(workflow=drain_wf, vars={"max_items": 1, "budget_s": 60, "thinking": "off"})
    state = runtime.tick(workflow=drain_wf, run_id=drain_id, max_steps=50)
    assert state.status == RunStatus.WAITING and state.waiting.reason == WaitReason.SUBWORKFLOW
    child_id = state.waiting.details["sub_run_id"]
    proc_wf = registry.get_or_raise("note-process")

    def extracts_done() -> int:
        return sum(1 for r in svc.host.ledger_store.list(child_id)
                   if r.get("node_id") == "extract" and str(r.get("status")).endswith("completed"))

    for _ in range(200):  # one node at a time, until two of the three sections are read
        if extracts_done() == 2:
            break
        runtime.tick(workflow=proc_wf, run_id=child_id, max_steps=1)
    assert extracts_done() == 2

    body = c.get(PROCESSING, headers=h).json()
    by_flow = {a["flow"]: a for a in body["active"]}
    assert set(by_flow) == {"note-drain", "note-process"}
    proc, drain = by_flow["note-process"], by_flow["note-drain"]
    assert (proc["run_id"], proc["step"], proc["sections_done"], proc["sections_total"], proc["thinking"]) == (
        child_id, "extract", 2, 3, "off")
    assert proc["note"]["note_id"] == note_id and proc["step_started_at"] and proc["parent_run_id"] == drain_id
    assert (drain["run_id"], drain["child_run_id"], drain["step"], drain["sections_done"]) == (
        drain_id, child_id, "extract", 2)
    assert drain["note"]["note_id"] == note_id
    assert set(proc) >= {"run_id", "flow", "started_at", "note", "step", "step_started_at", "sections_done",
                         "sections_total", "thinking"}
    # Bob's plane has no runs: another account never sees them
    assert c.get(PROCESSING, headers=gw["bob"]).json()["active"] == []

    final = drive_until_done(runtime, drain_id, registry=registry)
    assert final.status == RunStatus.COMPLETED and final.output["processed"] == 1
    body = c.get(PROCESSING, headers=h).json()
    assert body["active"] == []
    child = svc.host.run_store.load(child_id)
    start = datetime.fromisoformat(child.created_at)
    end = datetime.fromisoformat(child.updated_at)
    (recent,) = [r for r in body["recent"] if r["revision_id"] == "rv_live"]
    assert recent["duration_s"] == round((end - start).total_seconds(), 3)
    assert recent["entities_created"] == 1 and recent["note"]["note_id"] == note_id


@needs_gateway
def test_the_last_reflect_run_and_its_insights_come_from_the_plane(gw):
    from abstractruntime.core.models import RunState, RunStatus

    from abstractnote.gateway.router import notebook_for

    svc = _svc(gw)
    nb = notebook_for(_plane(gw, "alice"))
    nb.set_meta("reflect_automation_id", "auto-reflect")
    for index, (status, output) in enumerate(((RunStatus.COMPLETED, {"insight_note_ids": ["a"], "success": True}),
                                              (RunStatus.COMPLETED, {"insight_note_ids": ["a", "b"],
                                                                     "success": True}))):
        run = RunState.new(workflow_id="abstractnote@0.1.1:note-reflect", entry_node="start",
                           vars={"_meta": {"occurrence": {"automation_id": "auto-reflect",
                                                          "occurrence_index": index + 1}}},
                           run_id=f"occ-reflect-{index + 1}")
        run.status, run.output = status, output
        svc.host.run_store.save(run)
    reflect = gw["client"].get(PROCESSING, headers=gw["alice"]).json()["reflect"]
    assert (reflect["last_status"], reflect["insights_last_run"]) == ("completed", 2)
    assert reflect["last_run_at"] == svc.host.run_store.load("occ-reflect-2").created_at
    failed = RunState.new(workflow_id="abstractnote@0.1.1:note-reflect", entry_node="start",
                          vars={"_meta": {"occurrence": {"automation_id": "auto-reflect", "occurrence_index": 3}},
                                "reflect_written": ["x"]}, run_id="occ-reflect-3")
    failed.status, failed.output = RunStatus.COMPLETED, {"success": False, "error": "The model did not answer."}
    svc.host.run_store.save(failed)
    reflect = gw["client"].get(PROCESSING, headers=gw["alice"]).json()["reflect"]
    assert (reflect["last_status"], reflect["insights_last_run"]) == ("failed", 1)
    assert gw["client"].get(PROCESSING, headers=gw["bob"]).json()["reflect"]["last_status"] is None
    json.dumps(reflect)
