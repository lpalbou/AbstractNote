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
    "context": {"attachments": [{"$artifact": "<id from /attachments/upload>"}]}
  }
}
```

Response: `{"run_id": "..."}`. Read the answer from `GET /runs/{run_id}` when `status` is
`completed` (`output.response`, `output.note_id`, `output.status`), or follow
`GET /runs/{run_id}/ledger/stream` and show the `answer_user` message as soon as it appears.

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

Run the processor now: `POST /api/gateway/automations/{processor_id}/commands
{"command_id": "<uuid>", "type": "automation.run_now"}` (the id comes from `GET /api/gateway/note/status`;
a `409 automation_busy` answer means a drain is already running).

Every run start also carries `input_data.session_id` equal to the request's `session_id` (the gateway
does not forward the run's session to the flow). Run errors appear in `output` as text starting with
`Runtime error: `; clients strip that prefix before showing the sentence.

## Note read API

Served by `abstractnote.gateway.router` and mounted by the gateway. Reads are available to any
signed-in principal for their own notebook. The few write actions are bookkeeping, not knowledge.

| Method and path | Purpose | Main parameters |
|---|---|---|
| `GET /note/status` | notebook health: counts, queue state, processor and reflect automation ids, model settings, store versions, embedding pin | |
| `GET /note/changes` | since you last looked: new notes, new entities, superseded claims, closed loops, failed notes, insights since a cursor; the default screen for humans and the first call for agents | `since (cursor or ISO), limit` |
| `GET /note/overview` | the landing data: recent notes, active entities, unacknowledged insights, today's resurfacing, activity sparkline | `days` |
| `GET /note/notes` | list notes | `q, since, until, kind, topic, entity, status, cursor, limit` |
| `GET /note/notes/{note_id}` | a note with attachments, revision, claims, linked entities, revisits | |
| `GET /note/notes/{note_id}/attachments/{attachment_id}` | attachment bytes (streams the blob) | |
| `GET /note/search` | quick recall (full-text plus vector), grouped | `q, since, until, kinds, entities, topics, limit` |
| `GET /note/entities` | list or search entities (entries merged into another are left out unless `include_merged=true`; each item carries `merged_into`) | `q, kind, sort=recent|frequent|name, cursor, limit, include_merged` |
| `GET /note/entities/{entity_id}` | entity card as of a moment: name, aliases, summary history, beliefs at `at`, relations, notes, open tasks (a merged id answers with the surviving entity and `resolved_from`); with `as_merged=true`, the entry's own record instead, not followed to the survivor | `at, as_merged` |
| `GET /note/entities/{entity_id}/timeline` | claims and notes about the entity in history order, with supersessions | `since, until` |
| `GET /note/entities/{entity_id}/then-now` | the earliest and latest summaries and the turning points (cached synthesis) | |
| `GET /note/relations/{a}/{b}` | the relationship between two entities through time: claims in history order, supersessions, then-versus-now of the link | `since, until` |
| `GET /note/claims/{claim_id}` | why do I believe this: the claim, the claims it superseded or was superseded by, the notes and attachment excerpts behind it | |
| `POST /note/claims/{claim_id}/retract` | user action: retract a claim (a revision with the account as source) | `reason` |
| `POST /note/tasks/{task_id}/close` | user action: close an open loop | `note?` |
| `POST /note/entities/{entity_id}/unmerge` | user action: undo a merge | `from_id, reason` |

A user action's revision carries `source = {account, client}`: `account` is the signed-in
principal; `client` is `web` when the request came through the Note web app's proxy (which marks
what it relays with `X-AbstractFramework-App-Proxy: abstractnote`) and `api` otherwise. Request
bodies cannot set it (an unknown field such as `client` is refused with 422).
| `GET /note/cues` | cue cards: a few reflect-generated prompts you can answer by adding a note (a person gone quiet, a decision that aged, a task mentioned often and never closed) | `limit` |
| `GET /note/graph` | nodes and edges for the explorer | `focus (entity ids), at, depth (1..3), since, until, kinds, min_count, limit` |
| `GET /note/timeline` | activity per bucket: notes, revisions, new entities, superseded claims | `since, until, granularity=day|week|month` |
| `GET /note/day/{YYYY-MM-DD}` | what happened on a day: notes, revisions with summaries, entities touched | `tz` |
| `GET /note/revisions` | revision feed | `since, until, entity, cursor, limit` |
| `GET /note/topics` | topics with counts and date ranges | |
| `GET /note/tasks` | open loops | `status, entity, limit` |
| `GET /note/insights` | insights | `kind, acked, limit` |
| `POST /note/insights/{id}/ack` | acknowledge an insight | |
| `GET /note/queue` | queue summary, failed notes with errors, and the processor's throughput (`processor: {max_items, interval}`) | `limit` |
| `POST /note/queue/{note_id}/retry` | re-queue a failed note | |
| `POST /note/notes/{note_id}/archive` | archive a note (never delete) | |
| `GET /note/settings` and `PUT /note/settings` | per-notebook settings: filing model, answering model, reflect schedule, processor schedule (`{every, max_items}`, default `2m` and 20; `max_items` 1 to 200), inline processing default; the automations take them when `processor/ensure` creates them | |
| `POST /note/processor/ensure` | create the processor and reflect automations if missing; returns their ids | |

Response envelopes are plain JSON objects; every list (`/notes`, `/entities`, `/revisions`,
`/tasks`, `/cues`, `/topics`, `/insights`) returns `{items, next_cursor}`, with `next_cursor` null
when there is no further page (the last four never page; `limit` bounds them). Times are ISO
8601 UTC. Errors use the gateway's error shape, `{"detail": "<one sentence>"}`: `401` without a
valid sign-in, `404` for an id that is not in the caller's notebook, `400` for bad input, `501`
for a read the installed `abstractnote` does not provide.

Pinned response shapes (clients decode these strictly; absent lists mean empty):

| Endpoint | Shape |
|---|---|
| `GET /note/changes` | `{cursor, notes: [NoteRef], entities: [{entity_id, name, kind, first_seen}], superseded_claims: [{claim_id, kind, text, superseded_by, note_id}], closed_loops: [{task_id, text, closed_at}], failed_notes: [{note_id, title, error}], insights: [Insight]}` |
| `superseded_claims[]` | `kind` is `superseded` (a later note replaced the claim; `superseded_by` is the new claim's id) or `retracted` (withdrawn by a note or by you; `superseded_by` is null); `text` is the claim in plain words built from entity names and the predicate's label ("Dana works at Acme"), never ids or vocabulary terms |
| `NoteRef` | `{note_id, title, kind, occurred_at, excerpt}` |
| `Insight` | `{note_id, title, body, insight_kind, occurred_at, acked_at, evidence_note_ids, entity_ids}` |
| `GET /note/overview` | `{recent: [NoteRef], entities: [{entity_id, name, kind, note_count, last_seen}], insights: [Insight], resurfacing: [NoteRef], activity: [{day, notes, revisions}]}` |
| `GET /note/cues` | `{items: [{cue_id, text, kind, subject: {kind, id, title}, evidence_note_ids, entity_ids}]}` (cues are insight notes; `cue_id` is the note id; `evidence_note_ids` and `entity_ids` are the insight's own: the notes it rests on and the entities it is about) |
| `GET /note/tasks` | `{items: [{task_id, note_id, text, status, due, closed_at}]}` |
| `GET /note/notes/{id}` | `{note_id, title, text, kind, occurred_at, received_at, status, topics, summary, source, location, attachments: [...], revision: {...} or null, entities: [...], claims: [...], revisits: [NoteRef], insight: {kind, evidence_note_ids, entity_ids, acked_at} or null}` (`insight` is set on an insight note, `kind` being its `insight_kind`, and null on every other note) |
| `GET /note/entities/{id}?as_merged=true` | `{entity_id, kind, name, aliases, summary, first_seen, last_seen, note_count, merged_into, merged_at, merge: {revision_id, at, recorded_at, reason, summary, note_id} or null, notes: [NoteRef]}` (the entry's own name, aliases, summary and notes; `merge` is the revision that joined it into `merged_into`, null when it is not merged; `at` is refused with 400) |
| `GET /note/queue` | `{stats: {queued, processing, failed, by_priority, oldest_enqueued_at}, failed: [{note_id, title, occurred_at, last_error, failed_at, failures}], waiting: [{note_id, title, occurred_at, priority, state, attempts, next_attempt_at}], automations: {processor_id, reflect_id}, processor: {max_items, interval}}` (`processor` is what the processor automation was created with while it exists, otherwise the `processor_schedule` setting it will be created with; `interval` is a duration such as `2m`) |
| `note-capture` output | `{response, note_id, status, revision_summary?}` |
| `note-recall` output | `{response, answer, evidence: [NoteRef], evolution, open_loops, follow_ups, dossier_note_id?, hits, effort}` |

Linking a new note to a subject: clients may send `input_data.context.about = {kind: insight|task|cue|entity|note, id, title}` on `note-capture`; `note_capture` records it and the revision links the note to that subject (`dcterms:references`), so answering a cue or commenting an insight is traceable.

Saving an answer: the web app's "Save this answer" sends `input_data.context.answer = {question, evidence_note_ids}` on `note-capture`; `note_capture` files the note as kind `synthesis` (the quick filing keeps that kind), keeps the `source` as given, and links it to each evidence note in this notebook with `dcterms:references` (the same link `context.about` makes; evidence ids outside the notebook are left out with a warning).

Graph response:

```json
{"as_of": "2026-10-19T00:00:00Z",
 "nodes": [{"id": "ex:person-dana", "kind": "person", "name": "Dana", "first_seen": "...", "last_seen": "...", "note_count": 12, "summary": "..."}],
 "edges": [{"source": "ex:person-dana", "target": "ex:project-lut", "predicate": "schema:participant", "first_seen": "...", "last_seen": "...", "count": 5, "active": true}]}
```

## Note tools

Tools are Python callables registered with AbstractCore's `@tool` decorator and bound to a
notebook directory. Inside the gateway they are bound to the caller's plane when the runtime
is built; headless, `abstractnote.tools.bind(notebook_dir)` binds them to a local notebook.
Flows call them through `tool_calls` nodes; agents may list them in `allowed_tools`.

| Tool | Arguments | Returns |
|---|---|---|
| `note_capture` | `text, idempotency_key?, title?, occurred_at?, location?, source?, priority?, attachments? ([{"$artifact": id}] inside the gateway; [{"path": ...}] only headless, inside the folder given to bind as allow_paths_under), session_id?, about?, answer? ({question, evidence_note_ids})` | `{note_id, status: new|duplicate, duplicate_of?, first_saved_at?, received_at, revisits: [{note_id, occurred_at}], similar: [{note_id, title, occurred_at, score}], topics: [...], attachments: [{attachment_id, sha256, mime, size, extracted_by, extracted_chars}], warnings: [...]}` |
| `note_file` | `note_id, title, kind, topics, urgency, language, one_line` | `{note_id, priority, status}` |
| `note_context` | `note_id, max_chars?` | `{note: {...}, sections: [...], similar_notes: [...], candidate_entities: [{entity_id, name, kind, aliases, summary, last_seen, active_claims: [...]}], open_tasks: [...], topics: [...], vocabulary: [{predicate, gloss}]}` |
| `note_resolve_candidates` | `note_id, mentions: [{ref, name, kind, aliases}]` | `{mentions: [{ref, candidates: [{entity_id, name, kind, aliases, summary, last_seen, score, matched_by}]}]}` |
| `note_apply_revision` | `note_id, extraction, linking` | `{revision_id, summary, entities_created, entities_merged, claims_added, claims_reinforced, claims_superseded, tasks_opened, tasks_closed, warnings}`; references outside the context packet are rejected into `create`/`new` with a warning |
| `note_queue_lease` | `worker_id, max_items=1` | `{items: [{note_id, priority, attempts}]}` |
| `note_queue_ack` | `note_id, outcome: ok|error, error?, revision_id?` | `{note_id, status, next_attempt_at?}` |
| `note_queue_stats` | | `{queued, processing, failed, by_priority, oldest_enqueued_at}` |
| `note_search` | `query, since?, until?, kinds?, entities?, topics?, limit=20, as_of?` | `{notes: [...], entities: [...], claims: [...], facets: {...}}` |
| `note_expand` | `note_ids, entity_ids, as_of?` | `{entities: [card...], claims_timeline: [...], relation_timelines: [...], neighbours: [...], then_now: [...]}` |
| `note_changes` | `since, limit` | the since-you-last-looked delta (same shape as `GET /note/changes`) |
| `note_entity` | `entity_id, at?` | the entity card |
| `note_timeline` | `since, until, granularity` | buckets |
| `note_open_loops`, `note_contradictions`, `note_resurface`, `note_anniversaries` | `limit, since?` | candidate lists for the reflect flow |
| `note_insight_write` | `kind, title, body, evidence_note_ids, entity_ids, run_id?` | `{note_id}` |

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
```

`Notebook.open(dir, embedder=...)` takes the embedding model the notebook is pinned to (none
needed for the built-in hashing embedder; `abstractnote.store.embeddings.openai_compat_embedder(url,
model)` for an OpenAI-compatible server). `Notebook` is also a context manager.

The note tools and the flows run headlessly too:

```python
from abstractnote.tools import bind, TOOL_NAMES
from abstractnote.flows.runner import NoteRunner

tools = bind("notes")                                      # the 18 note tools, bound to a notebook
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
abstractnote search "query" --notebook DIR [--effort quick|standard|deep] [--since ISO] [--until ISO] [--as-of ISO]
                    [--limit N] [--provider P --model M]          # a model for standard and deep
abstractnote entity ID --notebook DIR [--at ISO]
abstractnote day YYYY-MM-DD --notebook DIR [--tz ZONE]
abstractnote status --notebook DIR
abstractnote changes --notebook DIR [--since CURSOR_OR_ISO] [--limit N]
abstractnote process --local --notebook DIR --provider P --model M [--max-items 20] [--budget-s 90]
abstractnote rebuild --notebook DIR [--reembed]                  # replay revisions into fresh stores
abstractnote bundle [--out DIR]                                  # pack abstractnote@<version>.flow

abstractnote import claude-sessions DIR --notebook DIR [--exclude-session ID ...] [--author NAME]
abstractnote import agora DB_PATH --notebook DIR [--channels a,b] [--include-system] [--operators a,b]
abstractnote import markdown DIR --notebook DIR [--glob '**/*.md']
abstractnote import files PATH ... --notebook DIR
                    # every import also takes [--since ISO] [--until ISO] [--dry-run] [--embed-base-url URL --embed-model MODEL]
```

Every notebook command also takes `--embed-base-url URL --embed-model MODEL` (an OpenAI-compatible
embedding server). A notebook is pinned to its embedding model when it is created, by the first
command that opens it; later commands must give the same model, or run `rebuild --reembed` with
the new one; `abstractnote import` takes the same two flags (see
[troubleshooting.md](troubleshooting.md#this-notebooks-vectors-were-made-with-the-embedding-model-)).

`--gateway URL --token T` and `abstractnote processor ensure` are reserved for a gateway client
mode that is not available in this version (exit `3`); use the HTTP API above instead.
