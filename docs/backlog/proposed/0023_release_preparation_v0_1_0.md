# Release preparation for 0.1.0 (and 0.1.1)

## Idea
Release order: (1) abstractmemory with `close_assertion` and raise abstractnote's floor; (2) abstractgateway with the AbstractNote seam and name that version in README, getting-started, the pyproject `gateway` extra and `abstractnote.gateway`'s import check; (3) abstractnote on PyPI and `@abstractframework/note` on npm; (4) the root installer's app list (`AF_NPM_APPS`, `NPM_RELEASE_VERSIONS`) and the framework pins; (5) the watch app through TestFlight once the operator rules on credential storage (0017). Every publication needs the operator's explicit go.

## Status
- Done: (1) AbstractMemory 0.3.1; (2) AbstractGateway 0.15.11 (`abstractgateway/abstractnote_seam.py`); (3) 0.1.0 on PyPI (Release run from tag v0.1.0 = c007f74) and on npm (published by hand; the workflow's npm job skipped it, so the npm trusted publisher is unproven).
- Prepared, not published (2026-10-19, gate review 03 B1/M1/M2/M3): 0.1.1 = HEAD's 20 commits past v0.1.0 (processing steps 1-3, recall fixes, web sign-in and canvas, watch kit, importer fidelity). Version bumped at the four sites, CHANGELOG `[0.1.1]`, docs describe `pip install "abstractnote[gateway]"`, release workflow runs the gateway tests, skips existing PyPI files, releases on GitHub only after both registries, and has a `dry_run` dispatch that rehearses the npm trusted-publisher exchange ([docs/release.md](../../release.md)).
- Next (operator): push `main`; dispatch the dry run (`gh workflow run release.yml --ref main -f version=0.1.1 -f dry_run=true`); when green, tag `v0.1.1` and push the tag. Then (4) and (5).
- Gateway follow-up (not AbstractNote's): a gateway without the `abstractnote` package lists the Note app as installed and running whenever an AbstractNote page answers on 127.0.0.1:3006 (external-app detection by page title; untracked/reports/W13.md).

## Metadata
- Created: 2026-10-19
- Updated: 2026-10-19 (0.1.1 prepared)
- Status: proposed
