"""``abstractnote import <source> ...`` (docs/api.md CLI). Also runnable as ``python -m abstractnote.importers``.

    abstractnote import claude-sessions DIR [--notebook DIR] [--since ISO] [--until ISO] [--exclude-session ID ...]
    abstractnote import agora DB_PATH [--channels a,b] [--include-system] [--operators a,b] [--notebook DIR]
    abstractnote import markdown DIR [--glob '**/*.md'] [--notebook DIR]
    abstractnote import files PATH ... [--notebook DIR]

Every command takes ``--dry-run`` (count what would be created; nothing is written and a missing
notebook is not created) and ``--embed-base-url`` / ``--embed-model`` (the notebook's embedding model,
as for ``capture`` and ``process``), and prints the counts report as JSON.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import ImportReport, run


def _csv(value: Optional[str]) -> Optional[List[str]]:
    if not value:
        return None
    return [v.strip() for v in value.split(",") if v.strip()]


def build_parser(prog: str = "abstractnote import") -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=prog, description="Import existing notes into a notebook (bulk priority).")
    sub = ap.add_subparsers(dest="source", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--notebook", type=Path, required=True, help="the notebook directory")
        p.add_argument("--since", help="only records at or after this ISO time")
        p.add_argument("--until", help="only records before this ISO time")
        p.add_argument("--dry-run", action="store_true", help="count only; write nothing")
        p.add_argument("--embed-base-url", help="OpenAI-compatible embeddings URL of the notebook's embedding model "
                       "(a notebook is pinned to its model at creation; without these flags: the built-in embedder)")
        p.add_argument("--embed-model", help="embedding model id at that URL")

    p = sub.add_parser("claude-sessions", help="Claude Code transcripts (a project folder or a folder of them)")
    p.add_argument("dir", type=Path)
    p.add_argument("--exclude-session", action="append", default=[], help="a session id to leave out (repeatable)")
    p.add_argument("--author", help="the account name recorded on typed prompts (default: this computer's user)")
    common(p)
    p = sub.add_parser("agora", help="an agora hub database (opened read-only)")
    p.add_argument("db", type=Path)
    p.add_argument("--channels", help="comma-separated channels to keep (default: all)")
    p.add_argument("--include-system", action="store_true", help="keep system and file messages and hub alerts")
    p.add_argument("--operators", help="comma-separated senders whose messages are notes on their own")
    common(p)
    p = sub.add_parser("markdown", help="a folder of Markdown files (one note per file)")
    p.add_argument("dir", type=Path)
    p.add_argument("--glob", default="**/*.md")
    common(p)
    p = sub.add_parser("files", help="files or folders (one note per file, bytes stored in the notebook)")
    p.add_argument("paths", nargs="+", type=Path)
    common(p)
    return ap


def _embedder(args: argparse.Namespace) -> Any:
    if args.embed_base_url or args.embed_model:
        if not (args.embed_base_url and args.embed_model):
            raise ValueError("Give both --embed-base-url and --embed-model.")
        from abstractnote.store.embeddings import openai_compat_embedder  # core lane

        return openai_compat_embedder(args.embed_base_url, args.embed_model)
    return None


def _open_notebook(path: Path, dry_run: bool, embedder: Any = None) -> Any:
    from abstractnote.store.notebook import Notebook  # core lane

    if dry_run and not (path / "note.sqlite3").is_file():
        return None
    return Notebook.open(path, embedder=embedder)


def main(argv: Optional[List[str]] = None, *, prog: str = "abstractnote import") -> int:
    args = build_parser(prog).parse_args(argv)
    options: Dict[str, Any] = {"since": args.since, "until": args.until}
    if args.source == "claude-sessions":
        options.update(path=args.dir, exclude_sessions=args.exclude_session, author=args.author)
    elif args.source == "agora":
        options.update(path=args.db, channels=_csv(args.channels), skip_system=not args.include_system)
        if args.operators:
            options["operators"] = _csv(args.operators)
    elif args.source == "markdown":
        options.update(path=args.dir, glob=args.glob)
    else:
        options.update(paths=args.paths)
    try:
        nb = _open_notebook(args.notebook, args.dry_run, _embedder(args))
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    try:
        report: ImportReport = run(args.source, nb, dry_run=args.dry_run, **options)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    finally:
        if nb is not None:
            nb.close()
    print(json.dumps(report.as_dict(), indent=2, ensure_ascii=False))
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main(prog="python -m abstractnote.importers"))
