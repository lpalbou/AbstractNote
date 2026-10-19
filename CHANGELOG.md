# Changelog

All notable user-visible changes to AbstractNote are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/).

## [0.1.0] - Unreleased

The first version. It is installed from source; PyPI, npm and App Store publication are pending.

### Added

- **Notebook**: notes with history time, place and provenance; attachments stored once per
  content; idempotent capture (a retried send answers `duplicate`); notes that repeat or resemble
  earlier ones are linked to them as revisits.
- **Understanding**: quick filing (title, kind, topics, urgency, language), then extraction,
  candidate lookup and model-decided linking of entities, claims and tasks, applied as revisions
  to a note index and an AbstractMemory triple store. Facts are superseded, never overwritten;
  the graph and the beliefs at any past moment can be read back.
- **Queue**: priorities (urgent, high, normal, bulk), leases, retries after one and ten minutes,
  failed notes visible with their error and a retry action.
- **Flows**: `note-capture`, `note-process`, `note-drain`, `note-recall`, `note-mine` and
  `note-reflect` in the `abstractnote` bundle, editable in the Flow editor and runnable
  headlessly; `note-recall` also answers the `abstractcode.agent.v1` chat contract.
- **Recall**: quick (full-text plus vector, no model), standard (graph expansion and one cited
  synthesis), deep (a stored dossier); since-you-last-looked changes, cue cards, entity cards as
  of a moment, then-versus-now, relation timelines, evidence chains, day and timeline views.
- **Reflection**: open loops, contradictions, resurfacing and anniversaries turned into insights
  on a schedule; the first one runs a full interval after the notebook is set up (opening an
  empty notebook never starts a model), or at once with "Reflect now".
- **User actions**: close a task, retract a claim, undo a merge, acknowledge an insight, archive
  a note; each one is a revision with the account as its source. A claim you retract was never
  true; "since you last looked" lists it as withdrawn, in plain words.
- **Gateway integration** (with the AbstractGateway integration): per-user note tools,
  auto-approved; the note read API under `/api/gateway/note`; the processor and reflect
  automations through `POST /note/processor/ensure`; the Note app in the gateway's app catalog.
- **Web app** `@abstractframework/note`: Capture, Graph, Remember and Settings.
- **Apple Watch app**: Note, Ask, Today and Settings; watch-only, watchOS 26 or later.
- **Command line** `abstractnote`: capture, search, entity, day, status, changes, rebuild,
  process, bundle and import, on a notebook directory.
- **Importers**: Claude Code sessions, an agora hub database (read-only), Markdown trees and
  files, with original dates, provenance and `--dry-run` counts.

### Requirements

- An AbstractMemory release with `close_assertion`, and an AbstractGateway release with the
  AbstractNote integration, for the gateway path.
