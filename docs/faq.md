# FAQ

Short answers to recurring questions. When the answer is a fix, it links to
[troubleshooting.md](troubleshooting.md); when it is a design question, it links to the page that
owns the topic.

## Using AbstractNote

### Do I need a gateway?

No, for a notebook on one machine: the `abstractnote` command line works on a notebook directory
with `--notebook DIR` and runs the flows locally with `process --local`
([getting-started.md](getting-started.md#2-a-headless-notebook-in-five-minutes)). Yes, for the web
app, the watch app, several users, and background processing on a schedule: those come from
AbstractGateway with the AbstractNote integration ([architecture.md](architecture.md#gateway)).

### Can the command line talk to my gateway?

Not in this version. `--gateway URL --token T` is reserved and answers "Gateway client mode is
not available in this version; run the command on the machine that holds the notebook with
--notebook DIR." (exit code 3). A gateway keeps a user's notebook in
`<data dir>/users/<tenant>/<runtime>/abstractnote` (or `<data dir>/abstractnote` without user
accounts); on that machine you can point `--notebook` at it. `abstractnote processor ensure` is
reserved the same way: use `POST /api/gateway/note/processor/ensure` or the web app.

### Why do notes I capture from the command line have no title or kind?

`abstractnote capture` stores the note directly, without a model. The quick filing step (title,
kind, topics, urgency, language) is part of the `note-capture` flow that gateway clients start.
Processing (`process --local`, or the gateway's processor) adds the summary, topics, entities,
claims and tasks. From Python, `NoteRunner.local(...).run_capture(...)` runs the whole capture
flow headlessly ([api.md](api.md#python-api)).

### Which models should I use?

A small fast model for filing and processing; the recommended one on Apple silicon is
`mlx-community/Qwen3.6-35B-A3B-4bit` with `--provider mlx` (install `abstractcore[apple]`). Any
provider AbstractCore supports works. Inside a gateway the web app's Settings choose a
**filing model** (the processor) and an **answering model** (standard and deep recall, and the
reflect automation); "Default" uses the gateway's default text model.

### I changed the model in Settings. Why does processing still use the old one?

Settings are stored in the notebook and used when the processor and reflect automations are
created. Automations that already exist keep the models they were created with.

### What is the embedding pin, and can I change models?

Vectors from different embedding models cannot be compared, so a notebook records the model that
created it and refuses another one. Change models with `abstractnote rebuild --reembed` and the
new `--embed-base-url` / `--embed-model`: it replays notes and revisions into fresh stores with the
new vectors. See [troubleshooting.md](troubleshooting.md#this-notebook-is-pinned-to-the-embedding-model).

### Is anything ever deleted or truncated?

No. Notes are archived, never deleted (`POST /note/notes/{id}/archive`). Long notes are split
into sections for the model and merged before linking; the stored text and the full-text index
always keep the whole note. Superseded facts keep their history with a closed validity window.

### Does AbstractNote describe images and transcribe recordings?

It stores every attachment and reads text, Markdown and PDF files. Image descriptions and
transcripts need an image model and a speech model passed to the note tools
(`abstractnote.tools.bind(..., extractors={"vision": ..., "stt": ...})`); the gateway integration
does not configure them yet. Without them the note keeps the file and its details, and the capture
result carries a warning such as "Kept photo.jpg; no image model is configured to describe it."

### How large can an attachment be?

Uploads through a gateway follow the gateway's attachment limit (25 MB per file by default). Add
larger files on the machine that holds them with `abstractnote import files PATH`.

### How fast is processing?

Expect about two model calls per note. The gateway's processor handles up to 20 notes every two
minutes, one at a time per user, so a large import is understood over hours while live notes go
first (imports use the lowest priority). "Process now" in the web app starts a pass immediately.

### Can agents use my notebook?

Yes. Inside the gateway the note tools are bound to the caller's own notebook and auto-approved,
and `note-recall` also answers the `abstractcode.agent.v1` chat contract (`prompt` in,
`output.response` out). Agents should start a session with `note_changes` and cite the note ids
they rely on; see [remembering.md](remembering.md#for-agents-and-entities).

## Design questions

### Why not regular expressions for names and duplicates?

Identity and change are judged by a model from candidates; deterministic code only produces exact
hashes and candidate lists. See [ADR 0004](adr/0004-models-decide-identity-no-text-heuristics.md).

### Why two stores?

The revision log is the truth; the note index serves fast interactive reads and the AbstractMemory
store holds every claim as a temporal triple that framework tools can read. Both are projections
you can rebuild. See [data-model.md](data-model.md) and
[ADR 0003](adr/0003-revision-log-is-truth-index-and-abstractmemory-projection.md).

### Why is understanding queued instead of immediate?

Capture must never wait for a model. The queue gives priority order, retries and a visible state;
"Instant linking" in the web app processes a note right after capture when you want that. See
[ADR 0005](adr/0005-eventual-consistency-queue-and-processor-automation.md).

## Limits of this version

- Not published yet: no PyPI package, no npm package for the web app, no TestFlight or App Store
  build of the watch app. Install from source ([getting-started.md](getting-started.md)).
- The gateway integration and AbstractMemory's `close_assertion` are on branches of those
  packages until their next releases; AbstractMemory's must ship first.
- How the Apple apps store the sign-in (a protected file in the app container) is proposed and
  awaits a ruling ([ADR 0006](adr/0006-apple-client-credential-storage.md)).
- The command line works on local notebooks only (no gateway client mode).
- The iPhone app is planned, not built ([clients.md](clients.md#iphone-app-phoneiphone)).
