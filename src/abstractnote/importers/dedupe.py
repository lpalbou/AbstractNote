"""One note per source file (``abstractnote dedupe-sources``, eval2 D11).

A Claude Code curated memory file imported by both the ``markdown`` and the ``claude-sessions`` importers
(before the markdown importer handed memory files over, ``claude_sessions.memory_project``) is two notes with
the same content. :func:`plan` finds such pairs exactly: a ``claude-sessions`` memory note and a ``markdown``
note with the same file name and the same content hash (``source.origin.sha256``); :func:`dedupe_sources`
archives the later copy of each (by when it was received) in ONE user revision of ``note.archive`` ops that
name the copy kept (``duplicate_of``), each copy's reading withdrawn with it (W28, ``Notebook.archive_notes``).
Hashes and names decide, never the words (ADR 0004).
"""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional

ACTION = "note.dedupe"


def _origin(row: Any) -> Dict[str, Any]:
    try:
        src = json.loads(row["source_json"] or "{}") or {}
    except (TypeError, ValueError):
        return {}
    origin = src.get("origin") if isinstance(src, dict) else None
    return origin if isinstance(origin, dict) else {}


def plan(nb: Any) -> List[Dict[str, Any]]:
    """``[{keep, archive, file, sha256}]``: the live memory-file pairs, the earlier received copy kept."""
    q = nb.store.q
    base = ("SELECT note_id, received_at, source_json FROM notes WHERE archived_at IS NULL AND insight_kind IS NULL"
            " AND json_extract(source_json, '$.importer')=?")
    memory: Dict[tuple, List[Any]] = {}
    for r in q(base + " AND json_extract(source_json, '$.origin.file') IS NOT NULL", ("claude-sessions",)):
        o = _origin(r)
        if o.get("sha256") and PurePosixPath(str(o["file"])).parent.name == "memory":
            memory.setdefault((PurePosixPath(str(o["file"])).name, o["sha256"]), []).append(r)
    pairs: List[Dict[str, Any]] = []
    for r in q(base + " ORDER BY received_at, note_id", ("markdown",)):
        o = _origin(r)
        key = (PurePosixPath(str(o.get("path") or "")).name, o.get("sha256"))
        for other in memory.get(key, []):
            first, later = sorted((r, other), key=lambda x: (x["received_at"], x["note_id"]))
            pairs.append({"keep": first["note_id"], "archive": later["note_id"], "file": key[0], "sha256": key[1]})
    seen, out = set(), []
    for p in sorted(pairs, key=lambda p: (p["file"], p["archive"])):
        if p["archive"] not in seen and p["keep"] not in seen:
            seen.update((p["archive"], p["keep"]))
            out.append(p)
    return out


def dedupe_sources(nb: Any, account: Optional[str] = None, *, client: str = "cli", dry_run: bool = False
                   ) -> Dict[str, Any]:
    """Archive the later copy of every pair (:func:`plan`) in one revision: ``{dry_run, pairs, archived,
    revision_id}``. A second run finds nothing."""
    pairs = plan(nb)
    out: Dict[str, Any] = {"dry_run": dry_run, "pairs": pairs, "archived": 0, "revision_id": None}
    if dry_run or not pairs:
        return out
    n = len(pairs)
    summary = f"Archived {n} duplicate cop{'y' if n == 1 else 'ies'} of memory files imported twice."
    # W28: each copy's reading is withdrawn with it (its mentions no longer count twice); the kept copy is marked for
    # re-understanding only when it repeated a statement the copy withdraws
    res = nb.archive_notes([p["archive"] for p in pairs], account, "the same memory file imported twice",
                           client=client, target="sources", summary=summary, action=ACTION,
                           duplicate_of={p["archive"]: p["keep"] for p in pairs})
    out.update(archived=n, revision_id=res["revision_id"])
    return out
