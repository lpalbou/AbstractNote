# Memory convergence track (planned)

Operator ruling 2026-10-27: AbstractMemory *is* the semantic temporal memory graph and owns all of its
processing (AbstractMemory ADR 0002). AbstractNote builds on it and never rebuilds it; where AbstractMemory is not
good enough, AbstractMemory is improved. This track is AbstractNote's side of that convergence; the capability
work itself is AbstractMemory's track `abstractmemory/docs/backlog/planned/knowledge_graph_convergence/`
(0050-0055).

Governing ADRs: this repo's ADR 0003 and ADR 0008 (both amended 2026-10-27); AbstractMemory ADRs 0001 and 0002.

Items:
- `0033_converge_onto_abstractmemory.md`: the umbrella: migrate each parallel-graph capability, in
  AbstractMemory's order, and delete the local copy.
- `0034_remove_private_abstractmemory_access.md`: stop writing AbstractMemory's private table (first, smallest).
- `0035_hand_the_ripple_prototype_to_abstractmemory.md`: `retro/integration`'s propagation and activation go to
  AbstractMemory, not into `main` as they are.
- `0036_depend_on_the_layer_below.md`: package dependencies and imports follow the framework convention (the
  layer below only; no private gateway functions).

Order: 0034 → 0035 → 0033 (capability by capability) ; 0036 alongside, before the first release.

Rule while the track is open: no new generic memory-graph logic is added to this package.
