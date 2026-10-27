# Upstream entity resolution to AbstractMemory

## Idea
Once stable, move candidate generation and the alias layer into an `abstractmemory.entity_resolution` module (framework ADR 0025 v1) so entities and agents share it. Must not change AbstractMemory's existing public surface; coordinate with the memory backlog item 0016 (vocabulary module).

## Status
Deprecated 2026-10-27: superseded by the operator ruling that AbstractMemory owns the whole memory graph (AbstractMemory ADR 0002). Entity resolution is planned in AbstractMemory as `planned/knowledge_graph_convergence/0050_entity_registry_identity_and_reversible_merges.md`; AbstractNote's side is `planned/memory_convergence/0033_converge_onto_abstractmemory.md`.

## Metadata
- Created: 2026-10-19
- Status: deprecated (superseded by 0033 and AbstractMemory 0050)
