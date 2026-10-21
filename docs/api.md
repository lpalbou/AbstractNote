# API

AbstractNote has four surfaces: the gateway runs API (start flows), the note read API (served by
the gateway when `abstractnote` is installed), the note tools (callable from any flow or agent in
the user's runtime), and the Python library and CLI. All HTTP paths are under the gateway's
`/api/gateway` prefix and use the gateway's authentication; the caller's identity selects the
notebook. Clients never send user, tenant or runtime identifiers.

## Starting flows (gateway runs API)

Capture a note:

```http
POST /api/gateway/runs/start
{
  "bundle_id": "abstractnote", "flow_id": "note-capture",
  "session_id": "<client session id>",
  "input_data": {
    "text": "Met Dana about the LUT budget; she wants the Pareto by Friday.",
    "title": null,
    "occurred_at": "2026-10-19T09:12:00Z",
    "location": {"lat": 48.85, "lon": 2.35, "label": "Paris"},
    "source": {"client": "watch"},
    "idempotency_key": "<client uuid per capture>",
    "priority": null,
    "process_inline": false,
    "context": {"attachments": [{"$artifact": "<id from /attachments/upload>", "last_modified": 1541412000000}]}
  }
}
```

Response: `{"run_id": "..."}`. Read the answer from `GET /runs/{run_id}` when `status` is
`completed` (`output.response`, `output.note_id`, `output.status`), or follow
`GET /runs/{run_id}/ledger/stream` and show the `answer_user` message as soon as it appears.

Dates (ADR 0008 §7, data-model.md "Document dates", W30's precedence table there). `occurred_at` is when the
person wrote the note: send it for a note typed now (basis `client`, never moved by its text). An upload is dated by
its file first, whether `occurred_at` is sent or not (the file's metadata beats the client's time): its first
attachment's own metadata date (`exif`, `xmp`, `pdf_created`, `office_created`), else the first `last_modified`
sent with a file (the file's modification time as the client knows it: ISO 8601 or epoch milliseconds, the web's
`File.lastModified`; basis `file_mtime`; the watch sends none), else `occurred_at` or the moment of the capture
(basis `upload`; the watch's recordings, made now, keep `client`). A pasted text is the moment of the paste
(`source: {"client": "web", "origin": {"pasted": true}}`, basis `pasted_text`); a capture with nothing else the
moment of the capture (`capture_time`). A date the document states about itself beats both the file's date and
these stand-ins: the understanding dates the note by it (basis `content`, or `stated_month` / `stated_year` when
only a month or a year is stated) and the processed answer says which won and what lost ("Dated 5 March 2025 from
the document; the file itself is from 20 March 2026.", "...; it was sent on 21 October 2026."), replacing the
capture's own sentence; a date from a file is said at once ("Dated 2 July 2019 from the document.", "... from the
file's last change.", with "; it was sent on ..." when the client's time lost to it). A document that is a sequence
of separately dated entries (a diary) becomes a container and one note per entry (data-model.md "Dated parts"); the
processed answer then says "A document of 4 dated parts, from 2 April 2024 to 1 May 2024; each is read as a note of
its own."; `source.origin.no_split: true` keeps it in one note. The web app sends no `occurred_at` for a note of
files only. A note with no words, no title and no
readable text in any of its files is refused at once ("The file “x.docx” has no readable text, so the note was not
saved; add a few words about it and send it again."), nothing saved and nothing queued.

`process_inline: true` claims the note for this run's own understanding when it is stored, so no drain takes it
meanwhile (cognition.md "Claims"); when that understanding fails, the failure is logged as a failed attempt
(`GET /note/queue` errors) and the run ends with its sentence; the processor retries the note.

Attachments go through `POST /api/gateway/attachments/upload` first (25 MB per file by default,
the gateway's limit); larger files are imported with `abstractnote import files` on the machine
that holds them.

Ask a question:

```http
POST /api/gateway/runs/start
{"bundle_id": "abstractnote", "flow_id": "note-recall", "session_id": "<id>",
 "input_data": {"question": "What did I decide about the LUT budget, and did it change?",
                "effort": "standard", "as_of": null, "since": null, "until": null}}
```

Output: `{response, answer, evidence: [{note_id, title, occurred_at, excerpt}], evolution:
{earliest, latest, turning_points: [...]}, open_loops: [...], follow_ups: [...], dossier_note_id?,
hits, effort}` (`hits` is the quick search the answer started from).
`response` is the answer text, so chat clients using the `abstractcode.agent.v1` contract
(`prompt` in, `output.response` out) work unchanged: `prompt` is accepted as an alias of `question`.

Both `note-drain` and `note-process` accept a `thinking` input (`on` or `off`): a drain defaults to
`off` (the bulk setting; the processor automation sends none, so its drains run with thinking off),
inline processing at capture uses `on`; `{"input_data": {"max_items": 50, "budget_s": 3600, "thinking": "on"}}`
starts a drain with reasoning on.

A drain's output is `{response, processed, failed, errors: [{note_id, error}], requeued: [{note_id, reason}], communities}` (`requeued`: notes put back once because the notebook changed while they were read, cognition.md "note-drain and the queue"). It ends by finding
the themes again when the batch made them due (cognition.md "note-drain and the queue", ADR 0008 §5):
`communities` = `{recomputed, due, reasons: [never_computed|earlier_notes|reinterpreted|revisions],
revisions_since, retro_notes_since, reinterpreted_since, revisions_threshold, labels_waiting, label_calls}` plus
the recompute counts (`communities`, `new`, `labels_kept`, `stale_labels`, `unlabelled`, `computed_at` ...) when
it recomputed, or `{recomputed: false, error}` when finding them failed (the response says so; the notes stay
processed). `revisions_since` is what made it due; `label_calls` what naming the waiting groups will cost the
next consolidation (5 groups per call).

Run the processor now: `POST /api/gateway/automations/{processor_id}/commands
{"command_id": "<uuid>", "type": "automation.run_now"}` (the id comes from `GET /api/gateway/note/status`;
a `409 automation_busy` answer means a drain is already running). When `automations.processor.healthy` is
false, call `POST /api/gateway/note/processor/ensure` first: `run_now` on a stopped automation does nothing.

Every run start also carries `input_data.session_id` equal to the request's `session_id` (the gateway
does not forward the run's session to the flow). Run errors appear in `output` as text starting with
`Runtime error: `; clients strip that prefix before showing the sentence.

## Note read API

Served by `abstractnote.gateway.router` and mounted by the gateway. Reads are available to any
signed-in principal for their own notebook. The few write actions are bookkeeping, not knowledge.

| Method and path | Purpose | Main parameters |
|---|---|---|
| `GET /note/status` | notebook health: counts, queue state, the processor, reflect and consolidate automations (ids and health, see the shapes below), model settings, store versions (`versions: {abstractnote, schema, abstractmemory}`; `abstractmemory` is the version of the copy the server imports: its `__version__`, else the installed distribution that holds the imported file, else the `pyproject.toml` of the checkout it was imported from; null when none says), embedding pin | |
| `GET /note/changes` | since you last looked: new notes, new entities, superseded claims, closed loops, failed notes, insights since a cursor; the default screen for humans and the first call for agents | `since (cursor or ISO), limit, as_received` |
| `GET /note/overview` | the landing data: recent notes, active entities, unacknowledged insights, today's resurfacing, activity sparkline | `days` |
| `GET /note/notes` | list notes; the dated parts of a document are folded under their container (its `parts` counts them, data-model.md "Dated parts") | `q, since, until, kind, topic, entity, status, cursor, limit, part_of (one container's parts), parts (true: every note, parts included)` |
| `GET /note/notes/{note_id}` | a note with attachments, revision, claims, linked entities, revisits | |
| `GET /note/notes/{note_id}/attachments/{attachment_id}` | attachment bytes (streams the blob) | |
| `GET /note/search` | quick recall (full-text plus vector), grouped | `q, since, until, kinds, entities, topics, limit` |
| `GET /note/entities` | list or search entities (entries merged into another are left out unless `include_merged=true`; each item carries `merged_into`) | `q, kind, sort=recent|frequent|name, cursor, limit, include_merged` |
| `GET /note/entities/{entity_id}` | entity card as of a moment: name, aliases, summary history, beliefs at `at`, relations, notes, open tasks (a merged id answers with the surviving entity and `resolved_from`); with `as_merged=true`, the entry's own record instead, not followed to the survivor | `at, as_received, as_merged` |
| `GET /note/entities/{entity_id}/timeline` | claims and notes about the entity in history order, with supersessions | `since, until` |
| `GET /note/entities/{entity_id}/then-now` | what was known at the start and what is known now, and the turning points, in history order (cached; shape below) | |
| `GET /note/relations/{a}/{b}` | the relationship between two entities through time: claims in history order, supersessions, then-versus-now of the link | `since, until` |
| `GET /note/claims/{claim_id}` | why do I believe this: the claim, the claims it superseded or was superseded by, the notes and attachment excerpts behind it | |
| `POST /note/claims/{claim_id}/retract` | user action: retract a claim (a revision with the account as source) | `reason` |
| `POST /note/notes/{note_id}/redate` | user action "Set the date": when the note happened (a `note.redate` revision with the account as source, basis `user`; data-model.md "Document dates"). A note not understood yet moves with its mentions, tasks and queue place. An understood note moves, keeps its statements where its understanding placed them and is marked, with the notes that depend on it, for re-understanding at its new date (the answer carries `reinterpret`); while this notebook cannot understand notes again that re-date is refused with `409` and one sentence, nothing written. `400` for a date that cannot be read or the date it already has | `occurred_at, reason?` |
| `POST /note/tasks/{task_id}/close` | user action: close an open loop | `note?` |
| `POST /note/entities/{entity_id}/unmerge` | user action: undo a merge | `from_id, reason` |
| `POST /note/entities/normalize` | user action: merge every group of live entities that share a canonical name (or are that name with its kind echoed in parentheses) into the earliest one, one reversible `entity.merge` revision each (source `{client, account}`); `dry_run` only lists the groups | `dry_run` |
| `GET /note/review` | to review: matches the notebook proposed or made on its own and disagreements it kept, each with why, the two sides, the notes behind them and the actions you can take | `limit` |
| `POST /note/review/{review_id}/confirm` | user action: confirm a proposed match (a close match becomes a merge; a merge is kept) | |

The review list (`GET /note/review`) is built from stored records only, never from reading text. Each item has a
`kind` and the rule that put it there (`why`):

| `kind` | `why` | What it is | `actions` |
|---|---|---|---|
| `close_match` | `unsure_match` | processing could not tell whether a name is an existing entity, so it created a new entry and recorded a `skos:closeMatch` claim to the candidate (listed while that claim is active and the two are still separate) | `confirm` (merge the new entry into the candidate and supersede the close match), `keep_apart` (retract the close-match claim) |
| `merge` | `merged_by_model` | processing joined two entries on its own and the merge is still in force (merges you confirmed here are never listed) | `confirm` (keep it: the item leaves the list), `unmerge` |
| `contradiction` | `disputed_pair` | a note contradicted an earlier statement and both were kept, marked disputed | `retract` either side (the item leaves the list when one side is no longer active); `confirm` is refused with 400 |

Each action is `{action, method, path, body}` with `path` under `/api/gateway`; a `null` in `body` is for the
person to fill (the reason of a retraction). `confirm` records a user-action revision with `action = match.confirm`
and `target` = the review id, with the account from the signed-in principal; confirming an item that is not waiting
for review is `404`.

A user action's revision carries `source = {account, client}`: `account` is the signed-in
principal; `client` is `web` when the request came through the Note web app's proxy (which marks
what it relays with `X-AbstractFramework-App-Proxy: abstractnote`) and `api` otherwise. Request
bodies cannot set it (an unknown field such as `client` is refused with 422).
| `GET /note/cues` | cue cards: a few reflect-generated prompts you can answer by adding a note (a person gone quiet, a decision that aged, a task mentioned often and never closed) | `limit` |
| `GET /note/graph` | nodes and edges for the explorer; each node carries `community_id`; `omitted: {nodes, edges}` counts the nodes past `limit` (the most mentioned are kept) and the edges that touch them | `focus (entity ids), at, as_received, depth (1..3), since, until, kinds, min_count, limit` |
| `GET /note/communities` | the communities of the entity graph as last computed (groups of entities the notes keep together, each named by the model), largest first | |
| `POST /note/communities/recompute` | run community detection now (no model; the reflect flow names new and stale communities); returns the counts | `resolution` (body, optional; default 1.0, above 1 for smaller groups) |
| `GET /note/stats` | what the notebook has accumulated: totals, weekly activity over the whole span, entities, statements, tasks, insights, communities, processing, monthly growth, entities gone quiet (every number counted by the store, no model) | |
| `GET /note/timeline` | activity per bucket: notes, revisions, new entities, superseded claims | `since, until, granularity=day|week|month` |
| `GET /note/day/{YYYY-MM-DD}` | what happened on a day: notes, revisions with summaries, entities touched | `tz, as_received` |
| `GET /note/revisions` | revision feed | `since, until, entity, cursor, limit` |
| `GET /note/topics` | topics with counts and date ranges | |
| `GET /note/tasks` | open loops | `status, entity, limit` |
| `GET /note/insights` | insights | `kind, acked, limit` |
| `POST /note/insights/{id}/ack` | acknowledge an insight | |
| `POST /note/insights/archive-repeated` and `GET /note/insights/archive-repeated` | archive repeated insights: those with the same kind, title (case and spacing ignored) and subject (the same evidence notes and entities) keep their oldest unacknowledged one, the others are archived (each through the note archive below) in ONE revision of the signed-in account; acknowledged insights are left alone; `dry_run` only lists the groups. `POST` answers `{dry_run, insights, groups: [{kind, title, keep, archive: [note_id], count}], archived, kept, revision_id, cleanup, notice}`; `GET` answers `{cleanup: Cleanup \| null}`, the latest one. `Cleanup` = `{cleanup_id, kind, started_at, finished_at, status (running, completed, failed), total, done, groups, archived: [note_id], kept: [note_id], revision_id, notice, error}`. Reversible: `abstractnote unarchive --revision <revision_id>`, or `POST /note/notes/{id}/unarchive` per note | `POST`: `{dry_run?: bool}` (other fields 422) |
| `GET /note/queue` | queue summary, failed notes with errors, and the processor's throughput (`processor: {max_items, interval}`) | `limit` |
| `POST /note/queue/{note_id}/retry` | re-queue a failed note | |
| `POST /note/queue/{note_id}/priority` | move a waiting note in the line: `{"priority": 0..3}` (3 = front of the line); only a note waiting in the queue (not one being processed); records no revision | `priority` (body) |
| `GET /note/processing` | the processing page: the processor and reflect automations with their last run, the queue with an ETA, the pace over the last two hours, the runs active now with their step, the last 20 revisions, the failed notes, the model, the consolidation (every number read from the store or the plane's runs; carries an `ETag`, `If-None-Match` answers `304`) | |
| `GET /note/consolidation` | the memory consolidation (backlog 0032): its automation and schedule, the stage running now with n of m, elapsed time and ETA, the last result, what is pending (the `consolidation` object of `GET /note/processing`) | |
| `POST /note/notes/{note_id}/archive` | archive a note (never delete): it leaves the lists and search, and its reading is withdrawn with it (data-model.md "Archive"): its statements close as a retraction ("note archived"), what it had closed re-opens, the entries and tasks only it made retire, its mentions, topics and summaries leave the views, and the later notes that build on it are marked to be understood again. One user revision `note.archive` of the signed-in account. Answers `{note_id, status: "archived", archived_at, revision_id, withdrawn: {claims_retracted, claims_reopened, reinforcements_dropped, starts_restored, tasks_reopened, tasks_retired, entities_retired, mentions}, marked, ...}` (the apply result's keys too); a note archived again changes nothing (`already_archived: true`, `revision_id: null`), except one archived before its reading was withdrawn with it, which is withdrawn now | |
| `POST /note/notes/{note_id}/unarchive` | bring an archived note back: the inverse of its archive, op by op (its statements, what it had closed, its tasks, entries, mentions, topics and summaries are as they were; something another note or person changed since is left as it is, with a warning); a note that was waiting in the queue waits again; the later notes that build on it are marked again. One user revision `note.unarchive`. Answers `{note_id, status, archived_at: null, revision_id, restored: {claims_reopened, claims_closed_again, reinforcements_restored, starts_moved_back, tasks_restored, entities_restored}, warnings, marked, ...}`; `400` "That note is not archived." | |
| `GET /note/settings` and `PUT /note/settings` | per-notebook settings: filing model, answering model, reflect schedule, processor schedule (`{every, max_items}`, default `2m` and 20; `max_items` 1 to 200), consolidation schedule (`consolidate_schedule`: `{every}`, a number of hours or days such as `12h`, `1d` (default) or `7d`, or `off`), inline processing default; the automations take them when `processor/ensure` creates them; a `consolidate_schedule` change re-creates the consolidation automation at once (paused for `off`, resumed when only `off` had paused it) | |
| `POST /note/processor/ensure` | create the processor, reflect and consolidate automations if missing, repair them when unhealthy (another workflow version or tool list after an upgrade, failed, archived, gone), keep them otherwise; says what it did for each (shape below) | |
| `POST /note/refile` and `GET /note/refile` | `refile --missing` through the gateway: file the understood notes that have no kind (imports skipped the quick filing), oldest first, one `note-file` run each on your own runtime (the served workflow version, the filing model setting, the processor's tool list); `POST` answers `202` with the repair as soon as it is open and files in the background, one at a time per notebook (`409` "Notes are being filed already; wait for that to end."), `400` unless `missing` is true, `503` when the note workflows are not served; a repair left running by a stopped server is closed `interrupted` by the next `POST`; `GET` reads the newest repair | `POST`: `{missing: true, max_items? (1..500, default 50), budget_s? (default 600)}` |

Three dates (data-model.md "Time"): `at` asks what was true or known as of a moment of history (the
authored and valid dates); `as_received` (ISO) asks what the archive held on a date: only the notes received
by then and what they caused, leaving out material imported later even when it was written earlier. They
compose (`at` and `as_received` together: the graph as of T, from what the archive held on R). With
`as_received`, the graph, the entity card and the day answer carry `as_received` (normalised); `/changes` keeps
its pinned keys. An entity the archive did not hold yet is `400` ("That entity was not in your notebook yet on
that date."), as is a date that cannot be read.

Response envelopes are plain JSON objects; every list (`/notes`, `/entities`, `/revisions`,
`/tasks`, `/cues`, `/topics`, `/insights`, `/review`) returns `{items, next_cursor}`, with `next_cursor` null
when there is no further page (the last five never page; `limit` bounds them). Times are ISO
8601 UTC. Errors use the gateway's error shape, `{"detail": "<one sentence>"}`: `401` without a
valid sign-in, `404` for an id that is not in the caller's notebook, `400` for bad input, `501`
for a read the installed `abstractnote` does not provide.

Pinned response shapes (clients decode these strictly; absent lists mean empty):

| Endpoint | Shape |
|---|---|
| `GET /note/changes` | `{cursor, notes: [NoteRef + {how}], entities: [{entity_id, name, display_name, kind, first_seen}], superseded_claims: [{claim_id, kind, text, superseded_by, note_id}], closed_loops: [{task_id, text, closed_at}], failed_notes: [{note_id, title, error}], insights: [Insight]}`; `notes` lists the notes received after the cursor (`how: "captured"`) and those whose latest processing revision was recorded after it (`how: "understood"`, so an import's notes show up again as they are understood), each note once, at its latest event |
| `superseded_claims[]` | `kind` is `superseded` (a later note replaced the claim; `superseded_by` is the new claim's id) or `retracted` (withdrawn by a note or by you; `superseded_by` is null); `text` is the claim in plain words built from entity names and the predicate's label ("dana works at acme"), never ids or vocabulary terms |
| `NoteRef` | `{note_id, title, kind, occurred_at, excerpt}` |
| `Insight` | `{note_id, title, body, insight_kind, occurred_at, acked_at, evidence_note_ids, entity_ids}` |
| `GET /note/overview` | `{recent: [NoteRef], entities: [{entity_id, name, display_name, kind, note_count, last_seen}], insights: [Insight], resurfacing: [NoteRef], activity: [{day, notes, revisions}]}` |
| `GET /note/cues` | `{items: [{cue_id, text, kind, subject: {kind, id, title}, evidence_note_ids, entity_ids}]}` (cues are insight notes; `cue_id` is the note id; `evidence_note_ids` and `entity_ids` are the insight's own: the notes it rests on and the entities it is about) |
| `GET /note/tasks` | `{items: [{task_id, note_id, text, status, due, closed_at, age_days, stale}]}` (`age_days`: whole days since the task opened, until it closed for a closed one; `stale`: still open after 60 days, `STALE_DAYS` in `abstractnote.recall.reflect`, and never for a task whose note's date is approximate: a commit, a file date, a store update) |
| `GET /note/search` | `{query, order, as_of, considered, omitted: {entities, claims, facet_entities}, notes: [{note_id, title, kind, occurred_at, excerpt, score, relevance, matched_by, matched_entities, matched_claims, entity_ids, topics, insight_kind}], entities: [{entity_id, name, display_name, kind, aliases, summary, note_count, first_seen, last_seen, first_at, latest_at, note_ids, score, matched_by, merged_into}], claims: [{claim_id, subject_id, subject_name, predicate, object_id, object_name, object_literal, certainty, stance, status, observed_at, valid_from, valid_until, evidence, note_id, note_ids, superseded_by, disputed_with, reinforced_count, score}], facets, warnings}` |
| `GET /note/entities/{id}` | `{entity_id, name, display_name, kind, aliases, summary, summary_history, first_seen, last_seen, note_count, as_of, beliefs: [claim], relations: [{predicate, direction, other_id, other_name, count, first_seen, last_seen, active}], notes: [NoteRef], open_tasks, sparkline, community: {community_id, label, kind, size, stale_label} or null, merged_into, merged_from, resolved_from, retired: {at, revision_id, note_id, note_title, reason, into: [{entity_id, name}], sentence} or null}` (`retired`: set when a note understood again withdrew the entry, ADR 0008; `into` are the entries the new reading uses for the words that made it; null with `at`) |
| `GET /note/entities/{id}/timeline` | `{entity_id, since, until, items: [{type: note|claim|superseded|retracted, at, note?: NoteRef, claim?: claim}]}` (claims carry `evidence`, not a rendered sentence) |
| `GET /note/notes/{id}` | `{note_id, title, text, kind, occurred_at, date_basis, date_approximate, received_at, status, topics, summary, source, location, attachments: [...], revision: {...} or null, entities: [...], claims: [...], revisits: [NoteRef], insight: {kind, evidence_note_ids, entity_ids, acked_at} or null, interpretations: [{revision_id, at, recorded_at, action, summary, context, context_unknown, superseded_by, changes}], reinterpret_pending: [string], part_of: NoteRef or null, children: [NoteRef], reinterpret_error: string or null}` (`part_of` is the container of a dated part, null otherwise; `children` a container's dated parts in history order, [] otherwise (W30, data-model.md "Dated parts"); `insight` is set on an insight note, `kind` being its `insight_kind`, and null on every other note; `date_basis` says how `occurred_at` was found, `date_approximate` whether it is a stand-in a client may offer to correct: data-model.md "Note"; `interpretations` is how the notebook understood the note over time (ADR 0008), oldest first: `action` null for the first understanding and `note.reinterpret` for each re-understanding, `context` `{as_of, entities, claims, summaries}` what that reading saw (null and `context_unknown` true for a reading recorded before schema 3), `superseded_by` the reading that replaced it; `changes` for a re-understanding `[{entity_id, name, magnitude, deep, notes_marked}]` how deeply it changed each idea and how many later notes its deep changes marked (ADR 0008 §4a, data-model.md "Deep changes reach the ideas that relied on them"), [] for a first reading; `reinterpret_pending` the reasons the note waits to be understood again, [] when it does not; `reinterpret_error` why the last pass could not read it again (held for the day, held by another reading, the error of a failed reading: the sentence its pending mark keeps, W31), null when none; `revision` is the latest reading) |
| `GET /note/review` | `{items: [{review_id, kind, why, reason, at, sides: [ReviewSide], evidence_note_ids, actions: [{action, method, path, body}]}], next_cursor}` (newest first; `kind` `close_match`, `merge` or `contradiction`; `why` the rule; `reason` the processor's stated reason for a merge, else null; `at` when the notebook recorded it; `evidence_note_ids` starts with the note that raised it; `next_cursor` is always null) |
| `ReviewSide` | `{type, id, name, kind, note_ids}` (`type` `entity`: `name` its canonical name, `kind` its entity kind, `note_ids` its newest notes; `type` `claim`: `name` the statement in plain words, `kind` null, `note_ids` the note it came from) |
| `POST /note/review/{id}/confirm` | the revision result (`{revision_id, summary, entities_merged, ...}`) plus `review_id` |
| `GET /note/entities/{id}?as_merged=true` | `{entity_id, kind, name, display_name, aliases, summary, first_seen, last_seen, note_count, merged_into, merged_at, merge: {revision_id, at, recorded_at, reason, summary, note_id} or null, notes: [NoteRef]}` (the entry's own name, aliases, summary and notes; `merge` is the revision that joined it into `merged_into`, null when it is not merged; `at` is refused with 400) |
| `POST /note/entities/normalize` | `{dry_run, groups: [{into_id, from_ids, name, names, kinds, kind_suffix}], merges: [{from_id, into_id, revision_id}]}` (`kind_suffix` the entries of `from_ids` that are the name with its kind echoed in parentheses, `memory (software)` next to `Memory`, data-model.md "Entity"; `name` the shared canonical name, `names` and `kinds` each entry's surface form and kind, the surviving entry first; `merges` is empty on a dry run; a notebook opened without its embedding model answers the dry run and refuses the merge with 400) |
| entity `name`, `display_name` | everywhere an entity appears (lists, cards, graph nodes, overview, changes), `name` is its one canonical name (lower case, whitespace and punctuation as one `_`: `laurent_philippe_albou`) and is what a client shows; `display_name` is the first way a note wrote it (`Laurent-Philippe Albou`); `aliases` list other names once per canonical name |
| `GET /note/queue` | `{stats: {queued, processing, failed, by_priority, oldest_enqueued_at}, failed: [{note_id, title, occurred_at, last_error, failed_at, failures, errors: [{attempt, at, error}]}], waiting: [{note_id, title, occurred_at, priority, state, attempts, next_attempt_at}], automations: Automations, processor: {max_items, interval}}` (`processor` is what the processor automation was created with while it exists, otherwise the `processor_schedule` setting it will be created with; `interval` is a duration such as `2m`; `errors` are a failed note's last 3 logged failures, newest first, from the `queue_errors` log every failed attempt writes, an expired lease included, that nothing clears) |
| `GET /note/processing` | `{now, processor: {id, state, paused, healthy, reason, target, schedule: {every, max_items}, last_run_at, last_status}, reflect: {id, state, paused, healthy, reason, target, last_run_at, last_status, last_ended_at?, insights_last_run, communities: {count, unlabelled, stale_label, computed_at}}, queue: {queued, processing, failed, done, by_priority: {"3","2","1","0"}, oldest_enqueued_at, eta_s}, pace: {per_minute_5m, per_minute_1h, series: [{t, understood}]}, active: [ActiveRun], recent: [{revision_id, note: NoteRef, at, recorded_at, summary, entities_created, claims_added, claims_superseded, duration_s}], failed: [{note_id, title, error, attempts, next_attempt_at, failed_at}], model: {provider, model, source, thinking_bulk: "off", thinking_live: "on"}, consolidation: object or null, consolidation_error?}`. Automation fields as `AutomationHealth` (null states without a server); `last_run_at`/`last_status` are the automation's latest occurrence (`running`, `waiting`, `completed`, `failed`: a run that ended with `success: false` is `failed`); `insights_last_run` the insights that run wrote. "Understood" = an applied processing revision (user actions are not counted) by `recorded_at`; `series` is 24 five-minute buckets on the UTC clock, the last one the current partial bucket (`t` its start); `per_minute_1h` = understood in the last 60 minutes / 60; `eta_s` = queued / that rate (null when nothing was understood in the last hour). `recent`: the last 20 processing revisions, newest recorded first, counts of what each created; `duration_s` the start-to-end seconds of the note-process run that applied it (confirmed by the run's own output), null when no such run is found (headless processing). `failed` lists at most 50 failed notes, newest first. `model`: the notebook's `filing_model` when set (`source: "notebook"`), else the server's text default (`source: "server"`), else nulls. `consolidation` is what the consolidation module answers, null while it is not installed; a failure inside it is logged and named in `consolidation_error` (the page still answers) |
| `ActiveRun` | `{run_id, flow, status, started_at, parent_run_id, child_run_id?, note: NoteRef or null, step, step_started_at, sections_done, sections_total, thinking}`: a RUNNING or WAITING run of `note-drain`, `note-process`, `note-reflect`, `note-recall`, `note-mine` or `note-capture` in your plane, oldest first. `step` (`context`, `extract`, `resolve`, `link`, `apply`, `filing`, `search`, `synthesize` or null) is the node of the run's latest started effect, mapped by `abstractnote.recall.processing.STEP_OF_NODE` (a lease or an acknowledgement has none); a run whose latest effect is a subflow (a drain on its note-process, a recall on its mine, a capture processing inline) takes note, step, sections and thinking from that active child (`child_run_id`), so a drain and its note-process both appear. `sections_total` = the sections the note's context returned, `sections_done` = extractions completed since (null before the context is read); `thinking` = the run's `thinking` input (`on`/`off`), null for flows without one |
| `POST /note/queue/{id}/priority` | `{note_id, priority}`; `404` for a note not in your notebook, `400` for a priority outside 0..3 or a note that is not waiting, `422` for a priority that is not a whole number or any other field |
| `Automations` (`GET /note/status`, `GET /note/queue`) | `{processor_id, reflect_id, consolidate_id, processor: AutomationHealth, reflect: AutomationHealth, consolidate: AutomationHealth + {schedule: {every}}}`; the consolidate automation is also unhealthy ("The schedule changed.") when it runs another interval than `consolidate_schedule` (never while the setting is `off`, when it is paused) |
| `AutomationHealth` | `{id, state, target, healthy, reason, paused}`: `state` is the automation's state (`active`, `paused`, `failed`, `completed`, `archived`) or `missing` (never created, or gone); `target` the workflow it runs (`abstractnote@<version>:note-drain`); `healthy` is true only when it is active or paused AND runs the version of the note workflows this gateway serves AND its run input allows exactly the current note tools (`abstractnote.tools.TOOL_NAMES`; otherwise `reason` is "The tool list changed."); `reason` one sentence when it is not (null otherwise); `paused` whether the user paused it. Unhealthy = `POST /note/processor/ensure` repairs it |
| `POST /note/processor/ensure` | `{processor_id, reflect_id, consolidate_id, created, processor: EnsureAction, reflect: EnsureAction, consolidate: EnsureAction}`; `EnsureAction` = `{id, action, reason, previous_id?}`: `action` `created` (there was none), `kept` (healthy) or `recreated` (the previous one, `previous_id`, was archived and replaced; `reason` is its health reason); `created` lists the flows of every new automation (`[]` when all were kept). The consolidate automation's first occurrence is one interval after it is created, like reflect's; it is created paused when `consolidate_schedule` is `off`, and a replacement of one the user paused is paused again. `503` when the note workflows are not loaded: nothing is archived |
| `POST /note/refile` (202), `GET /note/refile` | `POST`: `{refile: Refile}`; `GET`: `{refile: Refile or null, missing}` (`missing`: understood notes still without a kind). `Refile` = `{refile_id, started_at, finished_at, status, total, done, filed: [{note_id, kind, topics, seconds}], skipped: [{note_id, reason}], kinds: {kind: count}, not_reached, max_items, budget_s, seconds, model, notice}`: `status` `running`, `completed`, `completed_with_gaps` (a note could not be filed, or the count or time did not reach every note: `not_reached`) or `interrupted`; `total` the notes without a kind when it opened; a note whose run did not end within the time is skipped with "Its filing did not end within the time given; it goes on, and the note is filed when it ends."; `notice` the one sentence of what is left (null while running or when nothing is) |
| `Consolidation` (`GET /note/consolidation`, `consolidation` of `GET /note/processing`) | `{automation: {id, state, paused, healthy, reason, schedule: {every}}, run: {run_id, started_at, stage, stage_done, stage_total, eta_s, elapsed_s, propagated} or null, last: {consolidation_id, started_at, finished_at, status, error, merges, communities, labelled, summaries_refreshed, insights, reinterpreted, unchanged, reinterpret_failed, left_out: {summaries, labels, reinterpret, propagated, propagated_from: [{entity_id, name, notes}], reasons, reinterpret_reasons: [{reason, notes}]} or null, budget_spent, notice, why} or null, pending: {stale_labels, unlabelled, stale_summaries, reinterpret}, reinterpret: {pending, by_entity: [{entity_id, name, notes}], reasons, context_unknown, consumed_today, last_marked_at, errors: [{error, notes}], propagated: {pending, by_source: [{entity_id, name, notes}], may_follow, may_follow_from: [{entity_id, name, notes}]}, candidates, marked, sources, top_sources: [{entity_id, name, signal, words, change: {kind_change, summary_distance, claim_share, relation_share}, mass, strength, notes, contribution}], candidate_sources: [same], would_mark_at: {"0.8", "1.2"}, theta, floor, threshold}}` (`reinterpret`: what waits to be understood again and why, readable before the stage runs: the notes marked, the entities most marked by, up to 10 distinct reason sentences, the marked notes whose reading predates the dependency record, the marks taken today, the last mark's time; `propagated` (ADR 0008 §4a): the notes waiting only because an idea they rely on was understood differently, by source idea (display names), and `may_follow`, an upper bound of the notes such marks may add when the waiting notes are read again, with the ideas it comes from; the activation gate's record (AbstractMemory ADR 0001 rule 7, data-model.md "The activation gate") over the candidates not read again since an earlier note reached them: `candidates` (notes), `marked` (the notes the gate marked), `sources` (how many touched ideas weigh on the marked notes) and `top_sources` (at most 5: those whose measured change weighs most on the marked notes, what waits and why; `candidate_sources` the same over every candidate, marked or not, for tuning; `signal` = the dominant one, `kind` `identity` (an exact-name close match from a new entry) `name` `summary` `claims` `relations` or `task`, `words` its end-user phrase such as "changed its kind", the measured change, the `mass` it was divided by, the `strength`, the candidate notes it reaches and its summed `contribution`), `would_mark_at` (how many candidates the stored potentials would mark with `theta` scaled by 0.8 and 1.2), `theta`, `floor` and `threshold` (the potential that marks). `pending.reinterpret` stays the count. `run.propagated` is `{notes, names, sources}` during the reinterpret stage (of the notes the pass reads, those read because an idea they rely on changed, the most frequent ideas' names, at most 3, and how many ideas), null in the other stages; `left_out.propagated` the notes left for that reason, `propagated_from` their source ideas, and the notice adds "of which 5 follow from ideas understood differently"). `stage` is `normalize`, `reinterpret`, `communities`, `labels`, `summaries`, `then_now` or `reflect` (cognition.md "note-consolidate"); `reinterpreted`, `unchanged`, `reinterpret_failed` (ADR 0008): the notes the reinterpret stage understood again, found unchanged without a model call, or could not read again (their marks wait); `pending.reinterpret` the notes marked to be understood again; `stage_done` of `stage_total` items; `eta_s` = the stage's items left x its measured seconds per item (the last completed run's measure until one item is done; null when neither exists); `elapsed_s` since the consolidation began; `run_id` the automation's running occurrence (null for a consolidation started otherwise). `last.status` is `completed` (nothing was left out), `completed_with_gaps` (the run finished its stages but left items for the next one: `left_out` counts the skipped entries, groups and notes still pending at the close plus what the time budget did not reach, never more than `pending`; `reasons` are the sentences why; `budget_spent` says the budget stopped a stage; `notice` is the end-user sentence, for example "12 summaries could not be refreshed this time; they will be tried again next time.", ADR 0007; for notes the reinterpret stage left, `left_out.reinterpret` counts every note the pass took and could not read again (held for the day, held by another reading, failed: read from the marks), `reinterpret_reasons` how many wait for each reason, the notice adds "Why: Already understood again three times today; read again tomorrow (35 notes)." and `why` is the causes the run row keeps (W31, F6); `error` stays null for a finished run), `failed` (`error` = the step's sentence) or `interrupted` (it stopped before closing: its automation run ended, or a later one began; `left_out` null); `merges`, `labelled` and `summaries_refreshed` count this run's work, `communities` the groups found, `insights` those the reflect stage wrote. `pending`: communities with a stale label, without one, and entries whose summary is stale (empty, still the placeholder they were created with, or older than their notes and statements) |
| `GET /note/communities` | `{computed_at, resolution, networkx, revision_count_at_compute, revisions_since, loose_ends, items: [Community]}` (`networkx` the version the last computation ran under, null if it recorded none; `computed_at` null, `items` empty before the first computation; `revisions_since` counts revisions applied after it, so a client can say the groups are being refreshed; `loose_ends` counts the live entities in no community) |
| `Community` | `{community_id, label, summary, kind, size, top_entities: [{entity_id, name, kind, note_count}], stale_label, labelled_at, created_at}`, largest first: `label` (at most 4 words), `summary` (one sentence) and `kind` (`project`, `theme`, `people`, `tooling`, `process` or `other`) are null until the reflect flow names it; `top_entities` are its 5 members with the most notes; `stale_label` true when the label is kept but more than 20 % of the members changed since it was given (the next reflect run names it again) |
| `POST /note/communities/recompute` | `{communities, loose_ends, entities, edges, new, labels_kept, stale_labels, unlabelled, resolution, computed_at, networkx, carry_over}` (`new`: communities that matched no previous one; `labels_kept`: labelled communities that kept their id; `carry_over` is null unless the previous computation ran under another networkx version or recorded none, then `{from_networkx, to_networkx, floor: 0.5, kept: [{community_id, label, jaccard}], stale: [{community_id, previous_community_id, label, jaccard}], dropped: [{community_id, label}]}`: `kept` the labels that kept their id only thanks to the lower floor, `stale` labels handed, marked stale, to the new group that overlaps them most, `dropped` labels no new group overlaps; a resolution that is not a number above 0 and at most 100 is 400; a notebook opened without its embedding model is 400) |
| graph node `community_id`, entity card `community` | every `GET /note/graph` node carries `community_id` (null for an entity in no community, or before the first computation); `GET /note/entities/{id}` carries `community: {community_id, label, kind, size, stale_label}` or null. Both are the communities as last computed, also with `at` or `as_received` |
| `GET /note/stats` | `{generated_at, span: {first_note_at, last_note_at, first_received_at, last_received_at}, totals, activity_weekly: [WeekActivity], recorded_weekly: [{week, notes_received, revisions_recorded}], entities_by_kind: {kind: n}, top_entities: [{entity_id, name, display_name, kind, note_count, first_seen, last_seen}], claims_by_predicate: [{predicate, words, count, active}], tasks: {open, closed, done, dropped}, insights_by_kind: {insight_kind: n}, communities, processing: {understood, queued, processing, failed, duplicate}, growth_monthly: [{month, entities, claims, new_entities, new_claims}], quiet_entities: [{entity_id, name, kind, note_count, last_seen, days_silent}], omitted: {top_entities, claims_by_predicate, quiet_entities}}` (`omitted`: how many each of the three lists leaves out). Notes are the ones you sent (not insights, not archived). `totals` = `{notes, notes_archived, insights, attachments, attachment_bytes, entities, entities_merged, entities_retired, claims, claims_active, revisions, topics, notes_by_kind: {kind or "unfiled": n}, notes_by_source: {client or "importer:<name>": n}, attachments_by_mime: {mime: n}}`. `top_entities`: the 20 live entities with the most notes. `claims_by_predicate`: the 15 most used predicates (`words` its plain words, `active` how many are still current), ties by predicate. `processing.queued` includes notes not filed yet. `communities` counts the stored communities. Maps are ordered by count |
| `WeekActivity` | `{week, notes, revisions, new_entities, claims_added, claims_superseded}`: `week` is the Monday (`YYYY-MM-DD`); in history time, "happened" (a note's date, a revision's `at`, an entity's `first_seen`: its earliest mention, which moves back when an earlier note arrives, a statement's `observed_at`, a supersession's end), every week from the earliest to the latest one, empty weeks included |
| `recorded_weekly[]` | `{week, notes_received, revisions_recorded}`: the same weeks in system time (when notes reached the notebook and when it applied revisions), from the first to the last such week, so a page shows "happened" (`activity_weekly`) beside "recorded"; `span` adds `first_received_at`, `last_received_at` beside `first_note_at`, `last_note_at` |
| `ThenNow` (`GET /note/entities/{id}/then-now`) | `{entity_id, first_seen, earliest, latest, earliest_claims: [Claim], current_beliefs: [Claim], turning_points: [TurningPoint], revision_count}`, everything in history order (dated knowledge, ADR 0008 §2), never the order notes arrived or summaries were written. `first_seen` = the earliest dated knowledge (the first note about it, or its earliest statement). `earliest` = `{at: first_seen, summary, note_id, revision_id, recorded_at, text?}`: `summary` is the summary that describes that first day (null when none does; then `earliest_claims` are the statements observed that day, and with none, `text` is the first note's own one line). `latest` = the current summary `{at, summary, note_id, revision_id, recorded_at}` (the last in the summary history, the card's). A summary of a superseded interpretation is in neither |
| `TurningPoint` | `{at, kind, text, claim_id?, note_id, revision_id, ...}` from the statements' validity windows, sorted by `at`: `supersede` (a belief replaced: `at` = the end of its window = the next belief's start; `valid_from`, `valid_until`, `superseded_by`, `replaced_by: {claim_id, text, valid_from, note_id}`, `reason`; `text` = "old → new"; `note_id` = the note that brought the change), `ended` (a window the note gave ended with nothing replacing it, `at` = its end, only once that date is past), `retract` (withdrawn, `at` = when), `dispute`, `merge`. Never a summary rewrite, and never a statement closed with the kind `reinterpreted` (its note understood again; `claims.status`); a closure kind it does not know is an error, never read as a change |
| `growth_monthly[]`, `quiet_entities[]` | `growth_monthly` runs month by month (`YYYY-MM`) from the first entity or statement to the last, with running totals of live entities (by first mention) and statements (retracted ones left out) and the month's new ones; `quiet_entities` are live entities with at least 2 notes and no note for 60 days or more (`SILENT_DAYS`), most mentioned first, at most 20 |
| `note-capture` output | `{response, note_id, status, revision_summary?}` |
| `note-recall` output | `{response, answer, evidence: [NoteRef], evolution, open_loops, follow_ups, dossier_note_id?, hits, effort}` |

Linking a new note to a subject: clients may send `input_data.context.about = {kind: insight|task|cue|entity|note, id, title}` on `note-capture`; `note_capture` records it and the revision links the note to that subject (`dcterms:references`), so answering a cue or commenting an insight is traceable.

Saving an answer: the web app's "Save this answer" sends `input_data.context.answer = {question, evidence_note_ids}` on `note-capture`; `note_capture` files the note as kind `synthesis` (the quick filing keeps that kind), keeps the `source` as given, and links it to each evidence note in this notebook with `dcterms:references` (the same link `context.about` makes; evidence ids outside the notebook are left out with a warning).

Graph response:

```json
{"as_of": "2026-10-19T00:00:00Z",
 "nodes": [{"id": "ex:person-dana", "kind": "person", "name": "Dana", "first_seen": "...", "last_seen": "...", "note_count": 12, "summary": "...", "community_id": "cm_..."}],
 "edges": [{"source": "ex:person-dana", "target": "ex:project-lut", "predicate": "schema:participant", "first_seen": "...", "last_seen": "...", "count": 5, "active": true}]}
```

## Note tools

Tools are Python callables registered with AbstractCore's `@tool` decorator and bound to a
notebook directory. Inside the gateway they are bound to the caller's plane when the runtime
is built; headless, `abstractnote.tools.bind(notebook_dir)` binds them to a local notebook.
Flows call them through `tool_calls` nodes; agents may list them in `allowed_tools`.

| Tool | Arguments | Returns |
|---|---|---|
| `note_capture` | `text, idempotency_key?, title?, occurred_at?, location?, source?, priority?, attachments? ([{"$artifact": id, "last_modified"?}] inside the gateway; [{"path": ..., "last_modified"?}] only headless, inside the folder given to bind as allow_paths_under), session_id?, about?, answer? ({question, evidence_note_ids}), process_inline? (claims the note for the caller's own understanding)` | `{note_id, status: new|duplicate, duplicate_of?, first_saved_at?, received_at, occurred_at, date_basis, date_sentence (null unless the date came from a file), revisits: [{note_id, occurred_at}], similar: [{note_id, title, occurred_at, score}], topics: [...], attachments: [{attachment_id, sha256, mime, size, extracted_by, extracted_chars}], warnings: [...]}` |
| `note_file` | `note_id, title, kind, topics, urgency, language, one_line` | `{note_id, priority, status, topics_not_assigned}`: a note keeps the first 4 filing topics; the others are named in `topics_not_assigned` and logged |
| `note_filing_context` | `note_id` | `{needed, note_id, occurred_at, title, text, similar, revisits, topics}`: `needed` = the note has no kind, is not an insight or archived and has something to read; the rest is what the capture's quick filing sees (note-process and the `note-file` flow, cognition.md "note-process" step 0) |
| `note_context` | `note_id, max_chars?` | `{note: {...}, sections: [...], similar_notes: [...], candidate_entities: [{entity_id, name, kind, aliases, summary, last_seen, active_claims: [...]}], open_tasks: [...], topics: [...], vocabulary: [{predicate, gloss}]}` |
| `note_resolve_candidates` | `note_id, mentions: [{ref, name, kind, aliases}]` | `{mentions: [{ref, candidates: [{entity_id, name, kind, aliases, summary, last_seen, score, matched_by}]}]}` |
| `note_apply_revision` | `note_id, extraction, linking, reinterpret=false` | `{revision_id, summary, entities_created, entities_merged, claims_added, claims_reinforced, claims_superseded, claims_retracted, tasks_opened, tasks_closed, claims_inserted_before, retrospective, warnings, marked, reinterpret_marked: {count, notes, by_entity: [{entity_id, name, notes}]}}` (`claims_inserted_before`: statements of an older note placed before a later belief; `retrospective`: entities the note marked, data-model.md "Retrospective insertion"; `marked`: the later notes this revision marked to be understood again, ADR 0008; `stale_context`: statements the linking named that are no longer believed, with a warning (when one was closed after the note's context was read and a drain holds the note, nothing is applied the first time: `{revision_id: null, requeue: true, reason, stale_context}`, cognition.md "note-drain and the queue"); `date_sentence` when the note's text dated it, with what lost: "Dated 14 March 2019 from the document; the file itself is from 20 March 2026."); W30: an extraction whose `moments` read the note as a document of dated entries splits it (data-model.md "Dated parts"): the result is the container's revision plus `split: {parts: [{note_id, title, occurred_at, date_basis, status}], created, duplicates}`, and a container read again answers `unchanged` (its parts are notes of their own; `ops_refused` `[{index, op, claim_id, successor_id, code, why}]` when the supersession invariants refused an op (a cycle, a later belief erased by an earlier one, a missing successor: the rest applied, a `invariant_refused: ...` warning names it, data-model.md "Invariants"), `kg_refused` when AbstractMemory refused a closure on its own state); a structurally corrupt answer (cognition.md "Validity of model answers": a fragment of the answer inside a prose field, a decision that names no candidate from its list or a mention the note does not have, an index answered twice in contradiction) is refused with one sentence and nothing is applied, for a re-reading too; other references outside the context packet (an existing statement, an open task, a statement's subject) are rejected into `create`/`new` with a warning. A note already understood answers `already_applied`, unless `reinterpret` is true or a consolidation pass took its re-understanding mark: then it is read again (`note.reinterpret`, data-model.md "Re-understanding"): the result adds `reinterpreted`, `previous_revision_id`, `diff` and `claims_reinterpreted`, `claims_revised`, `claims_reopened`, `claims_start_restored`, `tasks_reopened`, `tasks_retired`, `entities_retired` when they apply, and `propagation` `{records, deep, carried, hops, marked, upgraded, reached, below_floor, hop_cut, invisible, already_spread, sources}` (ADR 0008 §4a: how many ideas it measured and found deeply changed, the impact it was given, and the later notes of proximal ideas it marked), or is `{revision_id: null, unchanged: true, summary_only, previous_revision_id, ...}` when the new reading equals the old one |
| `note_queue_lease` | `worker_id, max_items=1` (1 to 100) | `{items: [{note_id, priority, attempts}], limit_applied}` (`limit_applied`: `max_items` after the clamp, which is logged) |
| `note_queue_ack` | `note_id, outcome: ok|error|requeue, error?, revision_id?` | `{note_id, status, next_attempt_at?}`; an error for a note with no text to read is final (`status` `failed` at once, the reason recorded); `requeue` (a drain's note whose reading was refused because the notebook changed while it was read) puts it back at its priority without a failure, the reason in `queue_errors` (`{note_id, status: queued, requeued: true, priority}`) |
| `note_queue_stats` | | `{queued, processing, failed, by_priority, oldest_enqueued_at}` |
| `note_search` | `query, since?, until?, kinds?, entities?, topics?, limit=20, as_of?, order? (relevance or earliest)` | `{query, order, notes: [...], entities: [...], claims: [...], facets: {...}, considered, omitted, warnings}` (`omitted`: the entity and statement hits past their 10 and the entity facets past 20; a limit outside 1 to 200 is applied and named in `warnings`): each note carries `score` (rank fusion), `relevance` (0 to 1), `matched_entities` and `matched_claims`; an entity or statement is a hit only when its relevance reaches 0.5 (`HIT_MIN_SCORE`) and a returned note carries it, and lists those `note_ids` (an entity also `first_at` and `latest_at`: the first and latest date among the matching notes that carry it); `order=earliest` lists the notes whose relevance reaches 0.75 (`EARLIEST_MIN_SCORE`) oldest first ([remembering.md](remembering.md#quick-relevance)) |
| `note_expand` | `note_ids, entity_ids, as_of?, query?, budget_chars?` | the standard recall packet, chosen within `budget_chars` (default 24000, `EXPAND_BUDGET_CHARS`, measured as JSON): `{as_of, budget_chars, used_chars, notes: [NoteRef with an excerpt around the match of query, else of the focus entities' names, and summary?], entities: [{entity_id, line, focus}], claims_timeline: [{entity_id, claims: [{claim_id, at, text, status, note_id, closed?}]}], relation_timelines: [{a, b, claims}], neighbours: [{entity_id, name, kind, count, via}], then_now: [{entity_id, earliest, latest, turning_points}], omitted: {notes, entities, claims, turning_points, relation_timelines, neighbours, then_now}}` (`omitted` counts what was not shown, for the budget or a part's bound: focus entities past 5, statements between the earliest and the latest kept, pairs past 10, neighbours past 10, ADR 0007; excerpts and one-line cards are marked previews ending with "…", aliases with "+n more"); the given `entity_ids` are the focus: their one-line cards come first, then the notes in the order given (best first, up to 60 % of the budget), the other entities those notes mention as one line each, and for the focus entities only the claim timelines (earliest and latest kept), then-versus-now, relations and neighbours; what is left goes to further notes |
| `note_changes` | `since, limit` | the since-you-last-looked delta (same shape as `GET /note/changes`) |
| `note_entity` | `entity_id, at?` | the entity card |
| `note_timeline` | `since, until, granularity` | buckets |
| `note_open_loops`, `note_contradictions`, `note_resurface`, `note_anniversaries` | `limit, since?` | candidate lists for the reflect flow (`{items, omitted}`: the first `limit` candidates, `omitted` the number not listed; each with its `rule`, `note_ids` and `entity_ids`; an open loop also carries `age_days` and `stale`, as `GET /note/tasks` does, counted from `last_mentioned_at` (its note or the latest note revisiting it), with `date_approximate`; contradictions are only the disputed pairs a note's linking marked; a candidate whose notes and entities already back an unacknowledged insight is left out) |
| `note_insight_write` | `kind, title, body, evidence_note_ids, entity_ids, run_id?` | `{note_id, status: new|duplicate, warnings}`; `duplicate` returns the existing note: the same insight, or an unacknowledged one of the same kind with the same evidence notes and entities |
| `note_communities_recompute` | `resolution?`, `only_if_due?` | the counts of `POST /note/communities/recompute`; with `only_if_due` (the end of a drain) it recomputes only when due and answers the drain's `communities` object above |
| `note_communities_pending` | `limit=5` (1 to 20), `exclude?` (community ids not to offer: a consolidation's groups skipped for this run) | `{count, items: [{index, community_id, size, stale_label, previous_label, previous_summary, top_entities: [{entity_id, name, kind, note_count, summary}], claims: [{claim_id, text}], excerpts: [NoteRef], omitted: {claims, excerpts}}], waiting, limit_applied}` (`waiting`: every group still waiting outside `exclude`; `limit_applied`: the limit after the clamp): communities with a stale label first, then those without one, largest first within each; `claims` are up to 8 active statements among the members (links between members first, then the most reinforced and the most certain), `excerpts` the 3 notes that mention the most members |
| `note_communities_label` | `items: [{community_id, label, summary, kind}]` | `{labelled, warnings}`: a label of more than 4 words is kept whole with an `over_length` warning, an unknown kind becomes `other`, an unknown community or an empty label is left out, each with a warning; labelling clears `stale_label` |
| `note_consolidation_begin` | | `{consolidation_id, started_at, planned_merges, pending: {stale_labels, unlabelled, stale_summaries}}`: opens a consolidation row (an earlier one still open is closed as `interrupted`) |
| `note_consolidation_progress` | `stage, done, total, note?, skipped?, reason?` | `{consolidation_id, stage, stage_done, stage_total, seconds_per_item, eta_s, elapsed_s, skipped}`: records the open consolidation's progress (`stage` one of the seven stages; the first report of a stage starts its clock); `skipped` (`[id]` or `[{id, reason}]`, `reason` the default sentence) are the items the stage leaves out for this run: recorded once on the stage (`skipped`, `reason`, `reasons`, `skipped_ids`) and logged as a warning on `abstractnote.consolidation` |
| `note_consolidation_end` | `status: completed|completed_with_gaps|failed, results? ({communities, insights, error, budget_spent, reinterpreted, unchanged, reinterpret_failed, not_reached: {summaries, labels, reinterpret}})` | the finished row as `last` of the Consolidation shape; `merges`, `labelled` and `summaries_refreshed` come from the stages' progress; a finished run is closed as `completed_with_gaps` when `left_out` is not empty, `completed` otherwise, whatever was asked |
| `note_entities_normalize` | | `{merges, groups}`: `Notebook.normalize_entities` with source client `consolidation` |
| `note_entities_stale_summaries` | `limit=8` (0 to 50; 0 = only the count) | `{count, items: [{entity_id, reason: empty|placeholder|retrospective|changed, since_summary: {notes, claims}, summary_revision_count, retro_marked_seq?, note_count, name, kind, aliases, summary, as_of, claims: [text (since date)], history: [{date, summary}], earlier: [{from, until, statement}], notes: [{title, date, excerpt}], omitted: {claims, notes, history, earlier}}], limit_applied}` (`omitted` counts what each part leaves out; `notes[].new`: recorded after the current summary): `summary_revision_count` is the revision-log sequence of the entry's CURRENT summary (the last in dated order); `history` and `earlier` are the dated history earliest first (cognition.md "note-consolidate"); while a consolidation is open an entry is offered once and `count` is what remains for it |
| `note_entities_set_summaries` | `items: [{entity_id, summary}]` | `{stored, revision_id, warnings, left_out: [{entity_id, reason}], over_length: [entity_id]}`: one `entity.summary` revision (run_id = the open consolidation, source client `consolidation`), each op dated `as_of` the entry's latest dated knowledge (its last mention, or its current summary's date when later), so it is the current summary until a note dated later writes one; an unknown or merged entry or an empty summary is left out with a warning; a summary over 600 characters is kept whole with an `over_length` warning (also in the revision's warnings); every warning is logged |
| `note_then_now_refresh` | | `{refreshed}`: recomputes the then-versus-now cache of every entry the open consolidation merged into or summarised |
| `note_reinterpret_next` | `limit=1` (0 to 5; 0 = only the counts), `failed?: [id or {id, reason}]` | `{items: [{note_id, occurred_at, reasons}], unchanged: [note_id], held: [{id, reason, code}], done, total, pending, pass_id, reinterpreted, reinterpret_failed}`: the open consolidation's next marked notes in history order, each once per pass (ADR 0008); each is claimed on its queue row before anything reads it; a note whose context did not change is consumed `unchanged` here without a model call; a note is held alone, its mark keeping the sentence and the stage's `skipped_ids` getting it (W31): `code` `held_today` (read again three times today), `held_by_inline` (an inline understanding holds it), `held_by_drain`, `held_by_reinterpret` (another pass), `error` (preparing it raised; the error's sentence); `failed` records the notes the last round could not read again (their marks wait for the next pass) |
| `note_mark_reinterpret` | `note_ids, reason?` | `{marked, already}`: marks understood notes to be understood again by the next consolidation (a note never understood is refused) |

All tools are read-and-write on the caller's own notebook only and are auto-approved. Every
tool returns JSON-serialisable dictionaries and raises a `ValueError` with a plain sentence on
bad input; tools never print or log secrets. Inside the gateway the binding injects the plane's
artifact store and account; tools never accept filesystem paths there.

## Python API

```python
from abstractnote import Notebook

nb = Notebook.open("/path/to/plane/abstractnote")          # creates the stores when missing
note = nb.capture("Met Dana about the LUT budget", occurred_at="2026-10-19T09:12:00Z",
                  source={"client": "cli"})                 # -> CaptureResult(note_id, status, similar)
nb.queue.stats()
hits = nb.search("LUT budget", since="2026-07-01")          # quick tier, no model
card = nb.entity("ex:person-dana", at="2026-09-01T00:00:00Z")
graph = nb.graph(focus=["ex:project-lut"], at=None, depth=2)
day = nb.day("2026-09-14")
nb.recompute_communities()                                 # community detection, no model
groups = nb.communities()                                  # {computed_at, items: [Community], ...}
stats = nb.stats()                                         # the GET /note/stats answer
nb.redate_note(note.note_id, "2021-03-03", "me", "the letter's date")  # "Set the date" (refused for an understood
                                                                         # note until it can be understood again)
nb.retro_state()                    # what retrospective insertions left to refresh (stale summaries, graph_version)
```

`Notebook.open(dir, embedder=...)` takes the embedding model the notebook is pinned to (none
needed for the built-in hashing embedder; `abstractnote.store.embeddings.openai_compat_embedder(url,
model)` for an OpenAI-compatible server). `Notebook` is also a context manager.

Opening a notebook never finishes a revision that a stopped process left half applied, because
another process may still be applying it: reads (status, search, entity cards, days, changes, open
loops) leave it alone. Writes finish it first (capture, filing, processing, user actions, archiving,
through `Notebook.recover_pending()`), and so do `Notebook.recover()`, `Notebook.open(dir, recover=True)`
and `abstractnote recover`.

The note tools and the flows run headlessly too:

```python
from abstractnote.tools import bind, TOOL_NAMES
from abstractnote.flows.runner import NoteRunner

tools = bind("notes")                                      # the 21 note tools, bound to a notebook
runner = NoteRunner.local("notes", provider="mlx", model="mlx-community/Qwen3.6-35B-A3B-4bit")
runner.run_capture("Met Dana about the LUT budget")        # the note-capture flow, quick filing included
runner.run_drain(max_items=20)                             # note-drain: process queued notes
runner.run_recall("What did Dana ask for?", effort="standard")
```

`Notebook` wraps the store (`abstractnote.store.NoteStore`), the AbstractMemory projection
(`abstractnote.store.kg.ClaimStore`), the queue, the embedder, and the recall helpers. The
processing steps (`abstractnote.process`) are pure functions over these objects so that the
flows, the CLI and the tests share one implementation.

## CLI

The `abstractnote` command works on a notebook directory. Output is JSON on standard output;
problems are one sentence on standard error. Exit codes: `0` done, `2` bad input, `3` a part that
is not installed or not available in this version (`abstractnote import`: `1` when some records
failed; they are listed under `errors`).

```
abstractnote capture ["text"] --notebook DIR [--file PATH ...] [--at ISO] [--title T] [--priority 0-3] [--key KEY]
                    [--no-split]                                  # keep a document of dated entries in one note
abstractnote search "query" --notebook DIR [--effort quick|standard|deep] [--since ISO] [--until ISO] [--as-of ISO]
                    [--limit N] [--order relevance|earliest]      # earliest: matching notes oldest first (quick)
                    [--provider P --model M]                      # a model for standard and deep
abstractnote entity ID --notebook DIR [--at ISO]
abstractnote day YYYY-MM-DD --notebook DIR [--tz ZONE]
abstractnote status --notebook DIR
abstractnote changes --notebook DIR [--since CURSOR_OR_ISO] [--limit N]
abstractnote process --local --notebook DIR --provider P --model M [--max-items 20] [--budget-s 90]
abstractnote refile --missing --local --notebook DIR --provider P --model M [--max-items 50] [--budget-s 600]  # file understood notes without a kind
abstractnote recover --notebook DIR                              # finish revisions a stopped process left half applied
abstractnote rebuild --notebook DIR [--reembed]                  # replay revisions into fresh stores
abstractnote normalize --notebook DIR [--dry-run] [--account NAME]  # merge entities that share a canonical name
abstractnote normalize-topics --notebook DIR [--dry-run] [--account NAME]  # one spelling per topic, one revision
abstractnote dedupe-sources --notebook DIR [--dry-run] [--account NAME]    # archive memory files imported twice
abstractnote archive-insights --repeated --notebook DIR [--dry-run] [--account NAME]  # keep the oldest of each repeated insight
abstractnote unarchive NOTE_ID ... --notebook DIR [--account NAME]       # bring archived notes back (one revision)
abstractnote unarchive --revision RID --notebook DIR [--account NAME]    # bring back every note that archive put away
abstractnote bundle [--out DIR]                                  # pack abstractnote@<version>.flow

abstractnote import claude-sessions DIR --notebook DIR [--exclude-session ID ...] [--author NAME]
abstractnote import agora DB_PATH --notebook DIR [--include-channels a,b] [--exclude-channels a,b]   # --channels = --include-channels
                    [--kinds decision,claim,work,thread,operator] [--include-retracted] [--include-system] [--operators a,b]
abstractnote import markdown DIR --notebook DIR [--glob '**/*.md']
abstractnote import files PATH ... --notebook DIR [--no-split]   # --no-split: a diary stays one note
                    # every import also takes [--since ISO] [--until ISO] [--dry-run] [--embed-base-url URL --embed-model MODEL]
```

Every notebook command also takes `--embed-base-url URL --embed-model MODEL` (an OpenAI-compatible
embedding server). A notebook is pinned to its embedding model when it is created, by the first
command that opens it; later commands must give the same model, or run `rebuild --reembed` with
the new one; `abstractnote import` takes the same two flags (see
[troubleshooting.md](troubleshooting.md#this-notebooks-vectors-were-made-with-the-embedding-model-)).

`--gateway URL --token T` and `abstractnote processor ensure` are reserved for a gateway client
mode that is not available in this version (exit `3`); use the HTTP API above instead.
