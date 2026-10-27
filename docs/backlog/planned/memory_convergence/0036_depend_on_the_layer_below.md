# Depend on the layer below; no private gateway functions

## Summary
Bring AbstractNote's dependencies in line with the framework convention: a package lists only the layer below
it. AbstractNote is an application on AbstractGateway; its direct framework dependencies, and its imports of
private gateway functions, should go.

## Why
Operator, 2026-10-27: "i try in all my packages to only list the layer below as a dependency". Today AbstractNote
pins four framework packages directly and imports private (`_`-prefixed) functions of AbstractGateway's routes,
so a refactor inside the gateway breaks it silently and version floors drift independently.

## Scope
- In: decide the single layer below (AbstractGateway for the server side; AbstractMemory remains reached through
  the gateway or as the one memory dependency, to decide with 0033); replace private gateway imports with public
  gateway API (raise them as AbstractGateway backlog items where missing); trim `dependencies` accordingly.
- Out: the memory migration (0033).

## Dependencies
AbstractGateway public API for automations, principals and bundles (to request); 0033 for the memory side.

## Expected outcomes
`pyproject.toml` lists the layer below only; no `_`-prefixed import from another framework package.

## Current code reality
`pyproject.toml` (2026-10-27): `abstractmemory>=0.3.1`, `abstractsemantics>=0.0.5`, `abstractruntime>=0.10.2`,
`abstractcore>=2.26.3`, `networkx`; extra `gateway = abstractgateway>=0.15.11`. Private gateway imports in
`gateway/router.py` (`_resolve_bundle_from_host`, `_principal_from_request`, `_create`, `_principal_tuple`,
`_command`) and `gateway/consolidation.py` (`_command`); `recall/processing.py` imports
`abstractgateway.ledger_tail` and `abstractgateway.core_config`; `flows/runner.py` and `gateway/router.py` import
AbstractRuntime directly; `tools/__init__.py` and `ingest/extract.py` import AbstractCore.

## Validation
`grep -rnE "from abstract[a-z]+(\.[a-z_]+)* import _" src/abstractnote` returns nothing; a clean-venv install of
AbstractNote with only the declared dependencies passes the suite.

## Metadata
- Created: 2026-10-27
- Status: planned
- Area: packaging, gateway seam
