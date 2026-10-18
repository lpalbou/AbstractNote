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
{earliest, latest, turning_points: [...]}, open_loops: [...], follow_ups: [...], dossier_note_id?}`.
`response` is the answer text, so chat clients using the `abstractcode.agent.v1` contract
(`prompt` in, `output.response` out) work unchanged: `prompt` is accepted as an alias of `question`.

Run the processor now: `POST /api/gateway/automations/{processor_id}/commands {"type": "run_now"}`
(the id comes from `GET /api/gateway/note/status`).

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
| `GET /note/entities` | list or search entities | `q, kind, sort=recent|frequent|name, cursor, limit` |
| `GET /note/entities/{entity_id}` | entity card as of a moment: name, aliases, summary history, beliefs at `at`, relations, notes, open tasks | `at` |
| `GET /note/entities/{entity_id}/timeline` | claims and notes about the entity in history order, with supersessions | `since, until` |
| `GET /note/entities/{entity_id}/then-now` | the earliest and latest summaries and the turning points (cached synthesis) | |
| `GET /note/relations/{a}/{b}` | the relationship between two entities through time: claims in history order, supersessions, then-versus-now of the link | `since, until` |
| `GET /note/claims/{claim_id}` | why do I believe this: the claim, the claims it superseded or was superseded by, the notes and attachment excerpts behind it | |
| `POST /note/claims/{claim_id}/retract` | user action: retract a claim (a revision with the account as source) | `reason` |
| `POST /note/tasks/{task_id}/close` | user action: close an open loop | `note?` |
| `POST /note/entities/{entity_id}/unmerge` | user action: undo a merge | `from_id, reason` |
| `GET /note/cues` | cue cards: a few reflect-generated prompts you can answer by adding a note (a person gone quiet, a decision that aged, a task mentioned often and never closed) | `limit` |
| `GET /note/graph` | nodes and edges for the explorer | `focus (entity ids), at, depth (1..3), since, until, kinds, min_count, limit` |
| `GET /note/timeline` | activity per bucket: notes, revisions, new entities, superseded claims | `since, until, granularity=day|week|month` |
| `GET /note/day/{YYYY-MM-DD}` | what happened on a day: notes, revisions with summaries, entities touched | `tz` |
| `GET /note/revisions` | revision feed | `since, until, entity, cursor, limit` |
| `GET /note/topics` | topics with counts and date ranges | |
| `GET /note/tasks` | open loops | `status, entity, limit` |
| `GET /note/insights` | insights | `kind, acked, limit` |
| `POST /note/insights/{id}/ack` | acknowledge an insight | |
| `GET /note/queue` | queue summary and failed notes with errors | |
| `POST /note/queue/{note_id}/retry` | re-queue a failed note | |
| `POST /note/notes/{note_id}/archive` | archive a note (never delete) | |
| `GET /note/settings` and `PUT /note/settings` | per-notebook settings: filing model, answering model, reflect schedule, inline processing default | |
| `POST /note/processor/ensure` | create the processor and reflect automations if missing; returns their ids | |

Response envelopes are plain JSON objects; lists return `{items, next_cursor}`. Times are ISO
8601 UTC. Errors use the gateway's error shape.

Pinned response shapes (clients decode these strictly; absent lists mean empty):

| Endpoint | Shape |
|---|---|
| `GET /note/changes` | `{cursor, notes: [NoteRef], entities: [{entity_id, name, kind, first_seen}], superseded_claims: [{claim_id, text, superseded_by, note_id}], closed_loops: [{task_id, text, closed_at}], failed_notes: [{note_id, title, error}], insights: [Insight]}` |
| `NoteRef` | `{note_id, title, kind, occurred_at, excerpt}` |
| `Insight` | `{note_id, title, body, insight_kind, occurred_at, acked_at, evidence_note_ids, entity_ids}` |
| `GET /note/overview` | `{recent: [NoteRef], entities: [{entity_id, name, kind, note_count, last_seen}], insights: [Insight], resurfacing: [NoteRef], activity: [{day, notes, revisions}]}` |
| `GET /note/cues` | `{items: [{cue_id, text, kind, subject: {kind, id, title}}]}` (cues are insight notes; `cue_id` is the note id) |
| `GET /note/tasks` | `{items: [{task_id, note_id, text, status, due, closed_at}]}` |
| `GET /note/notes/{id}` | `{note_id, title, text, kind, occurred_at, received_at, status, topics, summary, source, location, attachments: [...], revision: {...} or null, entities: [...], claims: [...], revisits: [NoteRef]}` |
| `note-capture` output | `{response, note_id, status, revision_summary?}` |
| `note-recall` output | `{response, answer, evidence: [NoteRef], evolution, open_loops, follow_ups, dossier_note_id?}` |

Linking a new note to a subject: clients may send `input_data.context.about = {kind: insight|task|cue|entity|note, id, title}` on `note-capture`; `note_capture` records it and the revision links the note to that subject (`dcterms:references`), so answering a cue or commenting an insight is traceable.

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
| `note_capture` | `text, idempotency_key?, title?, occurred_at?, location?, source?, priority?, attachments? ([{"$artifact": id}] inside the gateway; local paths only headless), session_id?, about?` | `{note_id, status: new|duplicate, duplicate_of?, first_saved_at?, received_at, revisits: [{note_id, occurred_at}], similar: [{note_id, title, occurred_at, score}], topics: [...], attachments: [{attachment_id, sha256, mime, size, extracted_by, extracted_chars}], warnings: [...]}` |
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

`Notebook` wraps the store (`abstractnote.store.NoteStore`), the AbstractMemory projection
(`abstractnote.store.kg.ClaimStore`), the queue, the embedder, and the recall helpers. The
processing steps (`abstractnote.process`) are pure functions over these objects so that the
flows, the CLI and the tests share one implementation.

## CLI

```
abstractnote capture "text" [--file PATH ...] [--at ISO] [--notebook DIR | --gateway URL --token T]
abstractnote import claude-sessions DIR [--notebook DIR] [--since ISO] [--priority 0]
abstractnote import agora DB_PATH [--channels a,b] [--skip-system] [--notebook DIR]
abstractnote import markdown DIR [--glob '**/*.md'] [--notebook DIR]
abstractnote process [--local --provider P --model M | --gateway URL --token T] [--max-items N] [--budget-s S]
abstractnote search "query" [--effort quick|standard|deep] [--since ISO] [--until ISO]
abstractnote entity ID [--at ISO]
abstractnote day YYYY-MM-DD
abstractnote status
abstractnote bundle [--out PATH]            # pack the .flow bundle
abstractnote processor ensure --gateway URL --token T
abstractnote rebuild [--notebook DIR] [--reembed]   # replay revisions into fresh index and triple stores
abstractnote changes --since ISO            # since you last looked, as JSON (for agents and scripts)
```

`--notebook DIR` works directly on a notebook directory (headless). `--gateway URL --token T`
goes through a gateway as a client, with the token passed as a direct parameter.
