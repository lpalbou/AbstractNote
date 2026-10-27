# Changelog

All notable user-visible changes to AbstractNote are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **Every statement says where it came from** (backlog 0037): what the notebook learns from a note now
  names the steps of AbstractRuntime's ledger that captured the note and that understood it (the run and
  the ledger row), and the note's text and attachments as stored files of the capture run. When a later note
  supersedes or retracts a statement, the closing carries the same. Corrections a person makes on the web page
  (retract, redate, undo a merge, confirm a match, close a task) do not run as steps of the ledger yet: they
  name the revision and its account, but no ledger source. A run id typed by a caller or a model is kept as a
  label, never as a source. The shape is AbstractMemory's (its ADR 0003), so the notebook's knowledge can
  move into the plane's shared memory with its sources. Inside the gateway a captured note's text is also
  stored as an artifact of the capture run; captures from the command line and importers are unchanged.
  Nothing is filled in for notes written before this version.

### Changed
- **Requires AbstractRuntime 0.14.0** (its step context and `LedgerStore.get_record`, ADR 0009): a note
  tool reads the run and ledger row of the step it runs in from the runtime; neither a flow nor a model passes
  it. The floor is `abstractruntime>=0.14.0`. This release waits until AbstractRuntime 0.14.0 and
  AbstractMemory 0.4.0 are on PyPI (root ADR-0034: never released above an unpublished floor).
- **Notebooks open as before**: the new fields are added to an existing notebook when it opens, empty
  for what it already holds; the notebook's format version does not change.
- **Requires AbstractMemory 0.4.0**: a statement placed between two earlier beliefs now moves the
  older belief's end date through AbstractMemory's public `narrow_assertion`; AbstractNote no longer
  writes AbstractMemory's private table (backlog 0034). The floor is `abstractmemory>=0.4.0`, the
  release that carries it.
- **What a thing is**: the description of a person, project or tool now says what it is (its kind,
  its role or purpose, and what it belongs to or works with), instead of the latest thing
  that happened to it. It is rewritten only when a note changes
  what the thing is, and a note dated before what the notebook learned later describes the thing
  as it stood then.
- **Statements**: understanding a note now names how two things relate with a specific verb (runs
  on, depends on, fixed in, status, version) read from the first to the second, and keeps the same
  wording for the same kind of fact, so a new status or version replaces the old one.
- **Agents and their packages**: an agent that writes messages (such as a seat named `gateway`) is
  kept apart from the software it works on, even when both carry the same name.
- **One spelling per topic**: `abstractnote normalize-topics` joins only topics that differ by case,
  hyphens, underscores or spaces; a plural (`bug_fixes`) stays its own topic.
- **Nothing left out without a word** (ADR 0007): a description longer than 600 characters or a
  theme name longer than 4 words is kept whole with a warning instead of being cut or dropped;
  topics a note could not keep are named in what processing did to it; lists chosen under a bound
  (the graph, recall packets, search, stats, reflection candidates) say how much they left out, and
  a request for more than a read allows is answered and logged.

### Fixed
- **Consolidation no longer stops silently**: when the model gives an empty or partial answer, the
  consolidation asks once more, sets aside what is still unanswered for the next pass and goes on,
  instead of ending the summaries stage and calling the pass complete with hundreds of summaries
  still waiting. A pass that could not refresh everything is marked as having gaps, and the
  Processing page says so in one quiet line ("12 summaries could not be refreshed this time; they
  will be tried again next time.").

## [0.1.1] - 2026-10-19

Install with `pip install "abstractnote[gateway]"` (AbstractGateway 0.15.11 or later carries the
AbstractNote integration); the web app is `@abstractframework/note` on npm.

### Added
- **Watch**: a redesigned app on the wrist: one large field with a round mic and a Send capsule,
  the state line Sending → Received → Organizing → Organized with a haptic on Received, a result card
  with the kind, the title and tappable linked entities; Ask answers instantly by default (Quick) with
  Standard one tap away and sources as rows; Today shows what changed since you last looked, one cue
  at a time, the largest themes and open loops; entity and theme pages with "How it evolved".

- **Themes**: the notebook finds the groups of things your notes keep bringing together (a project
  and its people, a set of tools, a recurring subject) without a model, and the weekly reflection
  names each one in a few words with one sentence on what holds it together. A theme keeps its name
  while it changes little and is named again when it has changed a lot. `GET /note/communities`
  lists them, `POST /note/communities/recompute` finds them again on request, and every entity
  card says which theme it belongs to. Notes that mention many things count for less, so one long
  note rarely forms a theme by itself (a theme held together by that note's own statements still can).
- **Numbers**: `GET /note/stats` counts what the notebook has gathered: notes by kind and by where
  they came from, attachments, weekly activity over its whole span, people, projects and ideas,
  statements and how many were replaced, open and closed actions, insights, themes, processing, and
  the entities that have gone quiet.
- **Web app, Overview**: Explore opens on an Overview of the notebook: five figures (notes
  understood and waiting, people projects and ideas, beliefs and how many were replaced, open
  actions, themes), notes per week over the last 16 weeks with the busy weeks marked, entries by
  kind and the most noted ones, a card per theme that opens the picture with that theme alone, what
  has gone quiet, and processing in words.
- **Web app, picture**: nodes take their theme's colour, with a legend that shows one theme alone;
  hovering a node or a link fades the rest, and a card beside it (also on keyboard focus) gives its
  name, kind, one sentence, how many notes, first and latest, its theme and its three strongest
  links in words, drawn on top and never over the node or its neighbours. Link words appear when
  you zoom in.
- **Web app, how an entry evolved**: an entity's history opens with a line of what was said about
  it, week by week (month by month past half a year), with a marker and a date on each period where
  a belief was replaced.
- **Apple Watch app**: sign in on the watch with your user name or email, then a token or the
  8-digit code sent to you, on a screen that also sets the server; a server that cannot transcribe
  voice says so in one sentence and keeps the recording, so it can be sent as it is.

### Changed

- **Entities**: every entity has one canonical name, shown everywhere: lower case, with spaces and
  punctuation as one underscore (`Laurent-Philippe Albou` is `laurent_philippe_albou`), so `Memory`
  and `memory` are one entity, never two; the first way a note wrote it stays available as
  `display_name`. A new mention with a name the notebook already has is filed under that entity,
  whatever the model decided. An older notebook joins its same-name entities when it next processes a
  note, or on request with `abstractnote normalize` or `POST /note/entities/normalize` (`--dry-run`
  lists them first); each join can be undone. Different names for one thing (`Gateway` and
  `abstractgateway`) stay a matter for the model and your review.
- **Capture**: reading a notebook never changes it. Capture, quick filing, your own actions and
  archiving first finish any change a stopped process left half applied; `abstractnote recover`
  does it on request.
- **Understanding**: every model step has a fixed output budget and a known thinking setting
  (quick filing and naming themes without thinking); thinking for extraction and linking is chosen
  per run: on when a note is processed on its own, off by default for the background processing of
  a queue or an import (`thinking` on note-process and note-drain). Long
  notes are read in 6000-character sections; a section with text whose extraction finds nothing is
  read once more, and a second empty answer is marked on the note. The prompts start with the same
  instructions for every note, and the linking step sees a short list of candidates per name, so
  prompts are several times smaller and a note is understood faster. The prompts show an entity's
  kind after a dash (`memory — software`) and a name that still arrives with its kind in parentheses
  (`memory (software)`) is filed under the name alone; `abstractnote normalize` joins the entries an
  earlier version created that way into the entity they name.
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
  `GET /note/queue` say whether each one can run. The same repair applies when a new version adds or
  removes a tool the processing uses ("The tool list changed."), and the Apple Watch app runs it at
  launch and after each sign-in.
- **Themes after a library upgrade**: the themes depended on the installed `networkx` release, so an
  upgrade could rename or drop some of them. AbstractNote now accepts only the tested releases
  (`networkx>=3.2,<3.7`), records the release each computation used, and after a change keeps every
  name that still fits, hands the others to the theme they overlap most to be named again, and
  lists what it did in the recompute answer.

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
