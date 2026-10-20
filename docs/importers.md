# Importers

Importers turn existing corpora into notes with faithful history time and provenance. They write
notes directly into a notebook at bulk priority (`0`) and let the processor understand them in
the background, so a large import never delays live notes. Every imported note keeps a `source`
object that points back to the exact origin, and re-running an import is idempotent because note
ids derive from the source key.

| Importer | Command | One note per | `occurred_at` | `source.origin` |
|---|---|---|---|---|
| Claude Code sessions | `abstractnote import claude-sessions DIR` | human-typed prompt (the operator's words; the final assistant text of that turn is its outcome); one note per session summary (title, compaction and away summaries); one note per subagent run (its brief and its last report); one note per curated memory file | the record timestamp; a memory file: see [Dates](#dates) | `{session_id, uuid, prompt_id, cwd, git_branch, model, file, line, outcome, outcome_author}` |
| Agora hub | `abstractnote import agora DB` | recorded decision (`store decision:*`), claim and work entry, resolved or answered thread (root plus replies), operator message | `created_at` (a store entry without a message: its `updated_at`) | `{message_id, channel, seq, sender, reply_to, status, hash, retracted_at?}` |
| Markdown tree | `abstractnote import markdown DIR` | file (ADRs, backlog items, notes), with front-matter and headings kept | see [Dates](#dates) | `{repo, path, blob_sha, commit, added_as?, completed?}` |
| Files | `abstractnote import files PATH...` | file (documents, images, audio, video, logs) | the file's own metadata date (EXIF, XMP, PDF `CreationDate`, Office `core.xml` created), else the file date | `{path, sha256}` |

Every `source.origin` also carries `date_basis`: how `occurred_at` was found.

## Dates

| `date_basis` | Meaning | Approximate |
|---|---|---|
| `record` | the transcript record's own timestamp (prompts, summaries) | no |
| `first_record` / `last_record` | a subagent run's first record / a session title's last record | `last_record` yes |
| `message` | the agora message's `created_at` (threads, operator messages, store entries naming a message) | no |
| `store_updated` | an agora store entry that names no message: its `updated_at` | yes |
| `origin_session` | a memory file: the first record of its origin session (front matter `originSessionId`, looked up in the same project folder, then its sibling project folders) | yes (within that session) |
| `front_matter_modified` | a memory file whose origin session is not on disk: its front matter `modified` | yes (last edit) |
| `front_matter` | a Markdown front matter `date`, `created` or `created_at` | no |
| `created_line` | a Markdown `Created:` field line anywhere in the file (`- **Created:** 2026-09-12`) | no |
| `date_line` | a Markdown `Date:` field line in the first 40 lines (later ones date completion reports) | no |
| `git_added` | the commit that first added the file, following moves (`git log --follow --diff-filter=A`); also when that commit is earlier than the file's `Created:` line | yes |
| `bulk_commit` | the file was first added by a bulk import commit (10 or more files first added in the same second, `markdown.BULK_COMMIT_FILES`) and has no field line: the earlier of its modification time and that commit | yes |
| `exif` | a photo's EXIF `DateTimeOriginal` (else `DateTime`; Pillow, when installed) | no |
| `xmp` | an XMP creation date in an image or a PDF (`xmp:CreateDate`, `photoshop:DateCreated`, `exif:DateTimeOriginal`) | no |
| `pdf_created` | a PDF's Info `/CreationDate` | no |
| `office_created` | an Office Open XML document's `docProps/core.xml` `dcterms:created` (docx, xlsx, pptx) | no |
| `file_mtime` | the file's modification time (a live capture: the `last_modified` the client sent with the file, the web's `File.lastModified`) | yes |
| `upload`, `pasted_text`, `capture_time` | a live capture: the moment of an upload, of a paste, or of a capture that sent no date (data-model.md "Document dates") | yes |
| `client` | a live note typed now: the client's time | no |
| `content` / `stated_month`, `stated_year` | written after import: the date the text states itself (`note.redate` at processing) / only its month or year | no / yes |
| `user` | written after import: a person set the date | no |

Files and captures share one detector, `abstractnote.ingest.dates.detect_file_date(path or bytes, mime)`: images
`exif` then `xmp`, PDFs `pdf_created` then `xmp`, Office documents `office_created`, then (for a path) `file_mtime`.
An import that gives no `occurred_at` takes the first attachment's metadata date and records its basis; a live
capture is dated by `ingest.dates.capture_date` (data-model.md "Document dates": typed now `client`; an upload or a
paste is a stand-in; without a client time the file's metadata date, then the `last_modified` the client sent,
then the moment of the capture). Bases written after import: `content` (or `stated_month` / `stated_year`) when
processing found the date the text states itself on a note whose date was approximate (`note.redate`), and `user`
when a person set the date; an import from before date bases were recorded reads as `unrecorded` (approximate).

The order for a memory file is `origin_session`, `front_matter_modified`, `git_added`,
`file_mtime`; for a Markdown file `front_matter`, `created_line` (or `git_added` when the first commit is
earlier than that line), `date_line`, `git_added`, `file_mtime`. A commit that first added 10 or more files in
the same second is an import of an existing tree, not the files' creation: its files without a field line get
`bulk_commit` instead of `git_added`. An approximate date never makes anything old, quiet or stale in reflect
(cognition.md "note-reflect"). A field line is a labelled metadata line (an optional list marker and bold around
the label) whose value starts with an ISO date. A Markdown `Completed:` field line is kept as
written in `source.origin.completed` (`2026-09-27`), ready to be stated as a fact; `N/A` is not
kept. `added_as` is the path the file was first added under, when it has moved since.

## Memory folders

A Claude Code curated memory folder (`<project>/memory/*.md` of a `~/.claude/projects` folder: the project
folder holds session transcripts or sits in `~/.claude/projects`) belongs to the `claude-sessions` importer.
`abstractnote import markdown` on such a folder hands each file over (`claude_sessions.memory_draft`), so both
importers give the same importer and source key (`memory:<project>:<file>:<content hash>`) and the second
import of a file is a duplicate; `MEMORY.md`, the index, is never a note. Pairs written before that rule (a
`markdown` note and a `claude-sessions` memory note with the same file name and content hash) are archived
by `abstractnote dedupe-sources --notebook DIR [--dry-run] [--account NAME]`: the later received copy of
each pair, in ONE revision of `note.archive` ops naming the copy kept (`duplicate_of`); a waiting copy leaves
the queue. A second run finds nothing.

## Prompt notes

A prompt note is the operator's prompt, with `source.author` the operator. The assistant's final
text for that turn is `source.origin.outcome`, by `source.origin.outcome_author` (the model id).
It is also appended to the text after a separator that names the model (`---` then
`## Assistant answer (<model id>)`), so a search finds it without it being the operator's words.

## Agora selection

`--kinds decision,claim,work,thread,operator` picks what to write (default: all). Notes are cut
the same way whatever the selection (an operator reply inside an answered thread stays part of
that thread), so imports of separate kinds add up to one full import. `--include-channels a,b`
(also `--channels`) keeps only those channels; `--exclude-channels a,b` leaves those out.
Retracted messages are left out unless `--include-retracted` is given; a kept one carries
`retracted_at`. Left-out records are counted in `skipped` by reason (`kind_filtered_<kind>`,
`channel_filtered`, `channel_excluded`, `retracted`).

Rules shared by all importers:

- Skip machine noise: tool results, hook output, system reminders, presence and silence alerts,
  file-change notices. Keep what a person or an agent wrote on purpose.
- Keep whole texts. Large texts are sectioned at processing time, never truncated.
- Attach images by hash and type; do not inline bytes into notes.
- Mark the author: `source.author` is the account name, the agent seat, or the model name.
- Private content stays in the notebook; importers never post or copy anything outside it.
- Report counts (notes created, duplicates skipped, attachments stored, notes by kind, records
  skipped by reason), the time range covered, and the `date_basis` histogram with
  `approximate_dates` (how many dates are a session start, a last edit, a commit or a file date), in
  a dry run too, so a user sees how many dates are approximate before writing anything.

## Re-imports after the 2026-10-19 date and outcome fixes

Note ids derive from the source key, and none of the source keys changed: prompts
(`<session>:<uuid>`), memory files (`memory:<project>:<file>:<content hash>`), Markdown files
(`<repo>:<path>:<content hash>`) and agora entries keep their ids. A re-import into a notebook that
already holds them therefore reports duplicates and keeps the notes as they were (old dates, the
answer under `## Outcome` in the text, no `date_basis`); the corrected dates, outcome fields and
date bases reach a notebook only through a fresh import into a new notebook.

Every importer takes `--notebook DIR`, `--since`, `--until` and `--dry-run` (count only, write
nothing) and prints a counts report as JSON; the full flag list is in [api.md](api.md#cli). A
notebook pinned to an embedding server is opened with the same `--embed-base-url` and
`--embed-model` that `capture` and `process` take
([troubleshooting.md](troubleshooting.md#this-notebooks-vectors-were-made-with-the-embedding-model-)).

Processing an import runs through the same `note-process` flow as live notes. Expect roughly two
model calls per note; with a small local model this is a few seconds per note, so a corpus of a
few thousand notes takes hours. Run `abstractnote process --local` on the machine that holds the
model, or let the gateway's processor automation drain the queue over time.
