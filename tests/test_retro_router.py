"""``POST /note/notes/{id}/redate`` and the date fields of ``GET /note/notes/{id}`` over HTTP (W23)."""

from __future__ import annotations

import pytest

pytest.importorskip("abstractgateway", reason="abstractgateway is not installed in this environment")
pytest.importorskip("fastapi")

from test_gateway_router import _plane, gw  # noqa: E402,F401


def test_set_the_date_of_an_imported_note_over_http(gw):  # noqa: F811
    from abstractnote.gateway.router import notebook_for

    c, h = gw["client"], gw["alice"]
    nb = notebook_for(_plane(gw, "alice"))
    nid = nb.capture("An old letter about Castor.", occurred_at="2026-10-01T10:00:00Z", importer="files",
                     source_key="letter", source={"client": "importer", "origin": {"date_basis": "file_mtime"}}).note_id
    r = c.get(f"/api/gateway/note/notes/{nid}", headers=h)
    assert r.status_code == 200, r.text
    assert (r.json()["date_basis"], r.json()["date_approximate"]) == ("file_mtime", True)  # the UI offers "Set the date"
    r = c.post(f"/api/gateway/note/notes/{nid}/redate", headers=h,
               json={"occurred_at": "2019-04-05", "reason": "the letter's own date"})
    assert r.status_code == 200, r.text
    assert nb.revision(r.json()["revision_id"])["source"] == {"client": "api", "account": "alice"}
    body = c.get(f"/api/gateway/note/notes/{nid}", headers=h).json()
    assert (body["occurred_at"], body["date_basis"], body["date_approximate"]) == ("2019-04-05T00:00:00Z", "user",
                                                                                  False)
    assert c.post(f"/api/gateway/note/notes/{nid}/redate", headers=h,
                  json={"occurred_at": "2019-04-05"}).status_code == 400  # already that date
    assert c.post(f"/api/gateway/note/notes/{nid}/redate", headers=h,
                  json={"occurred_at": "soon"}).status_code == 400
    assert c.post("/api/gateway/note/notes/nt_nope/redate", headers=h,
                  json={"occurred_at": "2019-04-05"}).status_code == 404
    assert c.post(f"/api/gateway/note/notes/{nid}/redate", headers=gw["bob"],
                  json={"occurred_at": "2018-01-01"}).status_code == 404  # another person's notebook


def test_the_redate_of_an_understood_note_is_refused_over_http_until_it_can_be_understood_again(gw, monkeypatch):  # noqa: F811
    """ADR 0008 §4/§7 (R4): 409 with one sentence, nothing written. MUTATION: map RedateRefused to 200."""
    from abstractnote.gateway.router import notebook_for
    from abstractnote.ingest.dates import NEEDS_REUNDERSTANDING
    from abstractnote.tools.context import ToolContext
    from abstractnote.tools.process import note_apply_revision, note_context, note_resolve_candidates

    c, h = gw["client"], gw["alice"]
    nb = notebook_for(_plane(gw, "alice"))
    nid = nb.capture("Castor runs on Llama.", occurred_at="2026-10-01T10:00:00Z").note_id
    ctx = ToolContext(nb=nb)
    note_context(ctx, nid)
    note_resolve_candidates(ctx, nid, [{"ref": "m1", "name": "Castor", "kind": "project", "aliases": []}])
    note_apply_revision(ctx, nid, {"summary": "Castor.", "mentions": [{"ref": "m1", "name": "Castor",
                                                                      "kind": "project", "role": "the project"}],
                                   "claims": [], "tasks": [], "time_refs": [], "topics": []},
                        {"mentions": [{"ref": "m1", "decision": "create", "name": "Castor", "kind": "project",
                                       "summary": "Castor."}], "claims": [], "tasks": [], "revision_summary": "x"})
    from abstractnote.store import kg

    monkeypatch.setattr(kg, "supports_reinterpreted", lambda: False)  # an AbstractMemory without the kind
    r = c.post(f"/api/gateway/note/notes/{nid}/redate", headers=h, json={"occurred_at": "2019-04-05"})
    assert r.status_code == 409 and r.json()["detail"] == NEEDS_REUNDERSTANDING, r.text
    assert c.get(f"/api/gateway/note/notes/{nid}", headers=h).json()["occurred_at"] == "2026-10-01T10:00:00Z"
