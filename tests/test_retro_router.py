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
