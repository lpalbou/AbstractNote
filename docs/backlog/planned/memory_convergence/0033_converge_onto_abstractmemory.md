# Converge the notebook's knowledge graph onto AbstractMemory

## Summary
Replace, phase by phase, AbstractNote's parallel knowledge graph (entities, aliases, claims, relations,
revisions, retrieval, communities, consolidation, reflection, review) with AbstractMemory's, as each capability
lands there, and delete the local copy. The notebook's graph converges into the plane's one mind (AbstractMemory
ADR 0003). AbstractNote keeps capture, importers, note-specific prompts, the flows that frame them, and its views:
it becomes a thin client of the gateway again.

## Why
Operator ruling 2026-10-27 (AbstractMemory ADR 0002; this repo's ADR 0003 amended): about half of this package
is generic memory-graph logic no other application can use. AbstractEntity (persistent agents that keep diaries
and notes to self), assistants and agents need the same graph. AbstractMemory ADR 0003 sets the target: one
graph, several minds (a plane is one mind), a revision as the unit of change, mandatory typed ledger provenance,
three named time axes. Stronger designs built here are generalised into AbstractMemory first, then used from here.

## Scope
- In: the four phases below, each a set of PRs; for each capability, switch reads and writes to AbstractMemory's
  public surface (through the gateway for this package's server side), migrate existing notebooks (a one-time
  projection from the revision log, which stays replayable), delete the local module and its private tests once
  AbstractMemory's parity tests (ported from this package's tests) pass.
- Out: building the capability in AbstractMemory (its items 0050-0058); capture, importers, prompt wording, web
  and Apple views; the queue (AbstractRuntime/AbstractGateway, see 0036 and proposed 0020).

### Phase 1: foundations (2026-10-27, branch `feat/memory-graph-thin-client-1`)
- 0034: no write to AbstractMemory's private table; `narrow_assertion` (completed).
- 0037: ledger provenance on every revision, projected assertion and closure; the note body as an artifact of
  the capture run; the tools read AbstractRuntime's step context.
- ADRs 0001, 0003 and 0008 amended with AbstractMemory ADR 0003; this plan.
- Alongside (other packages): AbstractMemory `abstractmemory.graph` (SourceRef, Provenance, Revision,
  MemoryGraph, history, change feed, replay; its 0056) and performance (0058); AbstractRuntime
  `LedgerStore.get_record(run_id, seq)`, the step context, the memory facade (its ADR 0009, item 0853);
  AbstractGateway `/api/gateway/memory/*` over the facade.

### Phase 2: the revision log moves (AbstractMemory 0051 remainder + 0057)
- `NoteStore.project` becomes a client of `MemoryGraph.apply_revision` through the gateway: its ops expressed
  as graph ops (`assert`, `close` with kind and successor, `narrow`; entity and alias ops arrive with AbstractMemory
  0050); the provenance 0037 writes is already in the shape the graph requires.
- The notebook's `kg.sqlite3` and the plane's KG store fold into one plane mind (`<plane>/abstractmemory/`),
  the notebook's knowledge under its owner's `owner_id`; the home is the SQLite connector behind `MemoryGraph`
  (decision 2, taken).
- A one-time projection of existing notebooks from their revision log (cause `migration`, ids kept). It also
  adopts the assertions an existing `kg.sqlite3` holds with the earlier flat provenance (or, after a
  `rebuild()`, with empty `sources`), naming the import/migration revision that grounds them.
- Person corrections from the web (retract, redate, unmerge, confirm a match, close a task; `gateway/router.py`
  writes the notebook directly through `Notebook._user_revision`, outside any runtime step) carry no ledger
  address and no note, so their assertions and closures are ungrounded. Before the graph can accept them they
  become gateway runs (each correction a run, its tool step the source) or `person`-cause revisions with an
  `event`/`message` source. The same holds for captures and imports outside a runtime without an artifact store
  (the CLI, importers): their ground is decided here (an import run, or the imported file as an artifact).
- AbstractNote deletes `store/kg.py` and the projection parts of `store/notebook.py`.

### Phase 3: capabilities (AbstractMemory 0050 identity, 0052 reads, 0053 self-organisation, 0054 ripple, 0055 notes/diary layer)
Each lands in AbstractMemory with this package's tests ported as parity tests; AbstractNote then deletes the
local module:
- identity: `store/names.py`, `ids.py` (name keys), `process/resolve.py` (candidate lookup), the merge/alias parts
  of `process/revise.py`, `recall/review.py` → AM 0050;
- retrieval: `recall/search.py`, `recall/expand.py`, `recall/then_now.py`, `recall/changes.py`,
  `store/archive.py`, `store/embeddings.py` (`VectorCache`), `store/vector_index.py` → AM 0052;
- self-organisation: `recall/communities.py`, `store/topics.py`, `recall/consolidation.py`, `recall/reflect.py`,
  `recall/insights.py` → AM 0053 (and the runtime's `identity/communities.py` with them);
- ripple: `recall/propagation.py` (in the package since the schema-4 baseline merge, operator decision 1) and
  `recall/activation.py` (on `retro/integration` only) handed to AM 0054 (0035), then removed from the package;
  until then the merged code is a prototype kept working, not extended.

### Phase 4: thin client
What stays: `ingest/` (capture, dates and basis, normalise, blobs), `importers/`, `flows/` (the cognition prompts
and their framing; the bookkeeping tools call the graph through the runtime facade), `tools/` (thin),
`gateway/router.py` reduced to views over `/api/gateway/memory/*` plus the capture, importer and processing
routes, `cli.py`, the web and Apple apps. Dependencies on the layer below only and no private gateway functions
(0036; a public gateway extension API replaces the five private imports).

## Dependencies
AbstractMemory 0056 (provenance, revision log, `narrow_assertion`) → 0057 (one home per plane; store pending) → 0050
(identity) → 0052 (retrieval) → ripple_wave/0001 + 0054 (ripple) → 0053 (self-organisation) → 0055 (notes/diary
layer); AbstractRuntime's memory facade (its ADR 0009) and AbstractGateway's memory routes. Each phase's consumer
PRs merge only after the releases they pin are on PyPI (root ADR-0034). Supersedes proposed 0015 (moved to
`deprecated/`).

Operator decisions (memory transition report, "Decisions needed"):
1. TAKEN 2026-10-27: the AbstractNote baseline is the serve worktree's schema-4 line (cd6227c) merged into `main`
   (branch `feat/memory-graph-note-baseline`); phase 1 rides on it at schema 4 (0037 adds nullable columns, no
   version change). The one-time projection reads schema 4. That line brought R6's `recall/propagation.py` into
   the package (see 0035).
2. TAKEN 2026-10-27 evening: `MemoryGraph` is the only manipulation surface; SQLite is the default connector for
   the plane store, LanceDB a connector behind the same conformance suite; existing plane KG data enters through
   `import` revisions (AbstractMemory ADR 0003 decision 3, its 0057).
4. Writing the bodies of EXISTING notes into the plane's artifact store during the one-time projection (new
   captures already do it, 0037), which gives every historical statement an `artifact` source.
5. TAKEN 2026-10-27: visibility is strict ownership (AbstractMemory ADR 0003 decision 2, amended): the notebook's
   knowledge is readable by its owner's other applications and by nobody else by default; the plane is not a
   sharing scope; every read surface filters by `owner_id`.

## Phase 2 open decisions (verbatim from the memory transition report, with the lead's recommendations)

Source: `untracked/memory-transition/report.md` in the AbstractFramework root ("Decisions needed" 3, 4, 8; §6
"Adversarial architect pass" 10, 12, 13, 14). Copied verbatim on 2026-10-28; the report stays the record. Status
of the others: 1 and 5 TAKEN (above); 2 TAKEN 2026-10-27 evening (`MemoryGraph` is the only manipulation surface,
SQLite the default connector, LanceDB a connector behind the same conformance suite; AbstractMemory ADR 0003,
0057); 11 SETTLED by AbstractRuntime 0.14.0 (persisted `seq`); 6, 7, 9 not phase-2 blockers. Phase 2 is blocked
on 4 and 8 (the projection's ground); 10 changes the `owner_id` the projection writes, so it must be decided
before the projection runs. AbstractMemory owns 10, 13, 14; AbstractRuntime 3; AbstractGateway/AbstractEntity 12.

3. **Synthetic run ids on the in-process entity path.** `ChatSession` and the 24/7 life loop write memory under
   `run_id="chat-<session>"` with no run or ledger row behind it; only the verbatim artifact (`payload_ref`) and the
   diary entry ground those memories. Options: (a) accept artifact + diary refs as the ground for that path (the
   design allows it: a synthetic id is never stamped as a run/step ref), or (b) make the home-direct chat a real run
   in the entity's `runtime_<slug>.sqlite3` so every episode has a `step` ref. Recommendation: (b) in a later runtime
   slice; (a) is what phase 1 ships.

4. **Note bodies as ledger artifacts for EXISTING notes.** New captures store the body as an artifact of the capture
   run (phase 1). The 4k-note live notebooks have bodies only in `note.sqlite3` (+123 MB of blobs); writing them into
   the plane's artifact store during the one-time projection is feasible and gives every historical claim an
   `artifact` ref, but it is a user-data migration. Recommendation: do it in the same one-time projection as the
   schema-4 baseline (decision 1).

8. **Grounding notes captured outside a run.** CLI captures and file importers (Markdown, Claude sessions, agora)
   produce notes with no ledger source and no artifact. Under ADR 0003 the phase-2 projection refuses them
   (`ProvenanceRequired`). Options: (a) importers run as an `import` run through the gateway so each imported file is
   an artifact of that run (recommended; also gives the one-time projection of existing notebooks its ground), (b) a
   `migration`-cause revision with the imported file as an artifact written directly into the plane's artifact store.
   Same question for PERSON CORRECTIONS from the web UI (retract, redate, unmerge, confirm, task close: review finding
   N3): they are not runs today; phase 2 needs them to become gateway runs (recommended: every correction is a
   `person`-cause revision applied by a MEMORY_REVISE step of a tiny correction workflow, so the step is the source) or
   to carry an `event`/`message` source.

10. **Owner id = principal, scope = kind (architect B1/B2; must-change).** Today `owner_id` is `session_memory_<sid>`,
    `global_memory`, a run id or `abstractnote`: strings a caller can name, so no surface above the graph can enforce
    decision 5 (a flow wrote into another session's partition; `global_memory` is plane-shared by construction).
    Proposed: rows owned by `user:<tenant>/<id>` or `entity:<slug>`; `scope` ∈ {run, session:<sid>, global, notebook,
    self, diary, life, ...} under that owner; the owner of a write is derived from the run's principal (`run.actor_id`),
    never a pin; plane-shared memory, if wanted, is an explicit owner with a policy. Cost: an `import` revision rewriting
    `owner_id` on existing planes (ids kept); the KG lane's `resolve_scope_owner_id` becomes `resolve_owner(principal)` +
    scope; AbstractNote's `owner_id="abstractnote"` becomes the person's id. Recommendation: approve; it is the only
    shape under which decision 5 is enforceable by one column. Interim (applied now): a payload `owner_id` must be one of
    the run's own derived owners.

12. **Entity minds over `/memory/*` and entity ownership records (architect B4).** Delegation rests on `created_by`
    strings; legacy manifests (no `created_by`) are admin-only so the person running an old entity cannot read its
    graph; `/memory/*` cannot address an entity home. Proposed: `?mind=entity:<slug>` behind `require_entity_visible`,
    a `created_by` backfill and a transfer operation. Recommendation: approve as the AbstractEntity/gateway follow-up.

13. **A mind carries its own ledger id (architect A8).** Entity home refs name the HOST's catalog runtime id, so a home
    copied to another machine answers `unsupported` for its own sources. Proposed: the home persists its ledger id
    (manifest) and `open_mind` reads it. Recommendation: approve (small).

14. **Refuse, not degrade, on a 0.4.0 home read by 0.3.x (architect C5).** 0.3.2 silently returns 0 vector hits.
    Proposed: a 0.3.3 patch release whose store raises when it sees `vector_encoding=float32-blob`. Recommendation:
    approve (one line; makes decision 9's one-way migration loud).

## Expected outcomes
AbstractNote's `note.sqlite3` holds notes, attachments, links and app state only; knowledge lives in the plane's
AbstractMemory mind under its owner, every statement names its ledger sources, and the owner's other applications
reach it through the gateway; the generic modules listed above are gone.

## Current code reality
Phase 1 is on branch `feat/memory-graph-thin-client-1` (0034 completed, 0037 landed there; see each item), merged
on top of the schema-4 baseline (`feat/memory-graph-note-baseline`).
Everything listed under phases 2-4 is still local: the projection writes the notebook's own `kg.sqlite3`
(`store/kg.py`), nothing reads it back but `status()` and the tests, and the note index answers every read.

## Validation
Per phase: the AbstractNote acceptance and eval suites stay green on top of AbstractMemory; the local module is
deleted (a grep proves it); a notebook created before the migration opens and answers the same recall queries
after it; every statement of a migrated notebook is grounded or listed as ungrounded with the reason.

## Metadata
- Created: 2026-10-27
- Updated: 2026-10-27 (rewritten as the phased plan of the memory transition; AbstractMemory ADR 0003)
- Updated: 2026-10-28 (phase 2 open decisions 3, 4, 8, 10, 12, 13, 14 carried verbatim; decision 2 recorded as taken)
- Status: planned
- Area: store, process, recall
