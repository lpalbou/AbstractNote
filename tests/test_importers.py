"""Importers on small synthetic sources: what becomes a note, faithful time and provenance, noise
filtering, idempotent re-runs, dry-run counts (docs/importers.md)."""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest

from abstractnote import importers
from abstractnote.importers import ImportReport, agora, claude_sessions, files, markdown

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


def _drafts(maker, **kw):
    report = ImportReport(importer="t")
    return list(maker(report=report, **kw)), report


@pytest.fixture
def notebook(tmp_path):
    from abstractnote.store.notebook import Notebook

    nb = Notebook.open(tmp_path / "nb")
    yield nb
    nb.close()


# --------------------------------------------------------------------------- Claude sessions


def _jl(path: Path, records) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


@pytest.fixture
def claude_project(tmp_path):
    proj = tmp_path / "projects" / "-Users-me-repo"
    proj.mkdir(parents=True)
    sid = "s-1"
    base = {"sessionId": sid, "cwd": "/Users/me/repo", "gitBranch": "main"}
    _jl(proj / f"{sid}.jsonl", [
        {"type": "mode", "mode": "normal", "sessionId": sid},
        {**base, "type": "attachment", "uuid": "a0", "timestamp": "2026-10-01T08:00:00.000Z",
         "attachment": {"type": "hook_success", "stdout": "hook output"}},
        {**base, "type": "user", "uuid": "u1", "promptId": "p1", "timestamp": "2026-10-01T08:00:01.000Z",
         "origin": {"kind": "human"}, "promptSource": "typed",
         "message": {"role": "user", "content": [{"type": "text", "text": "Why does the tray show the wrong speaker?"},
                                                  {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                                               "data": base64.b64encode(PNG).decode()}}]}},
        {**base, "type": "assistant", "uuid": "x1", "timestamp": "2026-10-01T08:00:05.000Z",
         "message": {"model": "claude-opus", "content": [{"type": "text", "text": "Looking."}, {"type": "tool_use", "id": "t1"}]}},
        {**base, "type": "user", "uuid": "r1", "timestamp": "2026-10-01T08:00:06.000Z", "toolUseResult": {"stdout": "x"},
         "message": {"content": [{"type": "tool_result", "content": "x"}]}},
        {**base, "type": "assistant", "uuid": "x2", "timestamp": "2026-10-01T08:01:00.000Z",
         "message": {"model": "claude-opus", "content": [{"type": "text", "text": "PortAudio froze the default device."}]}},
        {**base, "type": "system", "subtype": "turn_duration", "uuid": "sy1", "timestamp": "2026-10-01T08:01:01.000Z"},
        {**base, "type": "user", "uuid": "m1", "timestamp": "2026-10-01T08:02:00.000Z", "isMeta": True,
         "message": {"content": "meta"}},
        {**base, "type": "user", "uuid": "n1", "timestamp": "2026-10-01T08:02:01.000Z",
         "message": {"content": "<system-reminder>be careful</system-reminder>"}},
        {**base, "type": "user", "uuid": "n2", "timestamp": "2026-10-01T08:02:02.000Z",
         "message": {"content": "<command-name>/clear</command-name>"}},
        {**base, "type": "user", "uuid": "n3", "timestamp": "2026-10-01T08:02:03.000Z", "isSidechain": True,
         "message": {"content": "sidechain brief"}},
        {**base, "type": "attachment", "uuid": "q0", "timestamp": "2026-10-01T08:02:30.000Z",
         "attachment": {"type": "queued_command", "prompt": "also check the watch", "source_uuid": "q1",
                        "commandMode": "prompt", "timestamp": "2026-10-01T08:02:30.000Z"}},
        {**base, "type": "user", "uuid": "u2", "promptId": "p2", "timestamp": "2026-10-01T09:00:00.000Z",
         "message": {"content": "Ship the fix."}},
        {**base, "type": "user", "uuid": "c1", "timestamp": "2026-10-01T10:00:00.000Z", "isCompactSummary": True,
         "message": {"content": "This session is being continued... summary"}},
        {**base, "type": "system", "subtype": "away_summary", "uuid": "aw1", "timestamp": "2026-10-01T11:00:00.000Z",
         "content": "Goal: fix the speaker routing."},
        {"type": "ai-title", "aiTitle": "Speaker routing", "sessionId": sid},
        {**base, "type": "assistant", "uuid": "x3", "timestamp": "2026-10-01T11:30:00.000Z",
         "message": {"model": "claude-opus", "content": [{"type": "text", "text": "Shipped."}]}},
    ])
    sub = proj / sid / "subagents"
    sub.mkdir(parents=True)
    _jl(sub / "agent-abc.jsonl", [
        {"type": "user", "timestamp": "2026-10-01T08:30:00.000Z", "message": {"content": "Find the HAL selector."}},
        {"type": "assistant", "timestamp": "2026-10-01T08:31:00.000Z", "message": {"model": "m", "content": [{"type": "text", "text": "It is kAudioHardwarePropertyDefaultOutputDevice."}]}},
    ])
    (sub / "agent-abc.meta.json").write_text(json.dumps({"agentType": "Explore", "description": "Find the selector"}))
    mem = proj / "memory"
    mem.mkdir()
    (mem / "MEMORY.md").write_text("# Memory index\n- [x](x.md)\n")
    (mem / "speaker.md").write_text("---\nname: Speaker routing\ndescription: frozen default device\n---\nUse UID.\n")
    _jl(proj / "s-live.jsonl", [{**base, "sessionId": "s-live", "type": "user", "uuid": "z", "timestamp": "2026-10-02T00:00:00Z",
                                 "message": {"content": "live session"}}])
    return proj


def test_claude_sessions_notes_time_and_provenance(claude_project):
    drafts, report = _drafts(claude_sessions.drafts, path=claude_project.parent, exclude_sessions=["s-live"], author="me")
    kinds = sorted(d.kind for d in drafts)
    assert kinds == ["away_summary", "compaction_summary", "memory", "prompt", "prompt", "prompt", "session_title", "subagent"]
    first = next(d for d in drafts if d.source_key == "s-1:u1")
    assert first.text == ("Why does the tray show the wrong speaker?\n\n## Outcome\n\nPortAudio froze the default device.")
    assert first.occurred_at == "2026-10-01T08:00:01Z"
    assert first.attachments == [{"data": PNG, "mime": "image/png"}]
    assert first.source["author"] == "me" and first.source["importer"] == "claude-sessions"
    assert first.source["origin"] == {"session_id": "s-1", "uuid": "u1", "prompt_id": "p1", "cwd": "/Users/me/repo",
                                      "git_branch": "main", "model": "claude-opus",
                                      "file": str(claude_project / "s-1.jsonl"), "line": 3}
    second = next(d for d in drafts if d.source_key == "s-1:u2")
    assert second.text == "Ship the fix.\n\n## Outcome\n\nShipped."
    queued = next(d for d in drafts if d.source_key == "s-1:q1")
    assert queued.text == "also check the watch" and queued.occurred_at == "2026-10-01T08:02:30Z"
    sub = next(d for d in drafts if d.kind == "subagent")
    assert sub.title == "Find the selector" and "## Report" in sub.text and sub.source["author"] == "Explore"
    mem = next(d for d in drafts if d.kind == "memory")
    assert mem.title == "Speaker routing" and mem.source_key.startswith("memory:-Users-me-repo:speaker.md:")
    assert report.skipped["tool_result"] == 1 and report.skipped["meta"] == 1
    assert report.skipped["system_reminder"] == 1 and report.skipped["command"] == 1
    assert report.skipped["sidechain"] == 1 and report.skipped["excluded_session"] == 1
    assert report.skipped["memory_index"] == 1 and report.skipped["attachment"] == 1


def test_memory_files_take_the_origin_session_date_then_modified_then_git_then_mtime(tmp_path):
    projects = tmp_path / "projects"
    proj, other = projects / "-Users-me-repo", projects / "-Users-me-other"
    (proj / "memory").mkdir(parents=True)
    other.mkdir()
    _jl(proj / "s-here.jsonl", [{"type": "mode", "mode": "normal"},
                                {"type": "user", "uuid": "u", "timestamp": "2026-09-01T07:00:00.000Z", "message": {"content": "hi"}},
                                {"type": "user", "uuid": "v", "timestamp": "2026-09-03T07:00:00.000Z", "message": {"content": "later"}}])
    _jl(other / "s-there.jsonl", [{"type": "user", "uuid": "w", "timestamp": "2026-08-15T10:00:00+02:00", "message": {"content": "x"}}])
    fm = "---\nname: {n}\nmetadata:\n  type: feedback\n{extra}---\n\nBody {n}.\n"
    mem = proj / "memory"
    (mem / "a.md").write_text(fm.format(n="a", extra="  originSessionId: s-here\n  modified: 2026-10-10T00:00:00.000Z\n"))
    (mem / "b.md").write_text(fm.format(n="b", extra="  originSessionId: s-there\n"))
    (mem / "c.md").write_text(fm.format(n="c", extra="  originSessionId: s-gone\n  modified: 2026-10-11T12:30:00.000Z\n"))
    (mem / "d.md").write_text("No front matter, committed.\n")
    (mem / "e.md").write_text("No front matter, not committed.\n")
    _git(mem, "init", "-q")
    _git(mem, "add", "d.md")
    _git(mem, "commit", "-q", "-m", "d", env={"GIT_AUTHOR_DATE": "2026-07-04T05:06:07+00:00",
                                               "GIT_COMMITTER_DATE": "2026-07-04T05:06:07+00:00"})
    os.utime(mem / "e.md", (1760000000, 1760000000))
    drafts, report = _drafts(claude_sessions.drafts, path=proj)
    got = {d.title: (d.occurred_at, d.source["origin"]["date_basis"]) for d in drafts if d.kind == "memory"}
    assert got == {"a": ("2026-09-01T07:00:00Z", "origin_session"),        # first record, not the later one
                   "b": ("2026-08-15T08:00:00Z", "origin_session"),        # found in a sibling project folder
                   "c": ("2026-10-11T12:30:00Z", "front_matter_modified"),  # origin session not on disk
                   "d": ("2026-07-04T05:06:07Z", "git_added"),
                   "e": (importers.iso_from_epoch(1760000000), "file_mtime")}
    a = next(d for d in drafts if d.title == "a")
    assert a.source["origin"]["origin_session_id"] == "s-here"
    assert a.source_key.startswith("memory:-Users-me-repo:a.md:")  # the id still derives from the file content


def test_claude_sessions_window(claude_project):
    drafts, report = _drafts(claude_sessions.drafts, path=claude_project, since="2026-10-01T09:00:00Z",
                             until="2026-10-01T10:30:00Z", exclude_sessions=["s-live"])
    assert sorted(d.source_key for d in drafts if d.kind != "memory") == ["s-1:c1", "s-1:u2"]
    assert report.skipped["outside_window"] >= 4


def test_claude_import_is_idempotent_and_bulk(claude_project, notebook):
    r1 = importers.run("claude-sessions", notebook, path=claude_project, exclude_sessions=["s-live"])
    assert r1.created == 8 and r1.duplicates == 0 and r1.attachments == 1 and not r1.errors
    assert r1.earliest == "2026-10-01T08:00:01Z"
    r2 = importers.run("claude-sessions", notebook, path=claude_project, exclude_sessions=["s-live"])
    assert r2.created == 0 and r2.duplicates == 8
    dry = importers.run("claude-sessions", notebook, path=claude_project, dry_run=True)
    assert dry.created == 1 and dry.duplicates == 8  # only the not-yet-imported live session
    stats = notebook.queue.stats()
    assert stats["by_priority"]["0"] == 8 and stats["by_priority"]["1"] == 0
    from abstractnote import ids

    note = notebook.note(ids.note_id_for_import("claude-sessions", "s-1:u1"))
    assert note["occurred_at"] == "2026-10-01T08:00:01Z" and note["source"]["importer"] == "claude-sessions"


# --------------------------------------------------------------------------- agora


def _agora_db(path: Path) -> Path:
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE messages (id TEXT, channel TEXT, seq INTEGER, sender TEXT, kind TEXT, status TEXT, urgency TEXT,
          critical INTEGER, downgraded INTEGER, to_agents TEXT, title TEXT, body TEXT, data TEXT, reply_to TEXT,
          created_at REAL, hash TEXT, retracted_at REAL, retracted_by TEXT);
        CREATE TABLE store (channel TEXT, key TEXT, value TEXT, version INTEGER, updated_by TEXT, updated_at REAL);
        CREATE TABLE blobs (channel TEXT, id TEXT, filename TEXT, content_type TEXT, size INTEGER, bytes BLOB,
          created_by TEXT, created_at REAL);
    """)
    t0 = 1783500000.0  # 2026-07-08T...
    rows = [
        ("m1", "commons", 1, "framework", "message", "open", "Which port for Note?", "3006 or 3007?",
         json.dumps({"attachments": [{"id": "sha-png", "filename": "ports.png", "content_type": "image/png"}]}), None, t0),
        ("m2", "commons", 2, "gateway", "message", "reply", None, "3006: next in the stack map.", None, "m1", t0 + 60),
        ("m3", "commons", 3, "laurent", "message", "reply", None, "Agreed, 3006.", None, "m2", t0 + 120),
        ("m4", "commons", 4, "memory", "message", "resolved", "Close the B1 gap", "close_assertion landed.", None, None, t0 + 200),
        ("m5", "commons", 5, "core", "message", "open", "Unanswered ask", "anyone?", None, None, t0 + 300),
        ("m6", "commons", 6, "hub", "system", "fyi", None, "presence", None, None, t0 + 400),
        ("m7", "hub-alerts", 1, "hub", "message", "fyi", None, "silence", None, None, t0 + 500),
        ("m8", "dm:framework--laurent", 1, "laurent", "message", "fyi", None, "Never release without my go.", None, None, t0 + 600),
        ("m9", "commons", 7, "laurent", "message", "fyi", None, "oops", None, None, t0 + 700),
    ]
    for (mid, ch, seq, sender, kind, status, title, body, data, reply_to, created) in rows:
        conn.execute("INSERT INTO messages (id, channel, seq, sender, kind, status, title, body, data, reply_to, created_at, hash, retracted_at)"
                     " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (mid, ch, seq, sender, kind, status, title, body, data, reply_to, created, "h-" + mid,
                      t0 + 800 if mid == "m9" else None))
    conn.execute("INSERT INTO store VALUES (?,?,?,?,?,?)", ("commons", "decision:note-port", json.dumps(
        {"summary": "Note listens on 3006.", "message_id": "m1"}), 2, "framework", t0 + 130))
    conn.execute("INSERT INTO store VALUES (?,?,?,?,?,?)", ("commons", "claim:note-web", json.dumps({"summary": "web lane owns web/"}),
                                                            1, "web", t0 + 140))
    conn.execute("INSERT INTO store VALUES (?,?,?,?,?,?)", ("commons", "gate:x", "{}", 1, "x", t0))
    conn.execute("INSERT INTO blobs VALUES (?,?,?,?,?,?,?,?)", ("commons", "sha-png", "ports.png", "image/png", len(PNG), PNG, "framework", t0))
    conn.commit()
    conn.close()
    return path


def test_agora_decisions_threads_and_operator_messages(tmp_path):
    db = _agora_db(tmp_path / "agora.db")
    drafts, report = _drafts(agora.drafts, path=db)
    by_key = {d.source_key: d for d in drafts}
    assert sorted(by_key) == ["message:m8", "store:commons:claim:note-web:1", "store:commons:decision:note-port:2",
                              "thread:m1", "thread:m4"]
    decision = by_key["store:commons:decision:note-port:2"]
    assert decision.kind == "decision" and decision.text.startswith("Note listens on 3006.")
    assert decision.occurred_at == importers.iso_from_epoch(1783500000.0)  # the message's time, not the store's
    thread = by_key["thread:m1"]
    assert thread.kind == "answered_thread" and "## gateway" in thread.text and "## laurent" in thread.text
    assert thread.attachments[0]["data"] == PNG and thread.source["origin"]["replies"] == 2
    assert by_key["thread:m4"].kind == "resolved_thread"
    assert by_key["message:m8"].kind == "operator_message" and by_key["message:m8"].source["author"] == "laurent"
    assert report.skipped["kind_system"] == 1 and report.skipped["hub_alerts"] == 1 and report.skipped["retracted"] == 1


def test_agora_channel_filter_and_idempotence(tmp_path, notebook):
    db = _agora_db(tmp_path / "agora.db")
    only_dm, _ = _drafts(agora.drafts, path=db, channels=["dm:framework--laurent"])
    assert [d.source_key for d in only_dm] == ["message:m8"]
    r1 = importers.run("agora", notebook, path=db)
    r2 = importers.run("agora", notebook, path=db)
    assert (r1.created, r2.created, r2.duplicates) == (5, 0, 5)


# --------------------------------------------------------------------------- Markdown


def _git(repo: Path, *args: str, env=None) -> None:
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=T", "-c", "user.email=t@example.com", *args],
                   check=True, capture_output=True, env={**os.environ, **(env or {})})


def test_markdown_dates_titles_and_git_provenance(tmp_path):
    repo = tmp_path / "repo"
    (repo / "docs" / "adr").mkdir(parents=True)
    (repo / "node_modules").mkdir()
    (repo / "docs" / "adr" / "0001-x.md").write_text("# ADR 0001: Gateway first\n\nBody.\n")
    (repo / "docs" / "note.md").write_text("---\ntitle: A dated note\ndate: 2024-05-01\n---\nText.\n")
    (repo / "node_modules" / "skip.md").write_text("# no\n")
    (repo / "empty.md").write_text("  \n")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "add", env={"GIT_AUTHOR_DATE": "2026-01-02T03:04:05+00:00",
                                                 "GIT_COMMITTER_DATE": "2026-01-02T03:04:05+00:00"})
    drafts, report = _drafts(markdown.drafts, path=repo)
    by_path = {d.source["origin"]["path"]: d for d in drafts}
    assert sorted(by_path) == ["docs/adr/0001-x.md", "docs/note.md"]
    adr = by_path["docs/adr/0001-x.md"]
    assert adr.title == "ADR 0001: Gateway first" and adr.occurred_at == "2026-01-02T03:04:05Z"
    assert adr.source["origin"]["repo"] == "repo" and len(adr.source["origin"]["commit"]) == 40
    assert len(adr.source["origin"]["blob_sha"]) == 40
    dated = by_path["docs/note.md"]
    assert dated.title == "A dated note" and dated.occurred_at == "2024-05-01T00:00:00Z"
    assert dated.text.startswith("---\ntitle: A dated note")  # front matter kept
    assert report.skipped["excluded_dir"] == 1 and report.skipped["empty"] == 1


def test_markdown_edit_is_a_new_note(tmp_path, notebook):
    root = tmp_path / "notes"
    root.mkdir()
    f = root / "a.md"
    f.write_text("# A\n\nfirst\n")
    assert importers.run("markdown", notebook, path=root).created == 1
    assert importers.run("markdown", notebook, path=root).created == 0
    f.write_text("# A\n\nsecond\n")
    assert importers.run("markdown", notebook, path=root).created == 1


# --------------------------------------------------------------------------- files


def test_files_store_bytes_with_file_time(tmp_path, notebook):
    d = tmp_path / "in"
    d.mkdir()
    (d / "log.txt").write_text("boot ok\n")
    (d / "pixel.png").write_bytes(PNG)
    (d / ".DS_Store").write_bytes(b"x")
    os.utime(d / "log.txt", (1760000000, 1760000000))
    drafts, report = _drafts(files.drafts, paths=[d])
    assert sorted(x.title for x in drafts) == ["log.txt", "pixel.png"]
    log = next(x for x in drafts if x.title == "log.txt")
    assert log.occurred_at == importers.iso_from_epoch(1760000000) and log.attachments[0]["data"] == b"boot ok\n"
    assert report.skipped["hidden_file"] == 1
    r1 = importers.run("files", notebook, paths=[d])
    assert r1.created == 2 and r1.attachments == 2
    (d / "copy.txt").write_text("boot ok\n")  # same bytes elsewhere: the same note
    r2 = importers.run("files", notebook, paths=[d])
    assert (r2.created, r2.duplicates) == (0, 3)


def test_cli_dry_run_writes_nothing(tmp_path, capsys, claude_project):
    from abstractnote.importers.cli import main

    nb_dir = tmp_path / "fresh-nb"
    assert main(["claude-sessions", str(claude_project), "--notebook", str(nb_dir), "--dry-run",
                 "--exclude-session", "s-live"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is True and out["created"] == 8 and not nb_dir.exists()


def test_a_queued_prompt_with_a_pasted_image_stores_text_and_an_attachment(tmp_path, notebook):
    # Evaluation-lane bug: a queued prompt with images was stored as the repr of its content list.
    proj = tmp_path / "proj"
    proj.mkdir()
    blocks = [{"type": "text", "text": "what is wrong on this screen?"},
              {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                           "data": base64.b64encode(PNG).decode()}}]
    _jl(proj / "s-q.jsonl", [{"type": "attachment", "uuid": "a1", "sessionId": "s-q", "timestamp": "2026-10-03T10:00:00Z",
                              "attachment": {"type": "queued_command", "prompt": blocks, "source_uuid": "q9",
                                             "commandMode": "prompt", "timestamp": "2026-10-03T10:00:00Z"}}])
    drafts, _ = _drafts(claude_sessions.drafts, path=proj)
    (d,) = drafts
    assert d.text == "what is wrong on this screen?" and "base64" not in d.text and "{'type'" not in d.text
    assert d.attachments == [{"data": PNG, "mime": "image/png"}]
    report = importers.run("claude-sessions", notebook, path=proj)
    assert report.created == 1 and report.attachments == 1
    from abstractnote import ids

    note = notebook.note(ids.note_id_for_import("claude-sessions", "s-q:q9"))
    assert note["text"] == "what is wrong on this screen?"
    (att,) = note["attachments"]
    assert att["mime"] == "image/png" and att["size"] == len(PNG) and len(att["sha256"]) == 64


def test_cli_import_takes_the_notebook_embedder(tmp_path, monkeypatch, capsys):
    from abstractnote.importers import cli
    from abstractnote.store.notebook import Notebook

    seen = {}
    real_open = Notebook.open

    def spy(path, embedder=None, **kw):
        seen["embedder"] = embedder
        return real_open(path, **kw)

    monkeypatch.setattr(Notebook, "open", staticmethod(spy))
    root = tmp_path / "md"
    root.mkdir()
    (root / "a.md").write_text("# A\n")
    assert cli.main(["markdown", str(root), "--notebook", str(tmp_path / "nb"), "--embed-base-url",
                     "http://127.0.0.1:9/v1", "--embed-model", "m-1"]) == 0
    from abstractnote.store.embeddings import embedder_model_id

    assert embedder_model_id(seen["embedder"]) == "m-1"
    capsys.readouterr()
    assert cli.main(["markdown", str(root), "--notebook", str(tmp_path / "nb"), "--embed-model", "m-1"]) == 2
    assert "Give both --embed-base-url and --embed-model." in capsys.readouterr().err
