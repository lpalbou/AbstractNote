# ADR 0003: The revision log is the truth; the index and the AbstractMemory store are projections

**Status**: Accepted  
**Date**: 2026-10-19

## Context
AbstractMemory models temporal facts with provenance and is shared with the framework, but has no entity records, aliases, queue, revisions or binary storage, and its canonicalisation lowercases terms. Interactive views need millisecond queries over entities, relations and time.

## Decision
Each processed note (and each user action) appends one revision with ordered ops. Revisions are append-only and are the authoritative history. The note index (SQLite) materialises entities, aliases, claims, relations, topics, tasks and insights for fast reads. Every claim is also written to an AbstractMemory `SQLiteTripleStore` owned by the notebook as a `TripleAssertion` (owner `abstractnote`, scope `global`) with the note's time as `observed_at` and validity windows. Supersession closes the old assertion in place through `close_assertion(assertion_id, valid_until)`, an additive method contributed to AbstractMemory's stores (existing callers are unaffected; `add` stays append-only), plus a journal closure. Write order: revision row, index projection, AbstractMemory projection, applied flags; recovery replays revisions whose flags are not both set. Embeddings are computed once in the index and handed to the triple store. Both projections can be rebuilt from notes plus revisions. Agents reach note knowledge through the note tools; the notebook's triple store is not the plane's shared KG store.

## Consequences
History and "as of" queries are exact in both stores; merges and supersessions are reversible; AbstractMemory tooling can read the notebook's claims with its own contracts; a small additive change to AbstractMemory (`close_assertion`) must land and be released before AbstractNote's first release; consistency across the two stores relies on deterministic ids and the applied flags.
