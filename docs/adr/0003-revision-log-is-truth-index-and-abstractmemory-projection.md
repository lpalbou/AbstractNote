# ADR 0003: The revision log is the truth; the index and the AbstractMemory store are projections

**Status**: Accepted, transitional. Amended 2026-10-27 (operator ruling): its direction is superseded by
AbstractMemory ADR 0002; see "Amendment" below.  
**Date**: 2026-10-19

## Context
AbstractMemory models temporal facts with provenance and is shared with the framework, but has no entity records, aliases, queue, revisions or binary storage, and its canonicalisation lowercases terms. Interactive views need millisecond queries over entities, relations and time.

## Decision
Each processed note (and each user action) appends one revision with ordered ops. Revisions are append-only and are the authoritative history. The note index (SQLite) materialises entities, aliases, claims, relations, topics, tasks and insights for fast reads. Every claim is also written to an AbstractMemory `SQLiteTripleStore` owned by the notebook as a `TripleAssertion` (owner `abstractnote`, scope `global`) with the note's time as `observed_at` and validity windows. Supersession closes the old assertion in place through `close_assertion(assertion_id, valid_until)`, an additive method contributed to AbstractMemory's stores (existing callers are unaffected; `add` stays append-only), plus a journal closure. Write order: revision row, index projection, AbstractMemory projection, applied flags; recovery replays revisions whose flags are not both set. Embeddings are computed once in the index and handed to the triple store. Both projections can be rebuilt from notes plus revisions. Agents reach note knowledge through the note tools; the notebook's triple store is not the plane's shared KG store.

## Consequences
History and "as of" queries are exact in both stores; merges and supersessions are reversible; AbstractMemory tooling can read the notebook's claims with its own contracts; a small additive change to AbstractMemory (`close_assertion`) must land and be released before AbstractNote's first release; consistency across the two stores relies on deterministic ids and the applied flags.

## Amendment (2026-10-27, operator ruling)

This ADR chose the right goals (an append-only history, exact "as of" reads, reversible merges, fast views) and
the wrong home for them. AbstractMemory is the framework's semantic temporal memory graph and owns all of its
processing (AbstractMemory ADR 0002, `abstractmemory/docs/adr/0002-abstractmemory-owns-the-memory-graph.md`).
The decision above is therefore transitional, kept only until each capability exists in AbstractMemory:

- **Context, corrected.** "AbstractMemory has no entity records, aliases, queue, revisions" described a gap to
  close in AbstractMemory, not a reason to build a second graph. The gap is now planned there
  (`abstractmemory/docs/backlog/planned/knowledge_graph_convergence/`, items 0050-0055). The queue is generic
  infrastructure for AbstractRuntime and AbstractGateway, not memory.
- **Direction.** The note index's entities, aliases, claims, relations, revisions, communities and recall
  logic converge onto AbstractMemory's public API; AbstractMemory becomes the store of record and AbstractNote
  keeps capture, importers, note-specific prompts and its views (AbstractMemory ADR 0002 decision 6).
- **Shared knowledge.** "The notebook's triple store is not the plane's shared KG store" no longer holds as a
  goal: what a person's notebook learns should be reachable by the other applications of the plane through
  AbstractMemory and the gateway, under the plane's access rules.
- **No new parallel logic.** Until the migration is done, no new generic memory-graph logic (entity
  resolution, claim revision, consolidation, communities, ripple, retrieval fusion) is added here; a missing
  capability becomes an AbstractMemory backlog item linked from this repo's backlog.
- **No private access.** The write to AbstractMemory's private table in `store/kg.py` (`store._table`,
  `store._conn`, `store._lock`, used to move a closed window earlier) is a violation to remove as soon as
  AbstractMemory ships `narrow_assertion` (backlog 0034).

Backlog: 0033 (converge onto AbstractMemory), 0034 (no private writes), 0035 (hand the ripple prototype to
AbstractMemory).
