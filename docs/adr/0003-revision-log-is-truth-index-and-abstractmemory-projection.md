# ADR 0003: The revision log is the truth; the index and the AbstractMemory store are projections

**Status**: Accepted  
**Date**: 2026-10-19

## Context
AbstractMemory models temporal facts with provenance and is shared with the framework, but has no entity records, aliases, queue, revisions or binary storage, and its canonicalisation lowercases terms. Interactive views need millisecond queries over entities, relations and time.

## Decision
Each processed note appends one revision with ordered ops. Revisions are append-only and are the authoritative history. The note index (SQLite) materialises entities, aliases, claims, relations, topics, tasks and insights for fast reads. Every claim is also written to an AbstractMemory `SQLiteTripleStore` as a `TripleAssertion` (owner `abstractnote`, scope `global`) with the note's time as `observed_at`, validity windows, and a journal closure on supersession. Both projections can be rebuilt from notes plus revisions. AbstractMemory is used as a library; its public surface is not changed.

## Consequences
History and "as of" queries are exact; merges and supersessions are reversible; the framework's memory tooling can read note knowledge; two stores must be kept consistent inside one revision application (one transaction per store, revision marked applied last).
