# Graph communities, their labels, and statistics

## Summary
Make a large notebook readable: find the communities of the entity graph in the background (no
model), have the model give each one a proper label in the reflect flow, and serve statistics that
show what has been accumulated (every number counted by the store).

## Why
Operator asks (2026-10-19): a background community detection over the knowledge graph to improve
readability and structure, each community analysed by the model to get a proper label; and
statistics that help understand what has been accumulated. The operator's notebook holds 2,306 live
entities and 135,226 weighted pairs: the explorer cannot show that without structure.

## Scope
- In: `src/abstractnote/recall/communities.py` and `recall/stats.py` (new), the `communities` and
  `entity_communities` tables (`store/schema.py`, kept by `rebuild`), `Notebook.communities`,
  `recompute_communities`, `communities_pending`, `label_communities`, `stats`, `community_id` on
  graph nodes and `community` on entity cards (`store/notebook.py`), the tools
  `note_communities_recompute`, `note_communities_pending`, `note_communities_label`
  (`tools/recall.py`, `tools/__init__.py`), the naming step of `note-reflect` (`flows/build.py`,
  regenerated JSON), `GET /note/communities`, `POST /note/communities/recompute`, `GET /note/stats`
  (`gateway/router.py`), tests, docs/api.md, docs/cognition.md, docs/data-model.md, `networkx>=3.2`.
- Out: the web views (lane W16 builds them on these shapes), watch, importers, `process/` (the
  naming prompt and schema therefore live in `recall/communities.py`).

## Dependencies
0006 (reflect), 0007 (gateway seam), 0015 canonical names (W15: one live entity per name).

## Expected outcomes
- Louvain (`networkx.algorithms.community.louvain_communities`, fixed seed, resolution 1.0 by
  default, configurable) over live entities; weights = statements not retracted (merged entries for
  their survivor) + co-mention weight `1/(k-1)` per note; groups under 3 entities are loose ends.
- Ids `cm_` + hash of the sorted member ids; Jaccard >= 0.6 with a previous community keeps its id,
  label, summary and kind; `stale_label` when more than 20 % changed since labelling.
- `note-reflect` ends with recompute -> pending (top entities, strongest statements, 3 notes) -> one
  `CommunityLabels` call (label <= 4 words, one sentence, kind) -> label; no call when nothing waits.
- `GET /note/stats`: totals, weekly activity over the whole span, entities by kind, top 20 entities,
  top 15 predicates, tasks, insights, communities, processing, monthly growth, quiet entities.

## Current code reality
Before this item the graph had no grouping beyond entity kinds and topics, and the only aggregate
read was `GET /note/timeline` (four series per bucket) and `GET /note/status` counts.

## Validation
`pytest -q tests` (tests/test_communities.py, test_communities_flow.py, test_gateway_communities.py,
test_stats.py; the flow/tool count tests updated). Every new check was run red first (missing module,
or a mutation of the guarded line: co-mention weight, Jaccard keep, stale rule, loose-end size,
read-only refusal, rebuild copy, canonical mapping, seed, retracted filter, empty-pending branch,
graph/card fields, word cap, stale pending, internal-statement filter, empty weeks, cumulative
growth, quiet thresholds, importer source, queued count, superseded filter) and green after.
Headless run on a copy of the operator's notebook: untracked/reports/W17.md.

## Metadata
- Created: 2026-10-19
- Status: completed
- Completed: 2026-10-19
- Area: recall, store, tools, flows, gateway router

## Completion report
- Date: 2026-10-19
- Landed: d04ce59 (detection, tables, stable ids), 40af819 (naming in note-reflect, tools,
  endpoints, graph and card fields), 28029a0 (statistics).
- Validation: pytest 329 passed (299 before); flows `--check` up to date.
- Notes and residual risks: on the operator's notebook (copy) detection takes 0.6 s and finds 21
  communities (sizes 427 to 9) and 2 loose ends; two of the five largest are mostly the entities of
  one long imported note (209 of 242 and 204 of 236 members, nearly all mentioned once), because
  every entity of a note is co-mentioned with every other one. Claims alone give 51 communities but
  450 loose ends. Weighing co-mentions down for very long notes is a tuning follow-up, not done here
  (it changes what the operator asked for). The naming prompt and schema live in
  `recall/communities.py` rather than `process/prompts` (another lane's folder). No model has named
  the real communities yet (no model runs in this lane).
