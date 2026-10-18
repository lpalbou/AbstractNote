# Notebook store and data model

## Summary
Implement `abstractnote.model` (Note, Attachment, Entity, Claim, Relation, Revision, QueueItem, Task, Topic, Insight), the SQLite note index (`abstractnote.store.schema`, `NoteStore`), the AbstractMemory projection (`abstractnote.store.kg.ClaimStore` over `SQLiteTripleStore` + `SQLiteJournal`), deterministic ids and hashing (`abstractnote.ids`), embeddings adapter, and `rebuild` (replay revisions into fresh stores).

## Why
Everything else (capture, processing, recall, the read API, the clients) depends on one faithful, rebuildable store with bitemporal semantics. The design is in [docs/data-model.md](../../../data-model.md).

## Scope
- In: tables and indexes listed in data-model.md; FTS5 over notes/entities/claims; vector columns with cosine search; migrations with a schema_version; `Notebook.open(dir)`; transaction per revision; projection rules to TripleAssertion; journal supersession; rebuild from notes + revisions; unit tests with an in-memory embedder.
- Out: model calls (0003), HTTP (0007), importers (0010).

## Dependencies
abstractmemory>=0.3.0 (library use only; no changes to its public surface), abstractsemantics registry for predicate validation.

## Expected outcomes
- `Notebook.open(tmp)` creates `note.sqlite3`, `kg.sqlite3`, `blobs/`, `extracted/`.
- Applying a revision updates entities/claims/relations/topics/tasks and writes matching assertions; `TripleQuery(active_at=...)` and the index's `beliefs_at` agree on a fixture.
- `rebuild` on a notebook with 1k notes reproduces identical entity/claim counts and ids.

## Current code reality
Repository bootstrapped on 2026-10-19 with only the skeleton. AbstractMemory offers TripleAssertion/TripleQuery/SQLiteTripleStore/SQLiteJournal (see untracked/recon/abstractmemory.md): no entity table, no resolver, no queue, nothing writes valid_from/valid_until.

## Validation
`pytest tests/test_store*.py tests/test_kg_projection.py tests/test_rebuild.py`; a property test that `graph(as_of=T)` never contains an entity created after T.

## Metadata
- Created: 2026-10-19
- Status: planned
- Area: core
