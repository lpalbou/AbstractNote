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
- Status: planned
- Area: watch
