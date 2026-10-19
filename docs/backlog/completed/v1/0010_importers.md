# Importers: Claude sessions, agora hub, Markdown, files

## Summary
Implement `abstractnote.importers` and the `abstractnote import` commands per importers.md with idempotent source keys, faithful `occurred_at`, provenance, noise filtering, and a counts report.

## Why
The framework's own history (54 sessions, 21k hub messages, 1.7k ADR/backlog files, 71 memory files) is the test corpus and a real use case: what did we forget?

## Scope
- In: the four importers; `--since/--until`; channel and kind filters for agora; skipping system/fs/hub-alerts; image attachments by hash; dry-run counts.
- Out: processing (the queue does it), other sources (proposed: email, browser history).

## Dependencies
0001, 0002.

## Expected outcomes
- Importing the current folder's sessions yields about 640 prompt notes, about 150 summary notes, about 235 subagent notes and 71 memory notes; re-running creates 0 new notes.
- Importing the default agora db yields the 241 decisions, claims/work entries and resolved threads with correct timestamps.

## Current code reality
Record shapes and counts are documented in untracked/recon/data-sources.md.

## Validation
`pytest tests/test_importers.py` on small fixtures; dry-run on the real sources with counts compared to the recon.

## Metadata
- Created: 2026-10-19
- Status: completed
- Completed: 2026-10-19
- Area: importers

## Completion report
- Date: 2026-10-19
- Landed: `importers/{claude_sessions,agora,markdown,files}.py` and `abstractnote import` (51b1501, 23e15e8: image prompts as attachments, embed flags).
- Validation: tests/test_importers.py; real import of 3,987 notes in the evaluation notebook (untracked/eval/import_*.json).
- Notes and residual risks: Residual: 14 earlier notes stored with repr text exist only in the evaluation notebook.
- Reviews: untracked/reviews/01-design-review.md (design), untracked/reviews/02-gate-review.md (integrated head; fixes in W7 commits up to d5e894d).
