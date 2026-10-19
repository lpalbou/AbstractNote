# Apple Watch app

## Summary
Build `watch/apple/`: XcodeGen project, `AbstractNoteWatch` target (watchOS 26, `ai.abstractframework.note.watch`), empty iOS container `AbstractNote`, UI tests, `Packages/AbstractGatewayClient` (snapshot of the AbstractAssistant watch client plus note endpoints, with `SNAPSHOT.json`), screens Note, Ask, Today, Settings, `scripts/build.sh`, `scripts/deploy-watch.sh`, a GitHub workflow.

## Why
The wrist is the lowest-friction capture surface; see clients.md.

## Scope
- In: sign-in (token or emailed code), file-sealed credential storage in the app container, start `note-capture` with `process_inline=false`, poll the run, standard recall, insights list, VoiceOver, mock client screenshots of every screen.
- Out: companion-app pairing, complications, background refresh (proposed later).

## Dependencies
Gateway contract (api.md), the `abstractnote` bundle published on the target gateway.

## Expected outcomes
- Simulator build and `swift test` pass; screenshots of all screens with the mock client; a note dictated on the watch appears in the web app.

## Current code reality
Precedent: abstractassistant/watch/apple (project.yml, build/deploy scripts, CI, pitfalls listed in untracked/recon/assistant-clients.md).

## Validation
`scripts/build.sh` green; manual install on the paired watch when the operator wants it.

## Metadata
- Created: 2026-10-19
- Status: completed
- Completed: 2026-10-19
- Area: watch

## Completion report
- Date: 2026-10-19
- Landed: `watch/apple/` (2ee84ab, 08cdabb, e885c63, 3093205, e428896; W7 TodayScreen): Note, Ask, Today, Settings, sign-in; client package snapshot with note endpoints; file-sealed credential; build/deploy scripts; CI workflow at `.github/workflows/watch.yml`.
- Validation: swift test 43 passed (recorded gateway fixtures, 401/409/413); simulator build and 17 screenshots; mutation checks red.
- Notes and residual risks: ADR 0006 proposed (operator ruling 0017). Residual: not run against a live gateway from the device; no attachment upload on the watch.
- Reviews: untracked/reviews/01-design-review.md (design), untracked/reviews/02-gate-review.md (integrated head; fixes in W7 commits up to d5e894d).
