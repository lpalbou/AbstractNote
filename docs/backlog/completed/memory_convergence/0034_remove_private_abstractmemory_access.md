# Remove the write to AbstractMemory's private table

## Summary
`store/kg.py` moves an already-closed assertion's `valid_until` earlier by writing AbstractMemory's private
`_table` under its `_lock`/`_conn`. Replace it with AbstractMemory's public `narrow_assertion(assertion_id,
valid_until)` once released, and raise the floor to that release.

## Why
A consumer writing another package's private storage breaks silently on any internal change and bypasses its
journal. AbstractMemory ADR 0002 forbids it; this repo's ADR 0003 (amended 2026-10-27) names it as the first
violation to remove.

## Scope
- In: the `store/kg.py` call site, the floor in `pyproject.toml`, a test.
- Out: any other change to the projection (0033).

## Dependencies
AbstractMemory `narrow_assertion` (its items `proposed/ripple_wave/0001` and planned 0051; whichever ships it)
and its release.

## Expected outcomes
No access to any `_`-prefixed attribute of an AbstractMemory object anywhere in this package.

## Current code reality
Before (2026-10-27, `main` b8b0f7b): `src/abstractnote/store/kg.py` used `store._table`, `store._conn` and
`store._lock` of AbstractMemory's `SQLiteTripleStore` to narrow a window; `pyproject.toml` pinned
`abstractmemory>=0.3.1`.
After (branch `feat/memory-graph-thin-client-1`): `ClaimStore.project` calls `self.store.narrow_assertion(
assertion_id, valid_until)` (AbstractMemory branch `feat/memory-graph-provenance`, 9818c79: True when a closed
window moved earlier, False for an unknown id or a window already at or before the date, `ValueError` for an
open window or a widening); the private method `ClaimStore.narrow_assertion` is gone; the floor is
`abstractmemory>=0.4.0`, the release that carries it.

## Validation
No `_`-prefixed attribute of an AbstractMemory object is read anywhere in this package:
`grep -rnE "\._(table|conn|lock)\b" src/abstractnote | grep -v "self\._"` returns nothing (the unfiltered grep
also matches AbstractNote's own `self._lock`/`self._conn` in `VectorCache`, `NoteVectorIndex` and the
processing plane cache, which are not AbstractMemory's). The supersession, `insert_before` and re-date tests
(`tests/test_retro.py`, `tests/test_kg_projection.py`, `tests/test_rebuild.py`) stay green against the live
AbstractMemory branch, and turn red when the narrow is removed.

## Metadata
- Created: 2026-10-27
- Status: completed
- Completed: 2026-10-27
- Area: store

## Completion report
- Date: 2026-10-27
- Landed: branch `feat/memory-graph-thin-client-1` (first commit of the branch); AbstractMemory side on its
  branch `feat/memory-graph-provenance` (9818c79, `narrow_assertion` on the SQLite and in-memory stores).
- Validation: `tests/test_retro.py`, `tests/test_kg_projection.py`, `tests/test_rebuild.py`: 43 passed with the
  editable AbstractMemory branch; mutant (the narrow call replaced by `False`): `test_retro.py` 2 failed
  (`test_out_of_order_2026_2022_2024_places_each_belief_in_its_interval` and the rebuild of the same history).
- Notes and residual risks: `abstractmemory>=0.4.0` is not on PyPI yet; the floor is written ahead of that
  release (the release sequence publishes AbstractMemory 0.4.0 before an AbstractNote that carries this). Until
  then a clean install resolves only against the editable checkout. A notebook written by an AbstractMemory
  without `narrow_assertion` opens unchanged (the method only moves `valid_until`).
