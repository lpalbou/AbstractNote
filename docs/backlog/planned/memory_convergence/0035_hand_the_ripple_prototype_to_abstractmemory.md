# Hand the ripple prototype (`retro/integration`) to AbstractMemory

## Summary
The retrospective-ingestion work on the unmerged branch `retro/integration` (dependency records, forward-only
invalidation, the R6 propagation and activation lane) is a prototype of AbstractMemory ADR 0001's ripple. Do not
merge its generic parts into `main`; hand their design, calibration and tests to AbstractMemory item 0054, then
consume AbstractMemory's ripple from here.

## Why
Operator ruling 2026-10-27: the ripple is AbstractMemory's process (its ADRs 0001-0002); this repo's ADR 0008 is
amended accordingly. Merging the prototype would deepen the parallel graph 0033 removes.

## Scope
- In: an inventory of the branch split into note-specific parts (dates and their basis, "as of the note's date"
  prompt framing, the re-understanding stage's views: may merge to `main` on their own merits) and generic parts
  (propagation, activation, dependency-bounded invalidation, no-call settle, calibrated thresholds: hand over);
  a handover note in AbstractMemory's item 0054 (design, constants with their calibration, the tests to port).
- Out: implementing the ripple in AbstractMemory (its item 0054).

## Dependencies
AbstractMemory `proposed/ripple_wave/0001` and planned 0054. Operator decision pending (2026-10-27): whether to
push `retro/integration` and `retro/w32` to the remote as a backup until the handover is done.

## Expected outcomes
`main` gains no generic ripple code; AbstractMemory 0054 cites this branch as prior art; once AbstractMemory
ships the ripple, AbstractNote triggers and displays it.

## Current code reality
`retro/integration` (local only, 28 commits ahead of `main` on 2026-10-27) adds `src/abstractnote/recall/
propagation.py` and `src/abstractnote/recall/activation.py`; the design brief is
`untracked/briefs/R6-propagation-design.md`; ADR 0008 records the R6 constants (decay 0.5, floor 0.1, one hop
per pass, budgeted stage, no-call settle).

## Validation
The handover note exists in AbstractMemory 0054; `main` contains no `recall/propagation.py` or
`recall/activation.py`; after AbstractMemory's release, the retro acceptance scenarios (ADR 0008 Validation) pass
with AbstractMemory's ripple.

## Metadata
- Created: 2026-10-27
- Status: planned
- Area: recall, retrospective ingestion
