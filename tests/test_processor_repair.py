"""The processor and reflect automations survive a note-workflow version change (W14).

An automation freezes ``abstractnote@<version>:<flow>`` at creation; after an upgrade the gateway
serves only the new version, so the old automation's next occurrence fails and the queue never
drains. ``POST /note/processor/ensure`` must notice and recreate it (keeping the paused state),
and ``GET /note/status`` / ``GET /note/queue`` must report it as unhealthy until then.
"""

from __future__ import annotations

import pytest

pytest.importorskip("abstractgateway", reason="abstractgateway is not installed in this environment")
pytest.importorskip("fastapi")

from test_gateway_router import _plane, gw  # noqa: E402,F401  (the scratch-gateway fixture)

ENSURE = "/api/gateway/note/processor/ensure"


# --------------------------------------------------------------------------- unit: the health rule


def _health(**kw):
    from abstractnote.gateway.health import automation_health

    base = dict(automation_id="a1", flow_id="note-drain", bundle_id="abstractnote", served_version="0.1.1",
                state="active", target="abstractnote@0.1.1:note-drain")
    return automation_health(**{**base, **kw})


def test_health_is_true_only_on_the_served_version_and_a_running_state():
    assert _health()["healthy"] is True and _health()["reason"] is None
    assert _health(state="paused", paused=True)["healthy"] is True
    stale = _health(target="abstractnote@0.1.0:note-drain")
    assert stale["healthy"] is False and "abstractnote@0.1.0:note-drain" in stale["reason"]
    for state in ("failed", "completed", "archived"):
        h = _health(state=state)
        assert h["healthy"] is False and state in h["reason"]
    assert _health(state=None)["healthy"] is False and _health(state=None)["state"] == "missing"
    assert _health(automation_id=None)["healthy"] is False
    assert _health(served_version=None)["healthy"] is False


# --------------------------------------------------------------------------- in-process gateway


def _svc(gw, who="alice"):
    from abstractgateway.service import get_gateway_service_for_principal

    principal = gw["registry"].authenticate(gw[who]["Authorization"].split(" ", 1)[1])
    return get_gateway_service_for_principal(principal)


def _definition(c, h, automation_id):
    got = c.get(f"/api/gateway/automations/{automation_id}", headers=h)
    assert got.status_code == 200, got.text
    return got.json()["definition"]


def _commands(svc, automation_id):
    records, _ = svc.runner.command_store.list_after(after=0, limit=10_000)
    return [r.type for r in records if r.run_id == automation_id]


def _apply_pending(svc, automation_id):
    """What the runner does with a queued command (the fixture runs without the runner)."""
    from abstractruntime.automations.commands import apply_automation_command

    records, _ = svc.runner.command_store.list_after(after=0, limit=10_000)
    for r in records:
        if r.run_id == automation_id:
            runtime, _wf = svc.host.runtime_and_workflow_for_run(automation_id)
            apply_automation_command(runtime, automation_id=automation_id, command_id=str(r.command_id), type=r.type,
                                     payload=dict(r.payload or {}), actor=r.client_id)


def _bump_to(monkeypatch, svc, version):
    """An upgrade + restart, in process: the installed version changes and the plane reloads its workflows."""
    import abstractnote

    monkeypatch.setattr(abstractnote, "__version__", version)
    svc.host.reload_bundles_from_disk(full=True)


def test_ensure_recreates_both_automations_after_a_bundle_version_change(gw, monkeypatch):
    import abstractnote

    real = abstractnote.__version__
    c, h = gw["client"], gw["alice"]
    svc = _svc(gw)
    _bump_to(monkeypatch, svc, "0.0.9")
    first = c.post(ENSURE, headers=h)
    assert first.status_code == 200, first.text
    first = first.json()
    assert (first["processor"]["action"], first["reflect"]["action"]) == ("created", "created")
    old_proc, old_refl = first["processor_id"], first["reflect_id"]
    assert _definition(c, h, old_proc)["target"]["workflow_id"] == "abstractnote@0.0.9:note-drain"
    # the user paused the reflect automation before the upgrade
    c.post(f"/api/gateway/automations/{old_refl}/commands", headers=h,
           json={"command_id": "user-pause", "type": "automation.pause"})
    _apply_pending(svc, old_refl)
    assert c.get("/api/gateway/note/status", headers=h).json()["automations"]["reflect"]["state"] == "paused"

    _bump_to(monkeypatch, svc, real)
    status = c.get("/api/gateway/note/status", headers=h).json()["automations"]
    assert status["processor"]["healthy"] is False and "0.0.9" in status["processor"]["reason"]
    assert status["processor"]["target"] == "abstractnote@0.0.9:note-drain"
    assert status["reflect"]["healthy"] is False
    queue = c.get("/api/gateway/note/queue", headers=h).json()["automations"]
    assert queue["processor"]["healthy"] is False

    second = c.post(ENSURE, headers=h)
    assert second.status_code == 200, second.text
    second = second.json()
    for kind, old in (("processor", old_proc), ("reflect", old_refl)):
        assert second[kind]["action"] == "recreated" and "0.0.9" in second[kind]["reason"], second
        assert second[kind]["id"] != old and second[kind]["previous_id"] == old
        assert "automation.archive" in _commands(svc, old)
    assert second["processor_id"] == second["processor"]["id"]
    assert sorted(second["created"]) == ["note-drain", "note-reflect"]
    new_proc, new_refl = second["processor_id"], second["reflect_id"]
    assert _definition(c, h, new_proc)["target"]["workflow_id"] == f"abstractnote@{real}:note-drain"
    assert _definition(c, h, new_refl)["target"]["workflow_id"] == f"abstractnote@{real}:note-reflect"
    # the user's settings are what the new automations run with
    assert _definition(c, h, new_proc)["trigger"]["config"]["every"] == "2m"
    assert _definition(c, h, new_proc)["target"]["input_data"]["max_items"] == 20
    # the paused reflect stays paused; the processor (not paused) gets no pause
    assert "automation.pause" in _commands(svc, new_refl)
    assert "automation.pause" not in _commands(svc, new_proc)
    _apply_pending(svc, new_refl)
    status = c.get("/api/gateway/note/status", headers=h).json()["automations"]
    assert status["processor"] == {**status["processor"], "id": new_proc, "healthy": True, "state": "active"}
    assert status["reflect"] == {**status["reflect"], "id": new_refl, "healthy": True, "state": "paused"}

    third = c.post(ENSURE, headers=h).json()
    assert (third["processor"]["action"], third["reflect"]["action"]) == ("kept", "kept")
    assert (third["processor_id"], third["reflect_id"], third["created"]) == (new_proc, new_refl, [])


def test_ensure_recreates_a_failed_or_missing_automation_at_the_same_version(gw):
    from abstractruntime.core.models import RunStatus

    from abstractnote.gateway.router import notebook_for

    c, h = gw["client"], gw["alice"]
    svc = _svc(gw)
    first = c.post(ENSURE, headers=h).json()
    run = svc.runner.run_store.load(first["processor_id"])
    run.status = RunStatus.FAILED
    svc.runner.run_store.save(run)
    notebook_for(_plane(gw, "alice")).set_meta("reflect_automation_id", "00000000-0000-0000-0000-000000000000")
    status = c.get("/api/gateway/note/status", headers=h).json()["automations"]
    assert (status["processor"]["state"], status["processor"]["healthy"]) == ("failed", False)
    assert (status["reflect"]["state"], status["reflect"]["healthy"]) == ("missing", False)

    second = c.post(ENSURE, headers=h).json()
    assert second["processor"]["action"] == "recreated" and "failed" in second["processor"]["reason"]
    assert second["reflect"]["action"] == "recreated" and second["reflect"]["reason"] == "It no longer exists."
    assert second["processor_id"] != first["processor_id"]
    status = c.get("/api/gateway/note/status", headers=h).json()["automations"]
    assert status["processor"]["healthy"] is True and status["reflect"]["healthy"] is True
    # and once more (a second repair at the same version gets a fresh automation, not the failed one)
    run = svc.runner.run_store.load(second["processor_id"])
    run.status = RunStatus.FAILED
    svc.runner.run_store.save(run)
    third = c.post(ENSURE, headers=h).json()
    assert third["processor"]["action"] == "recreated" and third["processor_id"] not in (
        first["processor_id"], second["processor_id"])
    assert third["reflect"]["action"] == "kept"


def test_ensure_never_archives_when_the_note_workflows_are_not_served(gw, monkeypatch):
    from abstractnote.gateway import router

    c, h = gw["client"], gw["alice"]
    svc = _svc(gw)
    first = c.post(ENSURE, headers=h).json()
    monkeypatch.setattr(router, "_served_version", lambda _svc: None)
    status = c.get("/api/gateway/note/status", headers=h).json()["automations"]
    assert status["processor"]["healthy"] is False and "not loaded" in status["processor"]["reason"]
    r = c.post(ENSURE, headers=h)
    assert r.status_code == 503 and "not loaded" in r.json()["detail"]
    assert "automation.archive" not in _commands(svc, first["processor_id"])
    assert router.notebook_for(_plane(gw, "alice")).meta("processor_automation_id") == first["processor_id"]
