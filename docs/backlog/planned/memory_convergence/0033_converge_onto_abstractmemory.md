# Converge the notebook's knowledge graph onto AbstractMemory

## Summary
Replace, capability by capability, AbstractNote's parallel knowledge graph (entities, aliases, claims,
relations, revisions, retrieval, communities, consolidation, reflection, review) with AbstractMemory's, as each
capability lands there, and delete the local copy. AbstractNote keeps capture, importers, note-specific prompts,
the flows that frame them, and its views.

## Why
Operator ruling 2026-10-27 (AbstractMemory ADR 0002; this repo's ADR 0003 amended): about half of this package
is generic memory-graph logic no other application can use. AbstractEntity (persistent agents that keep diaries
and notes to self), assistants and agents need the same graph. Stronger designs built here are generalised into
AbstractMemory first, then used from here.

## Scope
- In: for each capability, switch reads and writes to AbstractMemory's public API, migrate existing notebooks
  (a one-time projection from the revision log, which stays replayable), delete the local module and its private
  tests once AbstractMemory's parity tests pass.
- Out: building the capability in AbstractMemory (its items 0050-0055); capture, importers, prompt wording, web
  and Apple views; the queue (AbstractRuntime/AbstractGateway, see 0036 and proposed 0020).

## Dependencies
AbstractMemory 0051 (revision log, `narrow_assertion`) → 0050 (identity) → 0052 (retrieval) → ripple_wave/0001
+ 0054 (ripple) → 0053 (self-organisation) → 0055 (notes/diary layer). Each step here is blocked on its
AbstractMemory item. Supersedes proposed 0015 (moved to `deprecated/`).

## Expected outcomes
AbstractNote's `note.sqlite3` holds notes, attachments, links and app state only; knowledge lives in
AbstractMemory and is reachable by the plane's other applications; the generic modules listed below are gone.

## Current code reality
`main` (7d65353, 2026-10-27). Generic memory-graph logic to migrate, with its AbstractMemory target:
- identity: `store/names.py`, `ids.py` (name keys), `process/resolve.py`, the merge/alias parts of
  `process/revise.py`, `recall/review.py` → AM 0050;
- claims and revisions: `store/notes.py` (`NoteStore.project`), `store/notebook.py` (revision log, two-store
  write order), `store/schema.py`, `store/kg.py` (the projection) → AM 0051;
- retrieval: `recall/search.py`, `recall/expand.py`, `recall/then_now.py`, `recall/changes.py`,
  `store/archive.py`, `store/embeddings.py` (`VectorCache`), `store/vector_index.py` → AM 0052;
- self-organisation: `recall/communities.py`, `store/topics.py`, `recall/consolidation.py`, `recall/reflect.py`,
  `recall/insights.py` → AM 0053;
- ripple (`retro/integration` only): see 0035 → AM 0054.
Stays here: `ingest/`, `importers/`, `flows/` (note-specific framing), `tools/` (note tools, calling
AbstractMemory), `gateway/` (routes, per 0036), `cli.py`, the web and Apple apps.

## Validation
Per capability: the AbstractNote acceptance and eval suites stay green on top of AbstractMemory; the local
module is deleted (a grep proves it); a notebook created before the migration opens and answers the same
recall queries after it.

## Metadata
- Created: 2026-10-27
- Status: planned
- Area: store, process, recall
