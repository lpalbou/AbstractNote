# Recall tiers, then-versus-now and reflect

## Summary
Implement quick recall (`note_search`: FTS + vector over notes/entities/claims with grouping and facets), `note_expand` (entity cards as of a moment, claim timelines, neighbours, then-versus-now pairs), `note_entity`, `note_timeline`, `day`, the then-now cache, and the reflect candidate tools (`note_open_loops`, `note_contradictions`, `note_resurface`, `note_anniversaries`) plus `note_insight_write`.

## Why
The second half of AbstractNote is searchability by cue and effort, for humans and agents. Design in remembering.md.

## Scope
- In: ranking (BM25 + cosine fusion, recency boost optional and explicit), `as_of` filters everywhere, cached then-now per entity revision, insight notes with provenance.
- Out: the model synthesis (flow nodes, 0005), UI (0008).

## Dependencies
0001, 0003.

## Expected outcomes
- Quick search over 5k notes answers under 300 ms on an M-series laptop.
- `entity(at=T)` returns only beliefs valid at T; `then_now` lists turning points with note ids.
- Reflect candidates are deterministic and explainable (each carries the rule that produced it).

## Current code reality
Nothing yet; AbstractMemory's FTS5 and vector query exist on `SQLiteTripleStore` and are reused for claims.

## Validation
`pytest tests/test_recall.py tests/test_reflect_candidates.py` with a seeded notebook.

## Metadata
- Created: 2026-10-19
- Status: planned
- Area: core
