"""A Markdown tree -> one note per file (docs/importers.md): ADRs, backlog items, notes.

The whole file is the note text (front matter and headings kept). ``occurred_at`` = a date in the
front matter (``date``, ``created``, ``created_at``), else the commit that first added the file,
else the file's modification time. ``source.origin`` = ``{repo, path, blob_sha, commit}`` when the
tree is a git checkout. The source key includes the content hash: an edited file imported again is
a new note that revisits the earlier one; an unchanged file is a duplicate.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Dict, Iterator, Optional, Tuple

from . import ImportReport, NoteDraft, in_window, iso_from_epoch, iso_utc
from .claude_sessions import _front_matter

IMPORTER = "markdown"
DATE_KEYS = ("date", "created", "created_at", "Date", "Created")
SKIPPED_DIRS = frozenset({".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__"})


def _git(root: Path, *args: str) -> Optional[str]:
    try:
        out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout if out.returncode == 0 else None


def _git_index(root: Path) -> Tuple[Optional[Path], Dict[str, str], Dict[str, Tuple[str, str]]]:
    """(toplevel, {path: blob sha}, {path: (first commit sha, its author date)}) or empty maps."""
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
    first: Dict[str, Tuple[str, str]] = {}
    log = _git(toplevel, "log", "--reverse", "--diff-filter=A", "--name-only", "--format=@@%H %aI") or ""
    commit = date = None
    for line in log.splitlines():
        if line.startswith("@@"):
            commit, date = line[2:].split(" ", 1)
        elif line.strip() and commit and line not in first:
            first[line] = (commit, date)
    return toplevel, blobs, first


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
        occurred = _date_from_front_matter(fm) or (iso_utc(commit[1]) if commit else iso_from_epoch(file.stat().st_mtime))
        if not in_window(occurred, since, until):
            report.skip("outside_window")
            continue
        digest = hashlib.sha256(raw).hexdigest()
        origin = {"repo": repo, "path": rel, "blob_sha": blobs.get(rel), "commit": commit[0] if commit else None,
                  "sha256": digest}
        source = {"client": "importer", "importer": IMPORTER, "author": fm.get("author") or None,
                  "origin": {k: v for k, v in origin.items() if v is not None}}
        yield NoteDraft(IMPORTER, f"{repo}:{rel}:{digest}", text, occurred, "markdown", _title(text, fm, file),
                        {k: v for k, v in source.items() if v is not None})
