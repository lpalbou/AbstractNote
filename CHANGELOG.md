# Changelog

All notable user-visible changes to AbstractNote are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/).

## [0.1.1] - 2026-10-19

Install with `pip install "abstractnote[gateway]"` (AbstractGateway 0.15.11 or later carries the
AbstractNote integration); the web app is `@abstractframework/note` on npm.

### Changed

- **Capture**: reading a notebook never changes it. Capture, quick filing, your own actions and
  archiving first finish any change a stopped process left half applied; `abstractnote recover`
  does it on request.
- **Understanding**: every model step has a fixed thinking setting and output budget (quick filing
  without thinking; extraction and linking with it), whatever the model or the run asks. Long
  notes are read in 6000-character sections; a section with text whose extraction finds nothing is
  read once more, and a second empty answer is marked on the note. The prompts start with the same
  instructions for every note, and the linking step sees a short list of candidates per name, so
  prompts are several times smaller and a note is understood faster.
- **Understanding**: a name becomes an entity only when it occurs in the note and is something you
  would look up again (ids, values, versions and generic words stay text); an existing entity is
  reused only for the very same thing (a part, version or feature of it is a new one); one to three
  topics per note; a statement the note replaces is closed rather than kept beside the new one;
  a missing entity summary is a warning, never a failed note.
- **Understanding**: contradiction candidates are only statements the linking marks as disputing
  each other, so several people or parts of one thing are no longer offered as contradictions.
- **Recall**: quick search keeps an entity or statement only when it matches the question well and a
  returned note carries it, groups hits under their notes with first and latest dates, and lists
  notes oldest first with `--order earliest` (`order=earliest` on `GET /note/search`).
- **Recall**: the standard answer reads a packet of bounded size (24,000 characters by default):
  the entities you asked about, the notes with excerpts around your question, and the history of
  their statements, instead of everything the graph reaches.
- **Recall**: open loops say how many days they have been open and are marked stale after 60 days
  (`GET /note/tasks`, `note_open_loops`).
- **Web app**: sign-in uses the framework's own dialog (gateway address, account, token, keep me
  signed in), remembers the last address and account, and the top-right corner has appearance,
  About, Settings and Connect/Disconnect. A gateway without AbstractNote shows one sentence,
  "AbstractNote is not installed on this gateway.", in every view.
- **Web app**: the Explore picture zooms (wheel, trackpad, pinch), pans, lets you drag a node while
  the layout settles around it, and fits to view on load or double-click; an empty notebook invites
  the first note or an import; organized notes lead with their title and linked entities.
- **Apple Watch app**: Settings show the server and account and sign out; tap to talk transcribes,
  or sends the recording as it is; errors are one specific sentence.
- **Importers**: a prompt note's author is the person who wrote the prompt, with the assistant's
  answer recorded as such; `import agora` selects by `--kinds`, `--include-channels` and
  `--exclude-channels` and can keep retracted entries with `--include-retracted`; every imported
  note records how its date was found, and import reports count the approximate dates.
- **Install**: the documentation describes the published packages; installing from source checkouts
  is the contributor path.

### Fixed

- **Processing after an upgrade**: the background processing and the weekly reflection kept the
  version of AbstractNote they were set up with, so after an upgrade notes stayed queued. Opening
  the web app now repairs them (or press Repair in Settings, Queue; `POST /note/processor/ensure`
  through the API), keeping your schedules and a paused state; `GET /note/status` and
  `GET /note/queue` say whether each one can run.

## [0.1.0] - 2026-10-19

The first version: the `abstractnote` package on PyPI and the `@abstractframework/note` web app on npm;
it needs AbstractMemory 0.3.1 and, for the gateway seam, AbstractGateway 0.15.11. The App Store
publication of the phone app is pending.

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
- **Web app** `@abstractframework/note`: Capture, Remember and Explore, with a Settings panel.
- **Apple Watch app**: Note, Ask, Today and Settings; watch-only, watchOS 26 or later.
- **Command line** `abstractnote`: capture, search, entity, day, status, changes, rebuild,
  process, bundle and import, on a notebook directory.
- **Importers**: Claude Code sessions, an agora hub database (read-only), Markdown trees and
  files, with original dates, provenance and `--dry-run` counts.

### Requirements

- AbstractMemory 0.3.1 or later; AbstractGateway 0.15.11 or later for the gateway path.
