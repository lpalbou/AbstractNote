# Importers

Importers turn existing corpora into notes with faithful history time and provenance. They write
notes directly into a notebook at bulk priority (`0`) and let the processor understand them in
the background, so a large import never delays live notes. Every imported note keeps a `source`
object that points back to the exact origin, and re-running an import is idempotent because note
ids derive from the source key.

| Importer | Command | One note per | `occurred_at` | `source.origin` |
|---|---|---|---|---|
| Claude Code sessions | `abstractnote import claude-sessions DIR` | human-typed prompt, with the final assistant text of that turn as the outcome; one note per session summary (title, compaction and away summaries); one note per subagent run (its brief and its last report); one note per curated memory file | the record timestamp | `{session_id, uuid, prompt_id, cwd, git_branch, model, file, line}` |
| Agora hub | `abstractnote import agora DB` | recorded decision (`store decision:*`), claim and work entry, resolved or answered thread (root plus replies), operator message | `created_at` | `{message_id, channel, seq, sender, reply_to, status, hash}` |
| Markdown tree | `abstractnote import markdown DIR` | file (ADRs, backlog items, notes), with front-matter and headings kept | front-matter dates, else the first git commit date, else the file date | `{repo, path, blob_sha, commit}` |
| Files | `abstractnote import files PATH...` | file (documents, images, audio, video, logs) | file date or EXIF | `{path, sha256}` |

Rules shared by all importers:

- Skip machine noise: tool results, hook output, system reminders, presence and silence alerts,
  file-change notices. Keep what a person or an agent wrote on purpose.
- Keep whole texts. Large texts are sectioned at processing time, never truncated.
- Attach images by hash and type; do not inline bytes into notes.
- Mark the author: `source.author` is the account name, the agent seat, or the model name.
- Private content stays in the notebook; importers never post or copy anything outside it.
- Report counts (notes created, duplicates skipped, attachments stored) and the time range covered.

Every importer takes `--notebook DIR`, `--since`, `--until` and `--dry-run` (count only, write
nothing) and prints a counts report as JSON; the full flag list is in [api.md](api.md#cli). A
notebook pinned to an embedding server is opened with the same `--embed-base-url` and
`--embed-model` that `capture` and `process` take
([troubleshooting.md](troubleshooting.md#this-notebooks-vectors-were-made-with-the-embedding-model-)).

Processing an import runs through the same `note-process` flow as live notes. Expect roughly two
model calls per note; with a small local model this is a few seconds per note, so a corpus of a
few thousand notes takes hours. Run `abstractnote process --local` on the machine that holds the
model, or let the gateway's processor automation drain the queue over time.
