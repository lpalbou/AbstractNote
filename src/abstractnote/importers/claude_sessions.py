"""Claude Code session transcripts -> notes (docs/importers.md, recon data-sources.md section A).

One note per human-typed prompt, with the final assistant text of that turn as its outcome; one per
session title, compaction summary and away summary; one per subagent run (its brief and its last
report); one per curated memory file. ``occurred_at`` is the record's own timestamp; a memory
file (no record of its own) takes its origin session's first record (front matter
``originSessionId``, looked up in this project folder, then in its sibling project folders), else
the front matter ``modified``, else the commit that first added it, else its modification time.
``source.origin.date_basis`` says which.

DIR is a project directory (``~/.claude/projects/<project>/``: ``<session>.jsonl``,
``<session>/subagents/agent-*.jsonl`` + ``.meta.json``, ``memory/*.md``) or a directory of them.
Skipped as machine noise (by record type and flags, and by the harness's own envelope markers):
tool results, hook output and other attachments, meta records, sidechains, system records other
than away summaries, slash and shell commands, task notifications, system reminders, interruption
markers. Images in prompts are stored as attachments (by hash), never inlined.
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence

from . import ImportReport, NoteDraft, in_window, iso_from_epoch, iso_utc

IMPORTER = "claude-sessions"

# Envelopes the harness itself writes into user records (structure, not meaning).
_ENVELOPES = {
    "<task-notification>": "task_notification",
    "<local-command-": "local_command",
    "<command-name>": "command",
    "<command-message>": "command",
    "<bash-input>": "command",
    "<bash-stdout>": "command_output",
    "<bash-stderr>": "command_output",
    "<system-reminder>": "system_reminder",
    "[Request interrupted": "interrupted",
    "Caveat: The messages below were generated": "local_command",
}


def _project_dirs(root: Path) -> List[Path]:
    if any(root.glob("*.jsonl")) or (root / "memory").is_dir():
        return [root]
    return sorted(p for p in root.iterdir() if p.is_dir() and (any(p.glob("*.jsonl")) or (p / "memory").is_dir()))


def _text_and_images(content: Any) -> tuple[str, List[Dict[str, Any]], bool]:
    """(text, images, has_tool_result) of a message content (a string or a list of blocks)."""
    if isinstance(content, str):
        return content, [], False
    texts: List[str] = []
    images: List[Dict[str, Any]] = []
    tool_result = False
    for block in content or []:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text" and isinstance(block.get("text"), str):
            texts.append(block["text"])
        elif kind == "image":
            src = block.get("source") or {}
            if src.get("type") == "base64" and isinstance(src.get("data"), str):
                try:
                    images.append({"data": base64.b64decode(src["data"]), "mime": src.get("media_type") or "image/png"})
                except (ValueError, TypeError):
                    pass
        elif kind == "tool_result":
            tool_result = True
    return "\n\n".join(t for t in texts if t.strip()), images, tool_result


def _envelope(text: str) -> Optional[str]:
    head = text.lstrip()
    for marker, reason in _ENVELOPES.items():
        if head.startswith(marker):
            return reason
    return None


def _source(origin: Dict[str, Any], author: str) -> Dict[str, Any]:
    return {"client": "importer", "importer": IMPORTER, "author": author,
            "origin": {k: v for k, v in origin.items() if v is not None}}


def _session_drafts(path: Path, report: ImportReport, author: str, since: Optional[str], until: Optional[str]
                    ) -> Iterator[NoteDraft]:
    sid = path.stem
    turn: Optional[Dict[str, Any]] = None
    title: Optional[Dict[str, Any]] = None
    last_ts: Optional[str] = None

    def finish(t: Optional[Dict[str, Any]]) -> Iterator[NoteDraft]:
        if t is None:
            return
        body = t["text"]
        if t.get("outcome"):
            body = body + "\n\n## Outcome\n\n" + t["outcome"]
        origin = {"session_id": sid, "uuid": t["uuid"], "prompt_id": t.get("prompt_id"), "cwd": t.get("cwd"),
                  "git_branch": t.get("git_branch"), "model": t.get("model"), "file": str(path), "line": t["line"]}
        if in_window(t["ts"], since, until):
            yield NoteDraft(IMPORTER, f"{sid}:{t['uuid']}", body, t["ts"], t["kind"], None, _source(origin, author),
                            t["images"])
        else:
            report.skip("outside_window")

    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line_no, line in enumerate(fh, start=1):
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                report.skip("unreadable_line")
                continue
            kind = r.get("type")
            ts = iso_utc(r["timestamp"]) if isinstance(r.get("timestamp"), str) else None
            last_ts = ts or last_ts
            if kind == "ai-title" and r.get("aiTitle"):
                title = {"text": str(r["aiTitle"]), "line": line_no}
                continue
            if kind == "system":
                if r.get("subtype") == "away_summary" and isinstance(r.get("content"), str) and ts:
                    origin = {"session_id": sid, "uuid": r.get("uuid"), "cwd": r.get("cwd"), "git_branch": r.get("gitBranch"),
                              "file": str(path), "line": line_no}
                    if in_window(ts, since, until):
                        yield NoteDraft(IMPORTER, f"{sid}:{r.get('uuid')}", r["content"], ts, "away_summary",
                                        "While you were away", _source(origin, "claude-code"))
                    else:
                        report.skip("outside_window")
                else:
                    report.skip("system")
                continue
            if kind == "attachment":
                att = r.get("attachment") or {}
                # A queued prompt is a string or, with pasted images, a content-block list like a user
                # message: text blocks become the note, images become attachments (never a repr).
                qtext, qimages, _qtr = _text_and_images(att.get("prompt"))
                if att.get("type") == "queued_command" and att.get("commandMode") == "prompt" and (qtext.strip() or qimages):
                    qts = iso_utc(att["timestamp"]) if isinstance(att.get("timestamp"), str) else ts
                    origin = {"session_id": sid, "uuid": att.get("source_uuid") or r.get("uuid"), "cwd": r.get("cwd"),
                              "git_branch": r.get("gitBranch"), "file": str(path), "line": line_no, "queued": True}
                    if qts and in_window(qts, since, until):
                        yield NoteDraft(IMPORTER, f"{sid}:{origin['uuid']}", qtext.strip(), qts, "prompt", None,
                                        _source(origin, author), qimages)
                    else:
                        report.skip("outside_window")
                else:
                    report.skip("attachment")
                continue
            if kind == "assistant":
                if r.get("isSidechain"):
                    continue
                msg = r.get("message") or {}
                text, _imgs, _tr = _text_and_images(msg.get("content"))
                if turn is not None and text.strip():
                    turn["outcome"] = text.strip()
                    turn["model"] = msg.get("model") or turn.get("model")
                continue
            if kind != "user":
                report.skip(kind or "untyped")
                continue
            if r.get("isSidechain"):
                report.skip("sidechain")
                continue
            if r.get("toolUseResult") is not None:
                report.skip("tool_result")
                continue
            msg = r.get("message") or {}
            text, images, tool_result = _text_and_images(msg.get("content"))
            if tool_result:
                report.skip("tool_result")
                continue
            if r.get("isCompactSummary"):
                if ts and text.strip():
                    origin = {"session_id": sid, "uuid": r.get("uuid"), "cwd": r.get("cwd"), "git_branch": r.get("gitBranch"),
                              "file": str(path), "line": line_no}
                    if in_window(ts, since, until):
                        yield NoteDraft(IMPORTER, f"{sid}:{r.get('uuid')}", text, ts, "compaction_summary",
                                        "Session summary", _source(origin, "claude-code"))
                    else:
                        report.skip("outside_window")
                continue
            if r.get("isMeta"):
                report.skip("meta")
                continue
            origin_kind = (r.get("origin") or {}).get("kind") if isinstance(r.get("origin"), dict) else None
            if origin_kind not in (None, "human"):
                report.skip(f"origin_{origin_kind}")
                continue
            envelope = _envelope(text)
            if envelope:
                report.skip(envelope)
                continue
            if not text.strip() and not images:
                report.skip("empty")
                continue
            if ts is None:
                report.skip("no_timestamp")
                continue
            yield from finish(turn)
            turn = {"uuid": r.get("uuid"), "prompt_id": r.get("promptId"), "cwd": r.get("cwd"), "git_branch": r.get("gitBranch"),
                    "text": text.strip(), "images": images, "ts": ts, "line": line_no, "kind": "prompt"}
    yield from finish(turn)
    if title and last_ts:
        origin = {"session_id": sid, "file": str(path), "line": title["line"]}
        if in_window(last_ts, since, until):
            yield NoteDraft(IMPORTER, f"{sid}:title", title["text"], last_ts, "session_title", title["text"],
                            _source(origin, "claude-code"))


def _first_last(path: Path) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """(brief, last report, first timestamp, model) of one subagent transcript."""
    brief = report_text = first_ts = model = None
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if first_ts is None and isinstance(r.get("timestamp"), str):
                first_ts = iso_utc(r["timestamp"])
            msg = r.get("message") or {}
            if r.get("type") == "user" and brief is None and r.get("toolUseResult") is None:
                text, _i, tool_result = _text_and_images(msg.get("content"))
                if text.strip() and not tool_result:
                    brief = text.strip()
            elif r.get("type") == "assistant":
                text, _i, _t = _text_and_images(msg.get("content"))
                if text.strip():
                    report_text = text.strip()
                    model = msg.get("model") or model
    return brief, report_text, first_ts, model


def _subagent_drafts(session_dir: Path, report: ImportReport, since: Optional[str], until: Optional[str]) -> Iterator[NoteDraft]:
    sid = session_dir.name
    for path in sorted((session_dir / "subagents").glob("agent-*.jsonl")):
        agent_id = path.stem[len("agent-"):]
        meta_path = path.with_suffix(".meta.json")
        meta: Dict[str, Any] = {}
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                meta = {}
        brief, last, ts, model = _first_last(path)
        if not brief or ts is None:
            report.skip("subagent_without_brief")
            continue
        if not in_window(ts, since, until):
            report.skip("outside_window")
            continue
        body = "## Brief\n\n" + brief + ("\n\n## Report\n\n" + last if last else "")
        origin = {"session_id": sid, "agent_id": agent_id, "agent_type": meta.get("agentType"),
                  "description": meta.get("description"), "model": model or meta.get("model"),
                  "finished": meta.get("finished"), "file": str(path)}
        yield NoteDraft(IMPORTER, f"{sid}:agent:{agent_id}", body, ts, "subagent", meta.get("description"),
                        _source(origin, str(meta.get("agentType") or "subagent")))


def _front_matter(text: str) -> tuple[Dict[str, str], str]:
    """``key: value`` pairs of a ``---`` front matter block.

    Top-level keys keep their name; a key indented under a parent (``metadata:`` then
    ``  originSessionId: ...``) is kept as ``parent.key``. Deeper or list-valued blocks stay text only.
    """
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    block = text[3:end]
    out: Dict[str, str] = {}
    parent: Optional[str] = None
    for line in block.splitlines():
        if not line.strip() or ":" not in line:
            continue
        k, v = line.split(":", 1)
        value = v.strip().strip('"').strip("'")
        if not line[0].isspace():
            parent = k.strip()
            out[parent] = value
        elif parent and not out.get(parent) and not k.strip().startswith("-"):
            out[f"{parent}.{k.strip()}"] = value
    return out, text


def git_first_added(path: Path) -> Optional[tuple[str, str]]:
    """(commit, author date) of the commit that first added ``path``, following moves, or None.

    ``git log --follow --diff-filter=A`` lists every add along the file's rename chain, newest
    first; the last line is the original add.
    """
    import subprocess

    try:
        out = subprocess.run(["git", "-C", str(path.parent), "log", "--follow", "--diff-filter=A",
                              "--format=%H %aI", "--", path.name], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = [ln for ln in out.stdout.splitlines() if ln.strip()] if out.returncode == 0 else []
    if not lines:
        return None
    commit, _, date = lines[-1].partition(" ")
    return commit, date


def _first_timestamp(path: Path, max_lines: int = 500) -> Optional[str]:
    """The first record timestamp of a session transcript (reads line by line, never the whole file)."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for n, line in enumerate(fh):
                if n >= max_lines:
                    break
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(r, dict) and isinstance(r.get("timestamp"), str):
                    try:
                        return iso_utc(r["timestamp"])
                    except ValueError:
                        continue
    except OSError:
        return None
    return None


def _origin_session_start(project: Path, session_id: str, cache: Dict[str, Optional[str]]) -> Optional[str]:
    """First timestamp of ``<session_id>.jsonl`` in this project folder, else in a sibling project folder."""
    if session_id in cache:
        return cache[session_id]
    found: Optional[str] = None
    name = f"{session_id}.jsonl"
    candidates = [project / name] + sorted(p / name for p in project.parent.iterdir()
                                           if p.is_dir() and p != project)
    for candidate in candidates:
        if candidate.is_file():
            found = _first_timestamp(candidate)
            if found:
                break
    cache[session_id] = found
    return found


def memory_date(project: Path, path: Path, fm: Dict[str, str], cache: Dict[str, Optional[str]]
                ) -> tuple[str, str]:
    """(occurred_at, date_basis) of a memory file: the origin session's first record, else the
    front matter ``modified``, else the commit that first added the file, else its modification time."""
    sid = fm.get("originSessionId") or fm.get("metadata.originSessionId")
    if sid:
        start = _origin_session_start(project, sid, cache)
        if start:
            return start, "origin_session"
    modified = fm.get("modified") or fm.get("metadata.modified")
    if modified:
        try:
            return iso_utc(modified), "front_matter_modified"
        except ValueError:
            pass
    added = git_first_added(path)
    if added:
        try:
            return iso_utc(added[1]), "git_added"
        except ValueError:
            pass
    return iso_from_epoch(path.stat().st_mtime), "file_mtime"


def _memory_drafts(project: Path, report: ImportReport, since: Optional[str], until: Optional[str]) -> Iterator[NoteDraft]:
    mem = project / "memory"
    if not mem.is_dir():
        return
    cache: Dict[str, Optional[str]] = {}
    for path in sorted(mem.glob("*.md")):
        if path.name == "MEMORY.md":  # the index of the other files
            report.skip("memory_index")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if not text.strip():
            report.skip("empty")
            continue
        fm, _ = _front_matter(text)
        ts, basis = memory_date(project, path, fm, cache)
        if not in_window(ts, since, until):
            report.skip("outside_window")
            continue
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        origin = {"project": project.name, "file": str(path), "name": fm.get("name"), "sha256": digest,
                  "origin_session_id": fm.get("originSessionId") or fm.get("metadata.originSessionId"),
                  "date_basis": basis}
        yield NoteDraft(IMPORTER, f"memory:{project.name}:{path.name}:{digest[:16]}", text, ts, "memory",
                        fm.get("name") or path.stem, _source(origin, "claude-code"))


def drafts(*, report: ImportReport, path: str | Path, since: Optional[str] = None, until: Optional[str] = None,
           exclude_sessions: Sequence[str] = (), author: Optional[str] = None) -> Iterator[NoteDraft]:
    root = Path(path).expanduser()
    if not root.is_dir():
        raise ValueError(f"There is no folder at {path}.")
    who = author or getpass.getuser()
    excluded = {s.strip() for s in exclude_sessions if s and s.strip()}
    for project in _project_dirs(root):
        for session in sorted(project.glob("*.jsonl")):
            if session.stem in excluded:
                report.skip("excluded_session")
                continue
            yield from _session_drafts(session, report, who, since, until)
            if (project / session.stem / "subagents").is_dir():
                yield from _subagent_drafts(project / session.stem, report, since, until)
        yield from _memory_drafts(project, report, since, until)
