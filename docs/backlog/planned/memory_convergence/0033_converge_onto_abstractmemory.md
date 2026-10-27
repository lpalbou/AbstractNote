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
- The notebook's `kg.sqlite3` and the plane's KG store fold into one plane mind
  (`<plane>/abstractmemory/memory.sqlite3`); the gateway's plane default moves from LanceDB to that SQLite home.
- A one-time projection of existing notebooks from their revision log (cause `migration`, ids kept).
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
- ripple (`retro/integration` only): `propagation.py`, `activation.py` handed to AM 0054 (0035), never merged here.

### Phase 4: thin client
What stays: `ingest/` (capture, dates and basis, normalise, blobs), `importers/`, `flows/` (the cognition prompts
and their framing; the bookkeeping tools call the graph through the runtime facade), `tools/` (thin),
`gateway/router.py` reduced to views over `/api/gateway/memory/*` plus the capture, importer and processing
routes, `cli.py`, the web and Apple apps. Dependencies on the layer below only and no private gateway functions
(0036; a public gateway extension API replaces the five private imports).

## Dependencies
AbstractMemory 0056 (provenance, revision log, `narrow_assertion`) → 0057 (one SQLite home per plane) → 0050
(identity) → 0052 (retrieval) → ripple_wave/0001 + 0054 (ripple) → 0053 (self-organisation) → 0055 (notes/diary
layer); AbstractRuntime's memory facade (its ADR 0009) and AbstractGateway's memory routes. Each phase's consumer
PRs merge only after the releases they pin are on PyPI (root ADR-0034). Supersedes proposed 0015 (moved to
`deprecated/`).

Operator decisions phase 2 waits on (memory transition report, "Decisions needed"):
1. Which AbstractNote baseline is the migration source: the live plane notebooks are schema 4 (the
   `abstractnote-serve` worktree), `main` is schema 2 and refuses them, `retro/integration` is schema 5. The
   one-time projection reads the schema the data actually has. (Phase 1 does not bump the schema version.)
2. The gateway's plane store default (LanceDB → the SQLite home) and the import of existing plane KG data.
4. Writing the bodies of EXISTING notes into the plane's artifact store during the one-time projection (new
   captures already do it, 0037), which gives every historical statement an `artifact` source.
5. That the notebook's knowledge becomes readable by the plane's other applications once folded into the plane
   mind, and whether an `owner_id` rule is wanted.

## Expected outcomes
AbstractNote's `note.sqlite3` holds notes, attachments, links and app state only; knowledge lives in the plane's
AbstractMemory mind, every statement names its ledger sources, and the plane's other applications reach it
through the gateway; the generic modules listed above are gone.

## Current code reality
Phase 1 is on branch `feat/memory-graph-thin-client-1` (0034 completed, 0037 landed there; see each item).
Everything listed under phases 2-4 is still local: the projection writes the notebook's own `kg.sqlite3`
(`store/kg.py`), nothing reads it back but `status()` and the tests, and the note index answers every read.

## Validation
Per phase: the AbstractNote acceptance and eval suites stay green on top of AbstractMemory; the local module is
deleted (a grep proves it); a notebook created before the migration opens and answers the same recall queries
after it; every statement of a migrated notebook is grounded or listed as ungrounded with the reason.

## Metadata
- Created: 2026-10-27
- Updated: 2026-10-27 (rewritten as the phased plan of the memory transition; AbstractMemory ADR 0003)
- Status: planned
- Area: store, process, recall
