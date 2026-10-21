"""The ``abstractnote`` command line (docs/api.md, "CLI").

Every command works on a notebook directory with ``--notebook DIR`` (inside a
gateway the notebook is ``<gateway data dir>/abstractnote``, or
``<data dir>/users/<tenant>/<runtime>/abstractnote`` for a user plane).
``--gateway URL --token T`` client mode is not available yet and says so.
Output is JSON on stdout. Exit codes: 0 done, 2 bad input, 3 a part of
AbstractNote that is not installed or not available yet.

Read commands (search, entity, day, status, changes) never finish a half-applied revision:
another process may still be applying it. ``capture`` and ``process`` recover first, and
``abstractnote recover`` does it on request.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

EXIT_OK, EXIT_INPUT, EXIT_GAP = 0, 2, 3


class Gap(RuntimeError):
    """A piece the command needs is not available (another package part, or client mode)."""


def _emit(data: Any) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--notebook", type=Path, help="notebook directory (created when missing)")
    p.add_argument("--gateway", help="gateway URL (client mode)")
    p.add_argument("--token", help="gateway token, passed directly")
    p.add_argument("--embed-base-url", help="OpenAI-compatible embeddings URL (for example http://127.0.0.1:1234/v1). "
                   "A notebook is pinned to its embedding model when it is created (by the first command that "
                   "opens it, capture included; without these flags: the built-in hashing embedder); every later "
                   "command must give the same model, or run rebuild --reembed to change it")
    p.add_argument("--embed-model", help="embedding model id at that URL (pinned at notebook creation, see --embed-base-url)")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="abstractnote", description="AbstractNote: one endpoint for your notes.")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("capture", help="save a note now")
    _common(p)
    p.add_argument("text", nargs="?", default="")
    p.add_argument("--file", action="append", default=[], type=Path, help="attach a file (repeatable)")
    p.add_argument("--at", help="when it happened (ISO 8601; default now)")
    p.add_argument("--title")
    p.add_argument("--priority", type=int, choices=[0, 1, 2, 3])
    p.add_argument("--key", help="idempotency key (a retry with the same key saves nothing new)")

    p = sub.add_parser("search", help="search the notes")
    _common(p)
    p.add_argument("query")
    p.add_argument("--effort", choices=["quick", "standard", "deep"], default="quick")
    p.add_argument("--since")
    p.add_argument("--until")
    p.add_argument("--as-of")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--order", choices=["relevance", "earliest"], default="relevance",
                   help="quick effort: best matches first, or the matching notes oldest first")
    p.add_argument("--provider", help="model provider for standard and deep effort")
    p.add_argument("--model", help="model for standard and deep effort")

    p = sub.add_parser("entity", help="an entity card")
    _common(p)
    p.add_argument("entity_id")
    p.add_argument("--at")

    p = sub.add_parser("day", help="what happened on a day")
    _common(p)
    p.add_argument("date", help="YYYY-MM-DD")
    p.add_argument("--tz")

    p = sub.add_parser("status", help="notebook counts, queue and versions")
    _common(p)

    p = sub.add_parser("changes", help="what changed since a cursor or a time")
    _common(p)
    p.add_argument("--since")
    p.add_argument("--limit", type=int, default=50)

    p = sub.add_parser("recover", help="finish revisions a stopped process left half applied")
    _common(p)

    p = sub.add_parser("rebuild", help="replay revisions into fresh index and triple stores")
    _common(p)
    p.add_argument("--reembed", action="store_true", help="recompute every vector with the given embedder")

    p = sub.add_parser("normalize", help="merge the entities that share a canonical name (one reversible merge each)")
    _common(p)
    p.add_argument("--dry-run", action="store_true", help="only list the groups that would be merged")
    p.add_argument("--account", help="who asked, recorded in each merge's source")

    p = sub.add_parser("normalize-topics", help="give every topic one spelling (one revision)")
    _common(p)
    p.add_argument("--dry-run", action="store_true", help="only list the groups")
    p.add_argument("--account", help="who asked, recorded in the revision's source")

    p = sub.add_parser("dedupe-sources", help="archive the second copy of memory files imported by two importers")
    _common(p)
    p.add_argument("--dry-run", action="store_true", help="only list the pairs")
    p.add_argument("--account", help="who asked, recorded in the revision's source")

    p = sub.add_parser("archive-insights", help="archive repeated insights, keeping the oldest of each (one revision)")
    _common(p)
    p.add_argument("--repeated", action="store_true", help="insights with the same kind, title and subject (required)")
    p.add_argument("--dry-run", action="store_true", help="only list the groups")
    p.add_argument("--account", help="who asked, recorded in the revision's source")

    p = sub.add_parser("unarchive", help="bring archived notes back (their reading is restored)")
    _common(p)
    p.add_argument("note_ids", nargs="*", help="the notes to bring back")
    p.add_argument("--revision", help="bring back every note this archive revision put away")
    p.add_argument("--account", help="who asked, recorded in the revision's source")

    p = sub.add_parser("process", help="process queued notes now")
    _common(p)
    p.add_argument("--local", action="store_true", help="run the flows here (headless)")
    p.add_argument("--provider")
    p.add_argument("--model")
    p.add_argument("--max-items", type=int, default=20)
    p.add_argument("--budget-s", type=float, default=90.0)

    p = sub.add_parser("refile", help="file the understood notes that have no kind (imports skipped the filing)")
    _common(p)
    p.add_argument("--missing", action="store_true", help="the understood notes without a kind (required)")
    p.add_argument("--local", action="store_true", help="run the filing flow here (headless)")
    p.add_argument("--provider")
    p.add_argument("--model")
    p.add_argument("--max-items", type=int, default=50)
    p.add_argument("--budget-s", type=float, default=600.0)

    p = sub.add_parser("bundle", help="pack the abstractnote .flow bundle")
    p.add_argument("--out", type=Path, default=Path("."))

    p = sub.add_parser("processor", help="processor automation")
    _common(p)
    p.add_argument("action", choices=["ensure"])

    sub.add_parser("import", help="import a corpus (claude-sessions, agora, markdown, files)", add_help=False)
    return ap


def _embedder(args: argparse.Namespace) -> Any:
    if getattr(args, "embed_base_url", None) or getattr(args, "embed_model", None):
        if not (args.embed_base_url and args.embed_model):
            raise ValueError("Give both --embed-base-url and --embed-model.")
        from .store.embeddings import openai_compat_embedder

        return openai_compat_embedder(args.embed_base_url, args.embed_model)
    return None


def _client_mode(args: argparse.Namespace) -> None:
    if getattr(args, "gateway", None):
        raise NotImplementedError(
            "Gateway client mode is not available in this version; run the command on the machine that holds the "
            "notebook with --notebook DIR.")


def _open(args: argparse.Namespace) -> Any:
    _client_mode(args)
    if not getattr(args, "notebook", None):
        raise ValueError("Say which notebook with --notebook DIR (a gateway keeps it in <data dir>/abstractnote).")
    from .store.notebook import Notebook

    return Notebook.open(args.notebook, embedder=_embedder(args))


def _cmd_capture(args: argparse.Namespace) -> Any:
    files = []
    for path in args.file:
        if not path.is_file():
            raise ValueError(f"There is no file at {path}.")
        files.append({"data": path.read_bytes(), "filename": path.name})
    with _open(args) as nb:
        return nb.capture(args.text, title=args.title, occurred_at=args.at, priority=args.priority,
                          idempotency_key=args.key, attachments=files, source={"client": "cli"}).model_dump()


def _runner(args: argparse.Namespace) -> Any:
    try:
        from .flows.runner import NoteRunner
    except ImportError as exc:
        raise Gap(f"The flows are not installed with this AbstractNote ({exc}).") from None
    if not (args.provider and args.model):
        raise ValueError("Give --provider and --model for the model that processes or answers.")
    return NoteRunner.local(args.notebook, provider=args.provider, model=args.model, embedder=_embedder(args))


def _cmd_search(args: argparse.Namespace) -> Any:
    if args.effort == "quick":
        with _open(args) as nb:
            return nb.search(args.query, since=args.since, until=args.until, as_of=args.as_of, limit=args.limit,
                             order=args.order)
    _client_mode(args)
    if not args.notebook:
        raise ValueError("Say which notebook with --notebook DIR.")
    return _runner(args).run_recall(args.query, effort=args.effort, since=args.since, until=args.until,
                                    as_of=args.as_of)


def _cmd_process(args: argparse.Namespace) -> Any:
    _client_mode(args)
    if not args.local:
        raise NotImplementedError("Processing through a gateway is not available here; use --local with "
                                  "--provider and --model, or press Process now in the web app.")
    if not args.notebook:
        raise ValueError("Say which notebook with --notebook DIR.")
    return _runner(args).run_drain(max_items=args.max_items, budget_s=args.budget_s)


def _cmd_refile(args: argparse.Namespace) -> Any:
    _client_mode(args)
    if not args.missing:
        raise ValueError("Say which notes to file: --missing (the understood notes without a kind).")
    if not args.local:
        raise NotImplementedError("Filing through a gateway is not available here; use --local with --provider and "
                                  "--model.")
    if not args.notebook:
        raise ValueError("Say which notebook with --notebook DIR.")
    from .ingest.refile import refile_missing

    runner = _runner(args)
    with _open(args) as nb:
        nb.require_embedder()
        return refile_missing(nb, lambda note_id: runner.run("note-file", {"note_id": note_id}),
                              max_items=args.max_items, budget_s=args.budget_s,
                              model=f"{args.provider}/{args.model}")


def _cmd_normalize(args: argparse.Namespace) -> Any:
    # A dry run needs no embedder: a notebook pinned to a model that is not available here opens read-only for
    # meaning, which is enough to list the groups; the merges themselves need the notebook's model.
    with _open(args) as nb:
        return nb.normalize_entities(args.account, client="cli", dry_run=args.dry_run)


def _cmd_bundle(args: argparse.Namespace) -> Any:
    try:
        from .flows.bundle import pack
    except ImportError as exc:
        raise Gap(f"The flows are not installed with this AbstractNote ({exc}).") from None
    try:
        return {"bundle": str(pack(args.out, check_fresh=True))}
    except RuntimeError as exc:  # stale generated flows: the flows must be rebuilt first
        raise Gap(str(exc)) from None


def _cmd_import(argv: List[str]) -> int:
    try:
        from .importers.cli import main as import_main
    except ImportError as exc:
        print(f"The importers are not installed with this AbstractNote ({exc}).", file=sys.stderr)
        return EXIT_GAP
    return int(import_main(argv, prog="abstractnote import") or 0)


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["import"]:
        return _cmd_import(argv[1:])
    args = build_parser().parse_args(argv)
    try:
        if args.command == "capture":
            out = _cmd_capture(args)
        elif args.command == "search":
            out = _cmd_search(args)
        elif args.command == "entity":
            with _open(args) as nb:
                out = nb.entity(args.entity_id, at=args.at)
        elif args.command == "day":
            with _open(args) as nb:
                out = nb.day(args.date, args.tz)
        elif args.command == "status":
            with _open(args) as nb:
                out = nb.status()
        elif args.command == "changes":
            with _open(args) as nb:
                out = nb.changes(args.since, args.limit)
        elif args.command == "recover":
            with _open(args) as nb:
                nb.require_embedder()
                out = {"recovered": nb.recover()}
        elif args.command == "rebuild":
            with _open(args) as nb:
                out = nb.rebuild(reembed=args.reembed, embedder=_embedder(args))
        elif args.command == "normalize":
            out = _cmd_normalize(args)
        elif args.command == "normalize-topics":
            with _open(args) as nb:
                out = nb.normalize_topics(args.account, client="cli", dry_run=args.dry_run)
        elif args.command == "dedupe-sources":
            with _open(args) as nb:
                out = nb.dedupe_sources(args.account, client="cli", dry_run=args.dry_run)
        elif args.command == "archive-insights":
            if not args.repeated:
                raise ValueError("Say which insights to archive: --repeated (the same kind, title and subject).")
            with _open(args) as nb:
                out = nb.archive_repeated_insights(dry_run=args.dry_run, account=args.account, client="cli")
        elif args.command == "unarchive":
            if bool(args.revision) == bool(args.note_ids):
                raise ValueError("Give the notes to bring back, or --revision with the archive that put them away.")
            with _open(args) as nb:
                out = (nb.unarchive_revision(args.revision, args.account, client="cli") if args.revision else
                       nb.unarchive_notes(args.note_ids, args.account, client="cli"))
        elif args.command == "process":
            out = _cmd_process(args)
        elif args.command == "refile":
            out = _cmd_refile(args)
        elif args.command == "bundle":
            out = _cmd_bundle(args)
        elif args.command == "processor":
            _client_mode(args)
            raise NotImplementedError("Creating the processor needs a gateway: open the web app, or use "
                                      "POST /api/gateway/note/processor/ensure with your token.")
        else:  # pragma: no cover - argparse rejects unknown commands
            raise ValueError(f"Unknown command {args.command}.")
    except (NotImplementedError, Gap) as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_GAP
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_INPUT
    _emit(out)
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
