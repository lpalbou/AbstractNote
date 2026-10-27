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
`src/abstractnote/store/kg.py` (2026-10-27): imports `SQLiteTripleStore` and uses `store._table`, `store._conn`
and `store._lock` to narrow a window; `pyproject.toml` pins `abstractmemory>=0.3.1`.

## Validation
`grep -rnE "\._(table|conn|lock)\b" src/abstractnote` returns nothing; the supersession/re-date tests that
exercise window narrowing stay green against the released AbstractMemory.

## Metadata
- Created: 2026-10-27
- Status: planned
- Area: store
