# Documentation core set and llms files

## Summary
Complete the coredoc set: README, CHANGELOG, CONTRIBUTING, CODE_OF_CONDUCT, SECURITY, ACKNOWLEDGEMENTS, docs/README index, getting-started, architecture (with diagrams), api, faq, troubleshooting, the topic pages (data-model, cognition, remembering, clients, importers), and `llms.txt` / `llms-full.txt`.

## Why
External users, contributors and tools must be able to understand and run AbstractNote from the repository alone.

## Scope
Faithful to the code at the time of writing; no incident narratives; regenerate llms files in the same pass as any docs change.

## Dependencies
All implementation items (docs must describe what exists).

## Expected outcomes
Every page listed exists and agrees with the code; links resolve.

## Current code reality
Design pages written first as the specification (2026-10-19); they must be re-audited against the implementation before release.

## Validation
Link check script; manual read of getting-started on a clean machine.

## Metadata
- Created: 2026-10-19
- Status: completed
- Completed: 2026-10-19
- Area: docs

## Completion report
- Date: 2026-10-19
- Landed: README, CHANGELOG, CONTRIBUTING, CODE_OF_CONDUCT, SECURITY, ACKNOWLEDGEMENTS, docs/README, getting-started, faq, troubleshooting, llms.txt, llms-full.txt; design pages repaired to the code (6c04b2f, W7 docs commits).
- Validation: Link check 131 links 0 broken; every headless command verified in a venv; gate 02 doc claims re-checked (two false claims fixed by W7).
- Notes and residual risks: Residual: release names (gateway version with the seam, abstractmemory version) are placeholders until the release.
- Reviews: untracked/reviews/01-design-review.md (design), untracked/reviews/02-gate-review.md (integrated head; fixes in W7 commits up to d5e894d).
