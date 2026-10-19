"""The abstractnote CLI on a temporary notebook."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from abstractnote import ids
from abstractnote.cli import EXIT_GAP, EXIT_INPUT, main
from abstractnote.store.notebook import Notebook
from test_store_support import create, rev


def run(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr()
    return code, (json.loads(out.out) if out.out.strip() else None), out.err


def test_capture_status_search_day_changes(tmp_path, capsys):
    nb_dir = str(tmp_path / "nb")
    f = tmp_path / "plan.md"
    f.write_text("# Plan\n\nThe LUT prefill plan.")
    code, out, _ = run(capsys, "capture", "Met Dana about the LUT budget", "--notebook", nb_dir, "--at",
                       "2026-10-19T09:12:00Z", "--file", str(f), "--key", "k1")
    assert code == 0 and out["status"] == "new" and out["attachments"][0]["extracted_by"] == "markdown"
    note_id = out["note_id"]
    code, again, _ = run(capsys, "capture", "Met Dana about the LUT budget", "--notebook", nb_dir, "--key", "k1")
    assert again["status"] == "duplicate"
    code, status, _ = run(capsys, "status", "--notebook", nb_dir)
    assert code == 0 and status["counts"]["notes"] == 1 and status["queue"]["queued"] == 1
    assert status["embedding_pin"]["model_id"].startswith("abstractnote-hashing")
    code, hits, _ = run(capsys, "search", "prefill plan", "--notebook", nb_dir)
    assert hits["notes"][0]["note_id"] == note_id
    code, day, _ = run(capsys, "day", "2026-10-19", "--notebook", nb_dir)
    assert [n["note_id"] for n in day["notes"]] == [note_id]
    code, ch, _ = run(capsys, "changes", "--notebook", nb_dir)
    assert [n["note_id"] for n in ch["notes"]] == [note_id]
    code, ch2, _ = run(capsys, "changes", "--notebook", nb_dir, "--since", ch["cursor"])
    assert ch2["notes"] == []


def test_entity_and_rebuild(tmp_path, capsys):
    nb_dir = tmp_path / "nb"
    with Notebook.open(nb_dir) as nb:
        nb.apply_revision(rev("rv_1", "2026-01-01T00:00:00Z", create("ex:person-dana", "Dana", summary="A colleague.")))
    code, card, _ = run(capsys, "entity", "ex:person-dana", "--notebook", str(nb_dir))
    assert code == 0 and card["name"] == "dana" and card["summary"] == "A colleague."
    code, out, _ = run(capsys, "rebuild", "--notebook", str(nb_dir))
    assert code == 0 and out["replayed_revisions"] == 1 and out["after"]["entities"] == 1
    code, _, err = run(capsys, "entity", "ex:person-nobody", "--notebook", str(nb_dir))
    assert code == EXIT_INPUT and "not in this notebook" in err


def test_gaps_and_bad_input_say_so(tmp_path, capsys):
    code, _, err = run(capsys, "status")
    assert code == EXIT_INPUT and "--notebook" in err
    code, _, err = run(capsys, "status", "--gateway", "http://127.0.0.1:18899", "--token", "t")
    assert code == EXIT_GAP and "not available" in err
    code, _, err = run(capsys, "processor", "ensure", "--gateway", "http://127.0.0.1:18899", "--token", "t")
    assert code == EXIT_GAP
    code, _, err = run(capsys, "process", "--notebook", str(tmp_path / "nb"))
    assert code == EXIT_GAP and "--local" in err
    code, _, err = run(capsys, "process", "--local", "--notebook", str(tmp_path / "nb"))
    assert code == EXIT_INPUT and "--provider" in err
    code, _, err = run(capsys, "capture", "x", "--notebook", str(tmp_path / "nb"), "--at", "yesterday")
    assert code == EXIT_INPUT and "ISO 8601" in err


def test_import_and_bundle_delegate_to_their_lanes(tmp_path, capsys):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# A\n\nA note about Dana.")
    code = main(["import", "markdown", str(docs), "--notebook", str(tmp_path / "nb")])
    out = capsys.readouterr().out
    assert code == 0 and json.loads(out)["created"] == 1
    with Notebook.open(tmp_path / "nb") as nb:
        [item] = nb.notes()["items"]
        assert nb.queue.item(item["note_id"])["priority"] == 0
    code, out, err = run(capsys, "bundle", "--out", str(tmp_path / "dist"))
    if code == EXIT_GAP:  # the flows lane has not regenerated its JSON after a prompt change: said plainly
        assert "stale" in err and "abstractnote.flows.build" in err
    else:
        assert code == 0, err
        assert out["bundle"].endswith(".flow")


def test_console_script_and_import_are_cheap():
    proc = subprocess.run([sys.executable, "-c", "import abstractnote, sys; print(abstractnote.__version__);"
                           " print('abstractnote.store' in sys.modules)"], capture_output=True, text=True, check=True)
    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    declared = next(line.split('"')[1] for line in pyproject.splitlines() if line.startswith("version = "))
    assert proc.stdout.split() == [declared, "False"]  # __init__ says the version pyproject.toml releases
    proc = subprocess.run([sys.executable, "-m", "abstractnote.cli", "--help"], capture_output=True, text=True)
    assert proc.returncode == 0 and "capture" in proc.stdout
    assert ids.note_id_for_key("x").startswith("nt_")
