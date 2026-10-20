"""Commit-instant dates (eval2 D12): a Markdown file dated by git takes the earlier of its first commit and its
Created line, and a bulk commit (>= BULK_COMMIT_FILES files added in the same second) is no date: the file's own
Created/Date line, else the earlier of its modification time and the commit, basis ``bulk_commit`` (approximate)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from abstractnote import importers
from abstractnote.importers import ImportReport, markdown

BULK = {"GIT_AUTHOR_DATE": "2026-05-26T10:00:00+00:00", "GIT_COMMITTER_DATE": "2026-05-26T10:00:00+00:00"}
LATER = {"GIT_AUTHOR_DATE": "2026-06-01T08:00:00+00:00", "GIT_COMMITTER_DATE": "2026-06-01T08:00:00+00:00"}


def _git(repo: Path, *args: str, env=None) -> None:
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=T", "-c", "user.email=t@example.com", *args],
                   check=True, capture_output=True,
                   env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", **(env or {})})


def test_bulk_commits_and_created_lines(tmp_path):
    repo = tmp_path / "repo"
    (repo / "adr").mkdir(parents=True)
    n = markdown.BULK_COMMIT_FILES
    for i in range(n - 2):
        (repo / "adr" / f"{i:04d}.md").write_text(f"# ADR {i}\n\nBody {i}.\n")
    (repo / "adr" / "old.md").write_text("# Old\n\nEdited before the import commit.\n")
    os.utime(repo / "adr" / "old.md", (1775000000, 1775000000))  # 2026-03-31, before the bulk commit
    (repo / "adr" / "created.md").write_text("# Created\n\n- **Created:** 2025-09-12\n")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "import", env=BULK)
    (repo / "late.md").write_text("# Late\n\n- Created: 2026-06-10\n")  # its Created line is after its commit
    (repo / "plain.md").write_text("# Plain\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "two more", env=LATER)
    report = ImportReport(importer="markdown")
    drafts = list(markdown.drafts(report=report, path=repo))
    importers.write_drafts(None, drafts, report, dry_run=True)
    got = {d.source["origin"]["path"]: (d.occurred_at, d.source["origin"]["date_basis"]) for d in drafts}
    assert got["adr/old.md"] == (importers.iso_from_epoch(1775000000), "bulk_commit")
    assert got["adr/0000.md"] == ("2026-05-26T10:00:00Z", "bulk_commit")  # checked out after: the commit bounds it
    assert got["adr/created.md"] == ("2025-09-12T00:00:00Z", "created_line")
    assert got["late.md"] == ("2026-06-01T08:00:00Z", "git_added")  # the earlier of the commit and the line
    assert got["plain.md"] == ("2026-06-01T08:00:00Z", "git_added")
    assert "bulk_commit" in importers.APPROXIMATE_BASES
    assert report.as_dict()["approximate_dates"] == n - 1 + 2


def test_fewer_files_in_one_second_is_an_ordinary_commit(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    for i in range(markdown.BULK_COMMIT_FILES - 1):
        (repo / f"{i}.md").write_text(f"# {i}\n")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "import", env=BULK)
    report = ImportReport(importer="markdown")
    bases = {d.source["origin"]["date_basis"] for d in markdown.drafts(report=report, path=repo)}
    assert bases == {"git_added"}
