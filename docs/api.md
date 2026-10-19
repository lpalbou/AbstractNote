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

Both `note-drain` and `note-process` accept a `thinking` input (`on` or `off`): a drain defaults to
`off` (the bulk setting; the processor automation sends none, so its drains run with thinking off),
inline processing at capture uses `on`; `{"input_data": {"max_items": 50, "budget_s": 3600, "thinking": "on"}}`
starts a drain with reasoning on.

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
| `GET /note/status` | notebook health: counts, queue state, the processor and reflect automations (ids and health, see the shapes below), model settings, store versions, embedding pin | |
| `GET /note/changes` | since you last looked: new notes, new entities, superseded claims, closed loops, failed notes, insights since a cursor; the default screen for humans and the first call for agents | `since (cursor or ISO), limit, as_received` |
| `GET /note/overview` | the landing data: recent notes, active entities, unacknowledged insights, today's resurfacing, activity sparkline | `days` |
| `GET /note/notes` | list notes | `q, since, until, kind, topic, entity, status, cursor, limit` |
| `GET /note/notes/{note_id}` | a note with attachments, revision, claims, linked entities, revisits | |
| `GET /note/notes/{note_id}/attachments/{attachment_id}` | attachment bytes (streams the blob) | |
| `GET /note/search` | quick recall (full-text plus vector), grouped | `q, since, until, kinds, entities, topics, limit` |
| `GET /note/entities` | list or search entities (entries merged into another are left out unless `include_merged=true`; each item carries `merged_into`) | `q, kind, sort=recent|frequent|name, cursor, limit, include_merged` |
| `GET /note/entities/{entity_id}` | entity card as of a moment: name, aliases, summary history, beliefs at `at`, relations, notes, open tasks (a merged id answers with the surviving entity and `resolved_from`); with `as_merged=true`, the entry's own record instead, not followed to the survivor | `at, as_received, as_merged` |
| `GET /note/entities/{entity_id}/timeline` | claims and notes about the entity in history order, with supersessions | `since, until` |
| `GET /note/entities/{entity_id}/then-now` | the earliest and latest summaries and the turning points (cached synthesis) | |
| `GET /note/relations/{a}/{b}` | the relationship between two entities through time: claims in history order, supersessions, then-versus-now of the link | `since, until` |
| `GET /note/claims/{claim_id}` | why do I believe this: the claim, the claims it superseded or was superseded by, the notes and attachment excerpts behind it | |
| `POST /note/claims/{claim_id}/retract` | user action: retract a claim (a revision with the account as source) | `reason` |
| `POST /note/tasks/{task_id}/close` | user action: close an open loop | `note?` |
| `POST /note/entities/{entity_id}/unmerge` | user action: undo a merge | `from_id, reason` |
| `POST /note/entities/normalize` | user action: merge every group of live entities that share a canonical name into the earliest one, one reversible `entity.merge` revision each (source `{client, account}`); `dry_run` only lists the groups | `dry_run` |
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
| `GET /note/graph` | nodes and edges for the explorer; each node carries `community_id` | `focus (entity ids), at, as_received, depth (1..3), since, until, kinds, min_count, limit` |
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
| `GET /note/queue` | queue summary, failed notes with errors, and the processor's throughput (`processor: {max_items, interval}`) | `limit` |
| `POST /note/queue/{note_id}/retry` | re-queue a failed note | |
| `POST /note/notes/{note_id}/archive` | archive a note (never delete) | |
| `GET /note/settings` and `PUT /note/settings` | per-notebook settings: filing model, answering model, reflect schedule, processor schedule (`{every, max_items}`, default `2m` and 20; `max_items` 1 to 200), inline processing default; the automations take them when `processor/ensure` creates them | |
| `POST /note/processor/ensure` | create the processor and reflect automations if missing, repair them when unhealthy (another workflow version or tool list after an upgrade, failed, archived, gone), keep them otherwise; says what it did for each (shape below) | |

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
| `GET /note/changes` | `{cursor, notes: [NoteRef], entities: [{entity_id, name, display_name, kind, first_seen}], superseded_claims: [{claim_id, kind, text, superseded_by, note_id}], closed_loops: [{task_id, text, closed_at}], failed_notes: [{note_id, title, error}], insights: [Insight]}` |
| `superseded_claims[]` | `kind` is `superseded` (a later note replaced the claim; `superseded_by` is the new claim's id) or `retracted` (withdrawn by a note or by you; `superseded_by` is null); `text` is the claim in plain words built from entity names and the predicate's label ("dana works at acme"), never ids or vocabulary terms |
| `NoteRef` | `{note_id, title, kind, occurred_at, excerpt}` |
| `Insight` | `{note_id, title, body, insight_kind, occurred_at, acked_at, evidence_note_ids, entity_ids}` |
| `GET /note/overview` | `{recent: [NoteRef], entities: [{entity_id, name, display_name, kind, note_count, last_seen}], insights: [Insight], resurfacing: [NoteRef], activity: [{day, notes, revisions}]}` |
| `GET /note/cues` | `{items: [{cue_id, text, kind, subject: {kind, id, title}, evidence_note_ids, entity_ids}]}` (cues are insight notes; `cue_id` is the note id; `evidence_note_ids` and `entity_ids` are the insight's own: the notes it rests on and the entities it is about) |
| `GET /note/tasks` | `{items: [{task_id, note_id, text, status, due, closed_at, age_days, stale}]}` (`age_days`: whole days since the task opened, until it closed for a closed one; `stale`: still open after 60 days, `STALE_DAYS` in `abstractnote.recall.reflect`) |
| `GET /note/notes/{id}` | `{note_id, title, text, kind, occurred_at, received_at, status, topics, summary, source, location, attachments: [...], revision: {...} or null, entities: [...], claims: [...], revisits: [NoteRef], insight: {kind, evidence_note_ids, entity_ids, acked_at} or null}` (`insight` is set on an insight note, `kind` being its `insight_kind`, and null on every other note) |
| `GET /note/review` | `{items: [{review_id, kind, why, reason, at, sides: [ReviewSide], evidence_note_ids, actions: [{action, method, path, body}]}], next_cursor}` (newest first; `kind` `close_match`, `merge` or `contradiction`; `why` the rule; `reason` the processor's stated reason for a merge, else null; `at` when the notebook recorded it; `evidence_note_ids` starts with the note that raised it; `next_cursor` is always null) |
| `ReviewSide` | `{type, id, name, kind, note_ids}` (`type` `entity`: `name` its canonical name, `kind` its entity kind, `note_ids` its newest notes; `type` `claim`: `name` the statement in plain words, `kind` null, `note_ids` the note it came from) |
| `POST /note/review/{id}/confirm` | the revision result (`{revision_id, summary, entities_merged, ...}`) plus `review_id` |
| `GET /note/entities/{id}?as_merged=true` | `{entity_id, kind, name, display_name, aliases, summary, first_seen, last_seen, note_count, merged_into, merged_at, merge: {revision_id, at, recorded_at, reason, summary, note_id} or null, notes: [NoteRef]}` (the entry's own name, aliases, summary and notes; `merge` is the revision that joined it into `merged_into`, null when it is not merged; `at` is refused with 400) |
| `POST /note/entities/normalize` | `{dry_run, groups: [{into_id, from_ids, name, names, kinds}], merges: [{from_id, into_id, revision_id}]}` (`name` the shared canonical name, `names` and `kinds` each entry's surface form and kind, the surviving entry first; `merges` is empty on a dry run; a notebook opened without its embedding model answers the dry run and refuses the merge with 400) |
| entity `name`, `display_name` | everywhere an entity appears (lists, cards, graph nodes, overview, changes), `name` is its one canonical name (lower case, whitespace and punctuation as one `_`: `laurent_philippe_albou`) and is what a client shows; `display_name` is the first way a note wrote it (`Laurent-Philippe Albou`); `aliases` list other names once per canonical name |
| `GET /note/queue` | `{stats: {queued, processing, failed, by_priority, oldest_enqueued_at}, failed: [{note_id, title, occurred_at, last_error, failed_at, failures}], waiting: [{note_id, title, occurred_at, priority, state, attempts, next_attempt_at}], automations: Automations, processor: {max_items, interval}}` (`processor` is what the processor automation was created with while it exists, otherwise the `processor_schedule` setting it will be created with; `interval` is a duration such as `2m`) |
| `Automations` (`GET /note/status`, `GET /note/queue`) | `{processor_id, reflect_id, processor: AutomationHealth, reflect: AutomationHealth}` |
| `AutomationHealth` | `{id, state, target, healthy, reason, paused}`: `state` is the automation's state (`active`, `paused`, `failed`, `completed`, `archived`) or `missing` (never created, or gone); `target` the workflow it runs (`abstractnote@<version>:note-drain`); `healthy` is true only when it is active or paused AND runs the version of the note workflows this gateway serves AND its run input allows exactly the current note tools (`abstractnote.tools.TOOL_NAMES`; otherwise `reason` is "The tool list changed."); `reason` one sentence when it is not (null otherwise); `paused` whether the user paused it. Unhealthy = `POST /note/processor/ensure` repairs it |
| `POST /note/processor/ensure` | `{processor_id, reflect_id, created, processor: EnsureAction, reflect: EnsureAction}`; `EnsureAction` = `{id, action, reason, previous_id?}`: `action` `created` (there was none), `kept` (healthy) or `recreated` (the previous one, `previous_id`, was archived and replaced; `reason` is its health reason); `created` lists the flows of every new automation (`[]` when both were kept). `503` when the note workflows are not loaded: nothing is archived |
| `GET /note/communities` | `{computed_at, resolution, revision_count_at_compute, revisions_since, loose_ends, items: [Community]}` (`computed_at` null, `items` empty before the first computation; `revisions_since` counts revisions applied after it, so a client can say the groups are being refreshed; `loose_ends` counts the live entities in no community) |
| `Community` | `{community_id, label, summary, kind, size, top_entities: [{entity_id, name, kind, note_count}], stale_label, labelled_at, created_at}`, largest first: `label` (at most 4 words), `summary` (one sentence) and `kind` (`project`, `theme`, `people`, `tooling`, `process` or `other`) are null until the reflect flow names it; `top_entities` are its 5 members with the most notes; `stale_label` true when the label is kept but more than 20 % of the members changed since it was given (the next reflect run names it again) |
| `POST /note/communities/recompute` | `{communities, loose_ends, entities, edges, new, labels_kept, stale_labels, unlabelled, resolution, computed_at}` (`new`: communities that matched no previous one; `labels_kept`: labelled communities that kept their id; a resolution that is not a number above 0 and at most 100 is 400; a notebook opened without its embedding model is 400) |
| graph node `community_id`, entity card `community` | every `GET /note/graph` node carries `community_id` (null for an entity in no community, or before the first computation); `GET /note/entities/{id}` carries `community: {community_id, label, kind, size, stale_label}` or null. Both are the communities as last computed, also with `at` or `as_received` |
| `GET /note/stats` | `{generated_at, span: {first_note_at, last_note_at}, totals, activity_weekly: [WeekActivity], entities_by_kind: {kind: n}, top_entities: [{entity_id, name, display_name, kind, note_count, first_seen, last_seen}], claims_by_predicate: [{predicate, words, count, active}], tasks: {open, closed, done, dropped}, insights_by_kind: {insight_kind: n}, communities, processing: {understood, queued, processing, failed, duplicate}, growth_monthly: [{month, entities, claims, new_entities, new_claims}], quiet_entities: [{entity_id, name, kind, note_count, last_seen, days_silent}]}`. Notes are the ones you sent (not insights, not archived). `totals` = `{notes, notes_archived, insights, attachments, attachment_bytes, entities, entities_merged, claims, claims_active, revisions, topics, notes_by_kind: {kind or "unfiled": n}, notes_by_source: {client or "importer:<name>": n}, attachments_by_mime: {mime: n}}`. `top_entities`: the 20 live entities with the most notes. `claims_by_predicate`: the 15 most used predicates (`words` its plain words, `active` how many are still current), ties by predicate. `processing.queued` includes notes not filed yet. `communities` counts the stored communities. Maps are ordered by count |
| `WeekActivity` | `{week, notes, revisions, new_entities, claims_added, claims_superseded}`: `week` is the Monday (`YYYY-MM-DD`); in history time (a note's date, a revision's `at`, an entity's creation, a statement's `observed_at`, a supersession's end), every week from the earliest to the latest one, empty weeks included |
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
| `note_capture` | `text, idempotency_key?, title?, occurred_at?, location?, source?, priority?, attachments? ([{"$artifact": id}] inside the gateway; [{"path": ...}] only headless, inside the folder given to bind as allow_paths_under), session_id?, about?, answer? ({question, evidence_note_ids})` | `{note_id, status: new|duplicate, duplicate_of?, first_saved_at?, received_at, revisits: [{note_id, occurred_at}], similar: [{note_id, title, occurred_at, score}], topics: [...], attachments: [{attachment_id, sha256, mime, size, extracted_by, extracted_chars}], warnings: [...]}` |
| `note_file` | `note_id, title, kind, topics, urgency, language, one_line` | `{note_id, priority, status}` |
| `note_context` | `note_id, max_chars?` | `{note: {...}, sections: [...], similar_notes: [...], candidate_entities: [{entity_id, name, kind, aliases, summary, last_seen, active_claims: [...]}], open_tasks: [...], topics: [...], vocabulary: [{predicate, gloss}]}` |
| `note_resolve_candidates` | `note_id, mentions: [{ref, name, kind, aliases}]` | `{mentions: [{ref, candidates: [{entity_id, name, kind, aliases, summary, last_seen, score, matched_by}]}]}` |
| `note_apply_revision` | `note_id, extraction, linking` | `{revision_id, summary, entities_created, entities_merged, claims_added, claims_reinforced, claims_superseded, tasks_opened, tasks_closed, warnings}`; references outside the context packet are rejected into `create`/`new` with a warning |
| `note_queue_lease` | `worker_id, max_items=1` | `{items: [{note_id, priority, attempts}]}` |
| `note_queue_ack` | `note_id, outcome: ok|error, error?, revision_id?` | `{note_id, status, next_attempt_at?}` |
| `note_queue_stats` | | `{queued, processing, failed, by_priority, oldest_enqueued_at}` |
| `note_search` | `query, since?, until?, kinds?, entities?, topics?, limit=20, as_of?, order? (relevance or earliest)` | `{query, order, notes: [...], entities: [...], claims: [...], facets: {...}, considered, warnings}`: each note carries `score` (rank fusion), `relevance` (0 to 1), `matched_entities` and `matched_claims`; an entity or statement is a hit only when its relevance reaches 0.5 (`HIT_MIN_SCORE`) and a returned note carries it, and lists those `note_ids` (an entity also `first_at` and `latest_at`: the first and latest date among the matching notes that carry it); `order=earliest` lists the notes whose relevance reaches 0.75 (`EARLIEST_MIN_SCORE`) oldest first ([remembering.md](remembering.md#quick-relevance)) |
| `note_expand` | `note_ids, entity_ids, as_of?, query?, budget_chars?` | the standard recall packet, chosen within `budget_chars` (default 24000, `EXPAND_BUDGET_CHARS`, measured as JSON): `{as_of, budget_chars, used_chars, notes: [NoteRef with an excerpt around the match of query, else of the focus entities' names, and summary?], entities: [{entity_id, line, focus}], claims_timeline: [{entity_id, claims: [{claim_id, at, text, status, note_id, closed?}]}], relation_timelines: [{a, b, claims}], neighbours: [{entity_id, name, kind, count, via}], then_now: [{entity_id, earliest, latest, turning_points}], omitted: {notes, entities, claims}}`; the given `entity_ids` are the focus: their one-line cards come first, then the notes in the order given (best first, up to 60 % of the budget), the other entities those notes mention as one line each, and for the focus entities only the claim timelines (earliest and latest kept), then-versus-now, relations and neighbours; what is left goes to further notes |
| `note_changes` | `since, limit` | the since-you-last-looked delta (same shape as `GET /note/changes`) |
| `note_entity` | `entity_id, at?` | the entity card |
| `note_timeline` | `since, until, granularity` | buckets |
| `note_open_loops`, `note_contradictions`, `note_resurface`, `note_anniversaries` | `limit, since?` | candidate lists for the reflect flow (`{items}`, each with its `rule`, `note_ids` and `entity_ids`; an open loop also carries `age_days` and `stale`, as `GET /note/tasks` does; contradictions are only the disputed pairs a note's linking marked) |
| `note_insight_write` | `kind, title, body, evidence_note_ids, entity_ids, run_id?` | `{note_id}` |
| `note_communities_recompute` | `resolution?` | the counts of `POST /note/communities/recompute` |
| `note_communities_pending` | `limit=5` (at most 20) | `{count, items: [{index, community_id, size, stale_label, previous_label, previous_summary, top_entities: [{entity_id, name, kind, note_count, summary}], claims: [{claim_id, text}], excerpts: [NoteRef]}]}`: communities without a label or with a stale one, largest first; `claims` are up to 8 active statements among the members (links between members first, then the most reinforced and the most certain), `excerpts` the 3 notes that mention the most members |
| `note_communities_label` | `items: [{community_id, label, summary, kind}]` | `{labelled, warnings}`: a label of more than 4 words keeps its first 4, an unknown kind becomes `other`, an unknown community or an empty label is left out, each with a warning; labelling clears `stale_label` |

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
abstractnote search "query" --notebook DIR [--effort quick|standard|deep] [--since ISO] [--until ISO] [--as-of ISO]
                    [--limit N] [--order relevance|earliest]      # earliest: matching notes oldest first (quick)
                    [--provider P --model M]                      # a model for standard and deep
abstractnote entity ID --notebook DIR [--at ISO]
abstractnote day YYYY-MM-DD --notebook DIR [--tz ZONE]
abstractnote status --notebook DIR
abstractnote changes --notebook DIR [--since CURSOR_OR_ISO] [--limit N]
abstractnote process --local --notebook DIR --provider P --model M [--max-items 20] [--budget-s 90]
abstractnote recover --notebook DIR                              # finish revisions a stopped process left half applied
abstractnote rebuild --notebook DIR [--reembed]                  # replay revisions into fresh stores
abstractnote normalize --notebook DIR [--dry-run] [--account NAME]  # merge entities that share a canonical name
abstractnote bundle [--out DIR]                                  # pack abstractnote@<version>.flow

abstractnote import claude-sessions DIR --notebook DIR [--exclude-session ID ...] [--author NAME]
abstractnote import agora DB_PATH --notebook DIR [--include-channels a,b] [--exclude-channels a,b]   # --channels = --include-channels
                    [--kinds decision,claim,work,thread,operator] [--include-retracted] [--include-system] [--operators a,b]
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
