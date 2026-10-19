"""A Markdown tree -> one note per file (docs/importers.md): ADRs, backlog items, notes.

The whole file is the note text (front matter and headings kept). ``occurred_at`` = a date in the
front matter (``date``, ``created``, ``created_at``), else a ``Created:`` field line anywhere in
the file, else a ``Date:`` field line in its first 40 lines (``- **Created:** 2026-09-12``, ``Date:
2026-06-03``: a labelled metadata line, list marker and bold allowed, the value starting with an ISO
date), else the commit that first added the file following moves (``git log --follow
--diff-filter=A``), else the file's modification time; ``source.origin.date_basis`` says which. A
``Completed:`` field line is kept as written in ``source.origin.completed``. ``source.origin`` =
``{repo, path, blob_sha, commit, added_as}`` when the tree is a git checkout. The source key
includes the content hash: an edited file imported again is a new note that revisits the earlier
one; an unchanged file is a duplicate.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path
from typing import Dict, Iterator, Optional, Tuple

from . import ImportReport, NoteDraft, in_window, iso_from_epoch, iso_utc
from .claude_sessions import _front_matter

IMPORTER = "markdown"
DATE_KEYS = ("date", "created", "created_at", "Date", "Created")
SKIPPED_DIRS = frozenset({".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__"})
DATE_LINE_HEAD = 40  # a ``Date:`` line counts only near the top (later ones date completion reports)
# A labelled metadata line (structure, not meaning): optional list marker and bold/italic markers
# around the label, then a value that starts with an ISO date (optionally a time).
_FIELD = re.compile(r"^\s*(?:[-*+]\s+)?[*_]{0,2}(created|date|completed)[*_]{0,2}\s*:\s*[*_]{0,2}\s*"
                    r"(\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?)",
                    re.IGNORECASE)


def _git(root: Path, *args: str) -> Optional[str]:
    try:
        out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout if out.returncode == 0 else None


def _git_index(root: Path) -> Tuple[Optional[Path], Dict[str, str], Dict[str, Tuple[str, str, str]]]:
    """(toplevel, {path: blob sha}, {path: (first commit sha, its author date, path it was added as)}).

    One pass over the history, oldest first, with rename detection: an add records the commit; a
    rename hands the new path the old path's first add. This is ``git log --follow --diff-filter=A``
    for every file at once (one process instead of one per file).
    """
    top = _git(root, "rev-parse", "--show-toplevel")
    if not top:
        return None, {}, {}
    toplevel = Path(top.strip())
    blobs: Dict[str, str] = {}
    for line in (_git(toplevel, "ls-files", "-s") or "").splitlines():
        meta, _, rel = line.partition("\t")
        parts = meta.split()
        if len(parts) >= 2 and rel:
            blobs[rel] = parts[1]
    first: Dict[str, Tuple[str, str, str]] = {}
    log = _git(toplevel, "-c", "core.quotePath=false", "log", "--reverse", "-M", "--diff-filter=AR",
               "--name-status", "--format=@@%H %aI") or ""
    commit = date = None
    for line in log.splitlines():
        if line.startswith("@@"):
            commit, date = line[2:].split(" ", 1)
            continue
        fields = line.split("\t")
        if not (commit and date) or len(fields) < 2:
            continue
        status = fields[0]
        if status == "A" and fields[1] not in first:
            first[fields[1]] = (commit, date, fields[1])
        elif status.startswith("R") and len(fields) >= 3:
            first[fields[2]] = first.get(fields[1]) or first.get(fields[2]) or (commit, date, fields[2])
    return toplevel, blobs, first


def _fields(text: str) -> Dict[str, str]:
    """First ``Created:`` (anywhere), ``Date:`` (first DATE_LINE_HEAD lines) and ``Completed:``
    (anywhere) field values of a Markdown file, as written."""
    out: Dict[str, str] = {}
    for n, line in enumerate(text.splitlines()):
        m = _FIELD.match(line)
        if not m:
            continue
        key = m.group(1).lower()
        if key in out or (key == "date" and n >= DATE_LINE_HEAD):
            continue
        out[key] = m.group(2)
    return out


def _iso_day(value: str) -> Optional[str]:
    try:
        return iso_utc(value.replace(" ", "T", 1) if "T" in value or " " in value else value + "T00:00:00+00:00")
    except ValueError:
        return None


def _date_from_front_matter(fm: Dict[str, str]) -> Optional[str]:
    for key in DATE_KEYS:
        value = fm.get(key)
        if value:
            try:
                return iso_utc(value if "T" in value else value + "T00:00:00+00:00")
            except ValueError:
                continue
    return None


def _title(text: str, fm: Dict[str, str], path: Path) -> str:
    if fm.get("title"):
        return fm["title"]
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return path.stem


def drafts(*, report: ImportReport, path: str | Path, glob: str = "**/*.md", since: Optional[str] = None,
           until: Optional[str] = None) -> Iterator[NoteDraft]:
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"There is no folder at {path}.")
    toplevel, blobs, first = _git_index(root)
    repo = toplevel.name if toplevel else root.name
    for file in sorted(root.glob(glob)):
        if not file.is_file():
            continue
        rel_root = file.relative_to(root).as_posix()
        if SKIPPED_DIRS.intersection(file.relative_to(root).parts[:-1]):
            report.skip("excluded_dir")
            continue
        raw = file.read_bytes()
        text = raw.decode("utf-8", errors="replace")
        if not text.strip():
            report.skip("empty")
            continue
        fm, _ = _front_matter(text)
        rel = file.relative_to(toplevel).as_posix() if toplevel else rel_root
        commit = first.get(rel)
        fields = _fields(text)
        occurred, basis = _date_from_front_matter(fm), "front_matter"
        if occurred is None and fields.get("created"):
            occurred, basis = _iso_day(fields["created"]), "created_line"
        if occurred is None and fields.get("date"):
            occurred, basis = _iso_day(fields["date"]), "date_line"
        if occurred is None and commit:
            occurred, basis = iso_utc(commit[1]), "git_added"
        if occurred is None:
            occurred, basis = iso_from_epoch(file.stat().st_mtime), "file_mtime"
        if not in_window(occurred, since, until):
            report.skip("outside_window")
            continue
        digest = hashlib.sha256(raw).hexdigest()
        origin = {"repo": repo, "path": rel, "blob_sha": blobs.get(rel), "commit": commit[0] if commit else None,
                  "added_as": commit[2] if commit and commit[2] != rel else None, "sha256": digest,
                  "date_basis": basis, "completed": fields.get("completed")}
        source = {"client": "importer", "importer": IMPORTER, "author": fm.get("author") or None,
                  "origin": {k: v for k, v in origin.items() if v is not None}}
        yield NoteDraft(IMPORTER, f"{repo}:{rel}:{digest}", text, occurred, "markdown", _title(text, fm, file),
                        {k: v for k, v in source.items() if v is not None})
