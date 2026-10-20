"""One importer owns a curated memory folder (eval2 D11): ``import markdown`` hands Claude Code memory files to
the claude-sessions importer (same importer and source key, so the second import is a duplicate), and
``abstractnote dedupe-sources`` archives the later copy of the pairs an earlier import made, in one revision."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from abstractnote import importers
from abstractnote.cli import main
from abstractnote.importers import claude_sessions
from abstractnote.store.notebook import Notebook

TEXT = "---\nname: Speaker routing\n---\n\nThe default device is frozen.\n"


@pytest.fixture
def project(tmp_path):
    proj = tmp_path / ".claude" / "projects" / "-Users-me-repo"
    (proj / "memory").mkdir(parents=True)
    (proj / "s-1.jsonl").write_text(json.dumps({"type": "user", "uuid": "u1", "timestamp": "2026-10-01T08:00:00Z",
                                                "message": {"content": "hello there"}}) + "\n")
    (proj / "memory" / "speaker.md").write_text(TEXT)
    (proj / "memory" / "MEMORY.md").write_text("- [Speaker routing](speaker.md)\n")
    return proj


@pytest.fixture
def notebook(tmp_path):
    nb = Notebook.open(tmp_path / "nb")
    yield nb
    nb.close()


def _memory_notes(nb):
    return nb.store.q("SELECT note_id, archived_at, duplicate_of FROM notes WHERE"
                      " text LIKE '%default device is frozen%' ORDER BY received_at, note_id")


def test_the_memory_folder_is_imported_once_whichever_importer_runs_first(project, notebook):
    assert claude_sessions.memory_project(project / "memory" / "speaker.md") == project
    assert claude_sessions.memory_project(project / "notes" / "speaker.md") is None
    md = importers.run("markdown", notebook, path=project / "memory")
    assert md.created == 1 and md.skipped.get("memory_index") == 1
    cs = importers.run("claude-sessions", notebook, path=project)
    assert cs.duplicates == 1  # the memory file; the prompt is new
    assert len(_memory_notes(notebook)) == 1
    [row] = notebook.store.q("SELECT source_json FROM notes WHERE text=?", (TEXT,))
    assert json.loads(row[0])["importer"] == "claude-sessions"


def test_a_plain_markdown_tree_is_still_markdown(tmp_path, notebook):
    (tmp_path / "docs" / "memory").mkdir(parents=True)
    (tmp_path / "docs" / "memory" / "a.md").write_text("# A\n")
    importers.run("markdown", notebook, path=tmp_path / "docs")
    [row] = notebook.store.q("SELECT source_json FROM notes")
    assert json.loads(row[0])["importer"] == "markdown"


def test_dedupe_sources_archives_the_later_copy_in_one_revision(project, tmp_path, capsys):
    nb_dir = tmp_path / "nb2"
    with Notebook.open(nb_dir) as nb:
        digest = hashlib.sha256(TEXT.encode()).hexdigest()
        legacy = nb.capture(TEXT, occurred_at="2026-10-01T08:00:00Z", importer="markdown",
                            source_key=f"memory:speaker.md:{digest}", priority=0,
                            source={"origin": {"repo": "memory", "path": "speaker.md", "sha256": digest,
                                               "date_basis": "file_mtime"}}).note_id
        importers.run("claude-sessions", nb, path=project)  # the second copy, under its own key
        rows = _memory_notes(nb)
        assert len(rows) == 2
        later = next(r["note_id"] for r in rows if r["note_id"] != legacy)
        dry = nb.dedupe_sources(dry_run=True)
        assert dry["pairs"] == [{"keep": legacy, "archive": later, "file": "speaker.md", "sha256": digest}]
        assert all(r["archived_at"] is None for r in _memory_notes(nb))
    assert main(["dedupe-sources", "--notebook", str(nb_dir), "--account", "me"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["archived"] == 1 and out["revision_id"]
    with Notebook.open(nb_dir) as nb:
        row = nb.store.note_row(later)
        assert row["archived_at"] and row["duplicate_of"] == legacy and row["status"] == "archived"
        assert nb.store.note_row(legacy)["archived_at"] is None
        assert nb.queue.item(later)["state"] == "done" and nb.queue.item(legacy)["state"] == "queued"
        rev = nb.revision(out["revision_id"])
        assert [o["op"] for o in rev["ops"]] == ["note.archive"] and rev["source"]["account"] == "me"
        assert nb.dedupe_sources()["pairs"] == []
        nb.rebuild()
        assert nb.store.note_row(later)["archived_at"] and nb.store.note_row(later)["duplicate_of"] == legacy
