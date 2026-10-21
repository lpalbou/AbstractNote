"""W28 item 2: ``abstractnote archive-insights --repeated`` / ``POST /note/insights/archive-repeated``: insights with the
same kind, title (case-folded) and subject keep their oldest unacknowledged member; the rest are archived in ONE
revision through the note archive, with a ``cleanup_runs`` row and a notice; reversible with ``unarchive --revision``.
Each test names what it guards (mutation list in untracked/reports/W28.md)."""

from __future__ import annotations

import json

import pytest

from abstractnote.store.notebook import Notebook

ENT_A, ENT_B = "ex:project-lut", "ex:person-priya"


@pytest.fixture
def nb(tmp_path):
    notebook = Notebook.open(tmp_path / "nb")
    yield notebook
    notebook.close()


_N = [0]


def insight(nb: Notebook, kind: str, title: str, body: str, entities=()) -> str:
    out = nb.write_insight(kind, title, body)
    assert out["status"] == "new", out
    _N[0] += 1  # received one minute apart, in the order written: "the oldest" is the first
    nb.store.conn.execute("UPDATE notes SET received_at=? WHERE note_id=?",
                          (f"2026-10-01T10:{_N[0]:02d}:00Z", out["note_id"]))
    nb.store.conn.commit()
    if entities:  # the subject the reflection named (write_insight keeps the first of a subject; the eval data has
        # repeats from before that rule)
        nb.store.conn.execute("UPDATE notes SET entity_ids_json=? WHERE note_id=?", (json.dumps(list(entities)),
                                                                                     out["note_id"]))
        nb.store.conn.commit()
    return out["note_id"]


def world(nb: Notebook):
    _N[0] = 0
    ids = {}
    ids["r"] = [insight(nb, "reminder", "Add CodeActAgent", f"body {i}") for i in range(3)]
    ids["r"].append(insight(nb, "reminder", "  add   codeactagent ", "body 3"))  # the same title, folded
    ids["acked"] = insight(nb, "reminder", "Add CodeActAgent", "seen")
    nb.ack_insight(ids["acked"])
    ids["w"] = [insight(nb, "warning", "Add CodeActAgent", f"w{i}") for i in range(2)]  # another kind: its own group
    ids["a"] = [insight(nb, "reminder", "Stale LUT", f"a{i}", [ENT_A]) for i in range(2)]
    ids["b"] = insight(nb, "reminder", "Stale LUT", "b", [ENT_B])  # another subject: alone
    ids["one"] = insight(nb, "connection", "A link", "only one")
    return ids


def archived(nb: Notebook):
    return sorted(r[0] for r in nb.store.q("SELECT note_id FROM notes WHERE archived_at IS NOT NULL"))


def test_repeats_keep_the_oldest_unacknowledged_and_archive_the_rest_in_one_revision(nb):
    """Groups by (kind, folded title, subject); the oldest unacknowledged is kept, an acknowledged one is left alone;
    one revision, one completed cleanup row with the notice; a second run finds nothing; the undo brings all back.
    MUTATIONS: the title fold, the subject in the key, the oldest kept, acked insights counted, one revision per
    note, the progress row, the undo."""
    w = world(nb)
    dry = nb.archive_repeated_insights(dry_run=True)
    assert dry["archived"] == 5 and dry["kept"] == 3 and dry["insights"] == 11
    assert archived(nb) == [] and nb.store.q1("SELECT COUNT(*) FROM cleanup_runs")[0] == 0
    by_title = {(g["kind"], g["title"].strip().casefold()): g for g in dry["groups"]}
    g = by_title[("reminder", "add codeactagent")]
    assert g["keep"] == w["r"][0] and g["archive"] == w["r"][1:] and g["count"] == 4
    assert by_title[("warning", "add codeactagent")]["archive"] == w["w"][1:]
    assert by_title[("reminder", "stale lut")]["archive"] == w["a"][1:]
    revs = nb.store.q1("SELECT COUNT(*) FROM revisions")[0]
    out = nb.archive_repeated_insights(account="me")
    assert out["archived"] == 5 and nb.store.q1("SELECT COUNT(*) FROM revisions")[0] == revs + 1
    assert archived(nb) == sorted(w["r"][1:] + w["w"][1:] + w["a"][1:])
    rev = nb.revision(out["revision_id"])
    assert rev["action"] == "note.archive" and rev["source"]["account"] == "me"
    assert all(o["payload"].get("withdrawn") for o in rev["ops"] if o["op"] == "note.archive")
    row = nb.repeated_insights_cleanup()
    assert row["status"] == "completed" and row["done"] == 5 and row["revision_id"] == out["revision_id"]
    assert sorted(row["archived"]) == archived(nb) and row["groups"] == 3
    assert row["notice"] == "Archived 5 repeated insights and kept the oldest of each (3); they can be brought back."
    assert {i["note_id"] for i in nb.insights()["items"]} == {w["r"][0], w["acked"], w["w"][0], w["a"][0], w["b"],
                                                              w["one"]}
    again = nb.archive_repeated_insights()
    assert again["archived"] == 0 and again["notice"] == "No repeated insights to archive."
    back = nb.unarchive_revision(out["revision_id"], "me")
    assert sorted(back["notes"]) == sorted(row["archived"]) and archived(nb) == []
    with pytest.raises(ValueError, match="back already"):
        nb.unarchive_revision(out["revision_id"], "me")


def test_the_cli_lists_archives_and_brings_back(tmp_path, capsys):
    """``abstractnote archive-insights --repeated [--dry-run]`` and ``abstractnote unarchive --revision``; without
    --repeated it is refused (exit 2). MUTATION: drop the --repeated check or the dry-run flag."""
    from abstractnote.cli import main

    with Notebook.open(tmp_path / "nb") as nb:
        world(nb)
    assert main(["archive-insights", "--notebook", str(tmp_path / "nb")]) == 2
    capsys.readouterr()
    assert main(["archive-insights", "--repeated", "--dry-run", "--notebook", str(tmp_path / "nb")]) == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["dry_run"] is True and dry["archived"] == 5
    with Notebook.open(tmp_path / "nb") as nb:
        assert archived(nb) == []
    assert main(["archive-insights", "--repeated", "--notebook", str(tmp_path / "nb"), "--account", "me"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["archived"] == 5 and out["cleanup"]["status"] == "completed"
    assert main(["unarchive", "--revision", out["revision_id"], "--notebook", str(tmp_path / "nb")]) == 0
    assert len(json.loads(capsys.readouterr().out)["notes"]) == 5
    assert main(["unarchive", "--notebook", str(tmp_path / "nb")]) == 2


def test_the_routes_archive_repeated_and_bring_a_note_back(gw):
    """``POST /note/insights/archive-repeated {dry_run}``, ``GET`` the latest cleanup, ``POST /notes/{id}/unarchive``
    (400 when not archived), all as the signed-in account. MUTATION: drop a route, or the account."""
    from test_gateway_router import _plane

    from abstractnote.gateway.router import notebook_for

    c, h = gw["client"], gw["alice"]
    nb = notebook_for(_plane(gw, "alice"))
    w = world(nb)
    r = c.post("/api/gateway/note/insights/archive-repeated", headers=h, json={"dry_run": True, "x": 1})
    assert r.status_code == 422
    r = c.post("/api/gateway/note/insights/archive-repeated", headers=h, json={"dry_run": True})
    assert r.status_code == 200 and r.json()["archived"] == 5 and archived(nb) == []
    assert c.get("/api/gateway/note/insights/archive-repeated", headers=h).json() == {"cleanup": None}
    r = c.post("/api/gateway/note/insights/archive-repeated", headers=h, json={})
    assert r.status_code == 200, r.text
    assert nb.revision(r.json()["revision_id"])["source"] == {"client": "api", "account": "alice"}
    last = c.get("/api/gateway/note/insights/archive-repeated", headers=h).json()["cleanup"]
    assert last["status"] == "completed" and len(last["archived"]) == 5
    r = c.post(f"/api/gateway/note/notes/{w['r'][1]}/unarchive", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["archived_at"] is None
    assert nb.store.note_row(w["r"][1])["archived_at"] is None
    assert c.post(f"/api/gateway/note/notes/{w['r'][1]}/unarchive", headers=h).status_code == 400
    r = c.post(f"/api/gateway/note/notes/{w['one']}/archive", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "archived" and r.json()["revision_id"]
    assert nb.revision(r.json()["revision_id"])["source"]["account"] == "alice"


from test_gateway_router import gw  # noqa: E402,F401  (the scratch-gateway fixture)
