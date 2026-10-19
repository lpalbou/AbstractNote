"""Reads never recover (eval REPORT 4.3 item 6): status, search, entity, day, changes and the CLI reads
leave a half-applied revision alone, since another process may still be applying it. Capture, filing,
processing, user actions and ``abstractnote recover`` finish it."""

from __future__ import annotations

import json

import pytest

from abstractnote.cli import main
from abstractnote.store.notebook import Notebook
from test_store_support import claim, create, rev, t


def half_applied(root):
    """A notebook holding one finished revision and one stopped between the index and the triple store."""
    with Notebook.open(root) as nb:
        nb.apply_revision(rev("rv_0", t(1), create("ex:person-dana", "Dana"), create("ex:org-acme", "Acme")))
        with pytest.raises(RuntimeError):
            nb.apply_revision(rev("rv_1", t(2), claim("cl_1", "ex:person-dana", "schema:worksFor", "ex:org-acme",
                                                      at=t(2))), _fail_after_index=True)


def pending(root):
    with Notebook.open(root) as nb:
        return [r["revision_id"] for r in nb.store.pending_revisions()]


def test_library_reads_leave_a_half_applied_revision_alone(tmp_path):
    root = tmp_path / "nb"
    half_applied(root)
    assert pending(root) == ["rv_1"]
    with Notebook.open(root) as nb:  # the default open is a read
        nb.status()
        nb.search("Dana Acme")
        nb.entity("ex:person-dana")
        nb.day("2026-01-02")
        nb.changes()
        nb.tasks()
        nb.open_loops()
        nb.contradictions()
        assert [r["revision_id"] for r in nb.store.pending_revisions()] == ["rv_1"]
    assert pending(root) == ["rv_1"]


def test_cli_reads_leave_it_alone_and_recover_finishes_it(tmp_path, capsys):
    root = tmp_path / "nb"
    half_applied(root)
    for argv in (["status"], ["search", "Dana"], ["entity", "ex:person-dana"], ["day", "2026-01-02"], ["changes"]):
        assert main([*argv, "--notebook", str(root)]) == 0, argv
        capsys.readouterr()
        assert pending(root) == ["rv_1"], argv
    assert main(["recover", "--notebook", str(root)]) == 0
    assert json.loads(capsys.readouterr().out) == {"recovered": ["rv_1"]}
    assert pending(root) == []
    with Notebook.open(root) as nb:
        assert len(nb.kg.query(assertion_ids=("cl_1",))) == 1
    assert main(["recover", "--notebook", str(root)]) == 0
    assert json.loads(capsys.readouterr().out) == {"recovered": []}


@pytest.mark.parametrize("write", ["capture", "cli_capture", "user_action", "archive"])
def test_writes_recover_first(tmp_path, capsys, write):
    root = tmp_path / "nb"
    half_applied(root)
    if write == "cli_capture":
        assert main(["capture", "Dana called", "--notebook", str(root)]) == 0
        capsys.readouterr()
    else:
        with Notebook.open(root) as nb:
            if write == "capture":
                nb.capture("Dana called", occurred_at=t(3))
            elif write == "user_action":
                nb.retract_claim("cl_1", "me", "never true", occurred_at=t(4))
            else:
                note_id = nb.capture("to archive", occurred_at=t(3)).note_id
                nb.store.conn.execute("UPDATE revisions SET applied_kg=0 WHERE revision_id='rv_1'")
                nb.archive_note(note_id)
    assert pending(root) == []


def test_open_with_recover_true_still_finishes_it(tmp_path):
    root = tmp_path / "nb"
    half_applied(root)
    with Notebook.open(root, recover=True):
        pass
    assert pending(root) == []
