"""The gateway seam, end to end in-process: the note read API, isolation, tool binding, approvals, apps.

Runs against the abstractgateway checkout installed next to abstractnote (the
`abstractnote-seam` branch). Every test uses a scratch data dir, scratch HOME and user
accounts; no network, no live gateway.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("abstractgateway", reason="abstractgateway is not installed in this environment")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def gw(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"
    for d in (home, tmp_path / "data", tmp_path / "bundles"):
        d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("HF_HOME", str(home / "hf"))
    for name in ("ABSTRACTGATEWAY_AUTH_TOKEN", "ABSTRACTGATEWAY_PROVIDER", "ABSTRACTGATEWAY_MODEL",
                 "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ABSTRACTGATEWAY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ABSTRACTGATEWAY_FLOWS_DIR", str(tmp_path / "bundles"))
    monkeypatch.setenv("ABSTRACTGATEWAY_USERS_FILE", str(tmp_path / "users.json"))
    monkeypatch.setenv("ABSTRACTGATEWAY_SESSIONS_FILE", str(tmp_path / "sessions.json"))
    monkeypatch.setenv("ABSTRACTGATEWAY_USER_AUTH", "1")
    monkeypatch.setenv("ABSTRACTGATEWAY_ALLOWED_ORIGINS", "*")
    monkeypatch.setenv("ABSTRACTGATEWAY_RUNNER", "0")
    monkeypatch.setenv("ABSTRACTGATEWAY_AUTO_PUBLISH_SHIPPED", "0")
    from abstractgateway.service import reset_gateway_boot_state
    from abstractgateway.users import GatewayUserRegistry

    from abstractnote.gateway.router import close_notebooks

    reset_gateway_boot_state()
    reg = GatewayUserRegistry()
    _, alice = reg.create_user(user_id="alice", roles=["user"])
    _, bob = reg.create_user(user_id="bob", roles=["user"])
    _, admin = reg.create_user(user_id="admin", roles=["admin"])
    from abstractgateway.app import app

    with TestClient(app) as c:
        yield {
            "client": c, "data": tmp_path / "data", "registry": reg,
            "alice": {"Authorization": f"Bearer {alice}"}, "bob": {"Authorization": f"Bearer {bob}"},
            "admin": {"Authorization": f"Bearer {admin}"},
        }
    close_notebooks()
    reset_gateway_boot_state()


def _plane(gw, who: str) -> Path:
    from abstractgateway.service import get_gateway_service_for_principal

    principal = gw["registry"].authenticate(gw[who]["Authorization"].split(" ", 1)[1])
    return Path(get_gateway_service_for_principal(principal).config.data_dir)


def test_anonymous_is_refused(gw):
    c = gw["client"]
    assert c.get("/api/gateway/note/status").status_code == 401
    assert c.post("/api/gateway/note/processor/ensure").status_code == 401


def test_status_for_a_signed_in_user(gw):
    r = gw["client"].get("/api/gateway/note/status", headers=gw["alice"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert "notebook" not in body  # no server paths to clients
    assert body["automations"] == {"processor_id": None, "reflect_id": None}
    assert body["embedding_pin"]["model_id"]
    assert (_plane(gw, "alice") / "abstractnote" / "note.sqlite3").is_file()


def test_each_account_has_its_own_notebook(gw):
    from abstractnote.gateway.router import notebook_for

    alice_plane, bob_plane, admin_plane = _plane(gw, "alice"), _plane(gw, "bob"), _plane(gw, "admin")
    assert len({alice_plane, bob_plane, admin_plane}) == 3
    assert admin_plane.resolve() == gw["data"].resolve()  # m6: the admin account uses the root data dir
    note_id = notebook_for(alice_plane).capture("Met Dana about the LUT budget.", source={"client": "cli"}).note_id
    c = gw["client"]
    assert c.get(f"/api/gateway/note/notes/{note_id}", headers=gw["alice"]).status_code == 200
    for other in ("bob", "admin"):
        r = c.get(f"/api/gateway/note/notes/{note_id}", headers=gw[other])
        assert r.status_code == 404 and r.json()["detail"] == "That note is not in your notebook."
    assert c.get("/api/gateway/note/status", headers=gw["alice"]).json()["counts"]["notes"] == 1
    assert c.get("/api/gateway/note/status", headers=gw["bob"]).json()["counts"]["notes"] == 0


def test_settings_round_trip_and_refusal(gw):
    c, h = gw["client"], gw["alice"]
    r = c.put("/api/gateway/note/settings", headers=h, json={"process_inline_default": True})
    assert r.status_code == 200 and r.json()["process_inline_default"] is True
    assert c.get("/api/gateway/note/settings", headers=h).json()["process_inline_default"] is True
    bad = c.put("/api/gateway/note/settings", headers=h, json={"colour": "red"})
    assert bad.status_code == 400 and bad.json()["detail"] == "Unknown setting: colour."


def test_queue_summary_and_retry_of_an_unknown_note(gw):
    c, h = gw["client"], gw["alice"]
    q = c.get("/api/gateway/note/queue", headers=h)
    assert q.status_code == 200 and set(q.json()) >= {"stats", "failed", "waiting", "automations"}
    assert c.post("/api/gateway/note/queue/nt_nope/retry", headers=h).status_code == 404


def test_the_router_is_mounted_only_through_the_seam():
    from abstractgateway import abstractnote_seam
    from abstractgateway.app import app

    from abstractnote.gateway.router import router

    assert abstractnote_seam.note_router() is router
    included = [r for r in app.routes if getattr(r, "original_router", None) is router or getattr(r, "path", "") == "/api/gateway/note/status"]
    assert len(included) == 1


def test_the_approval_auto_set_carries_the_note_tools():
    from abstractgateway.abstractnote_seam import note_auto_approve_names

    from abstractnote.flows.build import NOTE_TOOLS
    from abstractnote.gateway.tools import AUTO_APPROVE_TOOL_NAMES

    assert set(AUTO_APPROVE_TOOL_NAMES) == set(NOTE_TOOLS)
    assert note_auto_approve_names() == frozenset(NOTE_TOOLS)


def test_the_app_catalog_lists_note():
    from abstractgateway.apps_manager import APP_BY_ID, FLAG_CONFIGURED_APPS, STACK_PORT
    from abstractgateway.tray.apps import WEB_APPS

    from abstractnote.gateway import appspec

    row = APP_BY_ID["note"]
    assert (row.name, row.package, row.default_port, row.cookie_prefix, row.gateway_url_env, row.html_title) == (
        appspec.APP_NAME, appspec.APP_PACKAGE, appspec.APP_DEFAULT_PORT, appspec.APP_COOKIE_PREFIX,
        appspec.APP_GATEWAY_URL_ENV, appspec.APP_HTML_TITLE)
    assert STACK_PORT["note"] == 3006 and "note" in FLAG_CONFIGURED_APPS
    assert any(w[0] == "note" and w[2] == appspec.APP_PACKAGE for w in WEB_APPS)


def test_apps_listing_contains_note(gw):
    r = gw["client"].get("/api/gateway/apps", headers=gw["admin"])
    assert r.status_code == 200, r.text
    ids = [a.get("id") for a in r.json().get("items") or r.json().get("apps") or []]
    assert "note" in ids


def _executor_chain(runtime):
    ex = runtime._tool_executor_for_resume
    chain = [ex]
    while getattr(chain[-1], "_delegate", None) is not None:
        chain.append(chain[-1]._delegate)
    return chain


def test_a_plane_runtime_loads_the_workflows_binds_and_auto_approves_the_note_tools(gw):
    from abstractgateway.service import get_gateway_service_for_principal

    from abstractnote.flows.build import ALL_FLOWS, NOTE_TOOLS

    principal = gw["registry"].authenticate(gw["alice"]["Authorization"].split(" ", 1)[1])
    host = get_gateway_service_for_principal(principal).host
    assert {f"abstractnote@0.1.0:{fid}" for fid in ALL_FLOWS} <= set(host.specs)
    chain = _executor_chain(host.runtime)
    policy = chain[0]._policy
    assert not policy.requires_approval([{"name": n} for n in NOTE_TOOLS])  # S2: no approval wait for note tools
    assert policy.requires_approval([{"name": "execute_command"}])          # the rest of the gate is unchanged
    assert "send_email" in policy.default_require_approval_tools            # the default caution list survives
    tool_map = chain[-1]._tool_map
    assert set(NOTE_TOOLS) <= set(tool_map)


def test_pinned_reads_on_an_empty_notebook(gw):
    c, h = gw["client"], gw["alice"]
    shapes = {
        "/api/gateway/note/changes": {"cursor", "notes", "entities", "superseded_claims", "closed_loops", "failed_notes", "insights"},
        "/api/gateway/note/overview": {"recent", "entities", "insights", "resurfacing", "activity"},
        "/api/gateway/note/cues": {"items"},
        "/api/gateway/note/tasks": {"items"},
        "/api/gateway/note/insights": {"items"},
        "/api/gateway/note/topics": {"items"},
    }
    for path, keys in shapes.items():
        r = c.get(path, headers=h)
        assert r.status_code == 200, (path, r.text)
        assert keys <= set(r.json()), (path, r.json())


def test_unknown_ids_are_404_and_bad_input_is_400(gw):
    c, h = gw["client"], gw["alice"]
    for path in ("/api/gateway/note/entities/ex:person-nobody", "/api/gateway/note/entities/ex:person-nobody/timeline",
                 "/api/gateway/note/claims/cl_nope", "/api/gateway/note/relations/ex:a/ex:b"):
        r = c.get(path, headers=h)
        assert r.status_code == 404, (path, r.status_code, r.text)
    r = c.post("/api/gateway/note/tasks/tk_nope/close", headers=h, json={})
    assert r.status_code == 404 and r.json()["detail"] == "That task is not in your notebook."
    r = c.post("/api/gateway/note/claims/cl_nope/retract", headers=h, json={"reason": "wrong"})
    assert r.status_code == 404
    assert c.get("/api/gateway/note/timeline?granularity=year", headers=h).status_code == 422


def test_the_plane_tools_are_the_core_lane_tools(gw):
    from abstractgateway.service import get_gateway_service_for_principal

    principal = gw["registry"].authenticate(gw["alice"]["Authorization"].split(" ", 1)[1])
    host = get_gateway_service_for_principal(principal).host
    tool_map = _executor_chain(host.runtime)[-1]._tool_map
    td = getattr(tool_map["note_capture"], "_tool_definition", None)
    assert td is not None and td.name == "note_capture" and "about" in td.parameters


def test_ensure_starts_the_processor_now_and_reflect_only_after_one_interval(gw):
    """Opening an empty notebook must not start an answering-model call: the reflect automation's
    first occurrence is its next scheduled time (review 02 M1); the processor drains at once."""
    from datetime import datetime, timedelta, timezone

    c, h = gw["client"], gw["alice"]
    before = datetime.now(timezone.utc)
    r = c.post("/api/gateway/note/processor/ensure", headers=h)
    assert r.status_code == 200, r.text
    ids = r.json()
    assert ids["created"] == ["note-drain", "note-reflect"]

    def config(automation_id):
        got = c.get(f"/api/gateway/automations/{automation_id}", headers=h)
        assert got.status_code == 200, got.text
        return got.json()["definition"]["trigger"]["config"]

    reflect = config(ids["reflect_id"])
    assert reflect["every"] == "7d"
    first = datetime.fromisoformat(reflect["start_at"])
    assert before + timedelta(days=7) - timedelta(minutes=1) <= first <= datetime.now(timezone.utc) + timedelta(days=7)
    proc = config(ids["processor_id"])
    assert datetime.fromisoformat(proc["start_at"]) <= datetime.now(timezone.utc)


def test_as_of_travels_over_http(gw):
    """Review 01 S4 on the wire (review 02 M2): `at` reaches the Notebook through the router."""
    from test_store_support import claim, create, rev, t

    from abstractnote.gateway.router import notebook_for

    c, h = gw["client"], gw["alice"]
    t1, before = t(10), t(9)
    nb = notebook_for(_plane(gw, "alice"))
    nb.apply_revision(rev("rv_asof", t1, create("ex:person-dana", "Dana"), create("ex:project-lut", "LUT"),
                          claim("cl_asof", "ex:person-dana", "schema:participant", "ex:project-lut", at=t1)))

    def graph(at):
        r = c.get("/api/gateway/note/graph", params={"at": at}, headers=h)
        assert r.status_code == 200, r.text
        return r.json()

    assert graph(before)["nodes"] == [] and graph(before)["edges"] == []
    then = graph(t1)
    assert {n["id"] for n in then["nodes"]} == {"ex:person-dana", "ex:project-lut"}
    assert then["edges"]
    r = c.get("/api/gateway/note/entities/ex:person-dana", params={"at": before}, headers=h)
    assert r.status_code == 400 and r.json()["detail"] == "That entity was not known yet at that time."
    r = c.get("/api/gateway/note/entities/ex:person-dana", params={"at": t1}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["as_of"] == t1 and [b["claim_id"] for b in r.json()["beliefs"]] == ["cl_asof"]


def test_user_actions_record_the_client_from_the_app_proxy_never_from_the_body(gw):
    """Review 02 m4: the account is the principal; the client is `web` only through the Note web
    app's proxy (`X-AbstractFramework-App-Proxy: abstractnote`), else `api`; a body cannot say."""
    from test_store_support import claim, create, rev, t

    from abstractnote.gateway.router import notebook_for

    c, h = gw["client"], gw["alice"]
    nb = notebook_for(_plane(gw, "alice"))
    nb.apply_revision(rev("rv_m4", t(10), create("ex:person-dana", "Dana"), create("ex:project-lut", "LUT"),
                          claim("cl_m4", "ex:person-dana", "schema:participant", "ex:project-lut", at=t(10)),
                          {"op": "task.open", "payload": {"task_id": "tk_m4", "text": "Send Dana the Pareto"}}))
    r = c.post("/api/gateway/note/claims/cl_m4/retract", headers=h, json={"reason": "wrong", "client": "watch"})
    assert r.status_code == 422  # no client-declared field
    r = c.post("/api/gateway/note/claims/cl_m4/retract", headers=h, json={"reason": "wrong"})
    assert r.status_code == 200, r.text
    assert nb.revision(r.json()["revision_id"])["source"] == {"client": "api", "account": "alice"}
    r = c.post("/api/gateway/note/tasks/tk_m4/close", headers={**h, "X-AbstractFramework-App-Proxy": "abstractnote"},
               json={})
    assert r.status_code == 200, r.text
    assert nb.revision(r.json()["revision_id"])["source"] == {"client": "web", "account": "alice"}
