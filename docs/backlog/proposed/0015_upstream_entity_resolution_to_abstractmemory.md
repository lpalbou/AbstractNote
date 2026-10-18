# Upstream entity resolution to AbstractMemory

## Idea
Once stable, move candidate generation and the alias layer into an `abstractmemory.entity_resolution` module (framework ADR 0025 v1) so entities and agents share it. Must not change AbstractMemory's existing public surface; coordinate with the memory backlog item 0016 (vocabulary module).

## Status
Proposed; not an implementation commitment. Promote to planned with a code-reality section when scheduled.

## Metadata
- Created: 2026-10-19
- Status: proposed
