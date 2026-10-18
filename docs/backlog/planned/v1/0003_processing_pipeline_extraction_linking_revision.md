# Processing pipeline: context, extraction, linking, revision

## Summary
Implement the pure functions behind `note_context`, `note_resolve_candidates` and `note_apply_revision`, the JSON schemas (`QuickFiling`, `Extraction`, `Linking`, `Answer`, `MiningPlan`, `SliceFindings`, `Dossier`, `Reflections`) and the prompt files, with the predicate vocabulary loaded from AbstractSemantics.

## Why
This is the cognitive core: look at existing knowledge, extract, resolve against candidates, let the model decide identity and what changed, apply an append-only revision. Design in cognition.md.

## Scope
- In: candidate generation (alias exact case-folded, FTS, vector kNN), the `Linking` decision application (reuse/create/merge, restates/refines/supersedes/contradicts, task open/close), entity summary revisions, warnings for `ex:` predicates, sectioning for large notes with merge of partial extractions, unsure -> create + `skos:closeMatch`.
- Out: no regex or keyword heuristics deciding identity (ADR 0004). The LLM calls themselves are flow nodes (0005); this item provides the prompts/schemas and a scripted-model test harness.

## Dependencies
0001, 0002.

## Expected outcomes
- Given a scripted `Extraction` + `Linking`, `note_apply_revision` produces the documented ops and the triple projection.
- Supersession closes the old claim (`valid_until`, journal closure) and keeps it readable in history.
- Merge keeps both entity records; `entity(from_id)` resolves to the merged target.

## Current code reality
Nothing yet. The runtime's `MEMORY_KG_RESOLVE` label resolver and ADR 0025 (framework) describe the candidate approach; AbstractMemory has no resolver.

## Validation
`pytest tests/test_process_*.py` with fixtures covering: new entity, alias reuse, merge, restate, supersede, contradict, task close, oversized note.

## Metadata
- Created: 2026-10-19
- Status: planned
- Area: core
