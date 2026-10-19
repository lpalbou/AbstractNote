# Web app: Capture, Graph, Remember

## Summary
Build `web/` (`@abstractframework/note`): Capture chat (panel-chat thread + composer with attachments and drop, streaming the `answer_user` message, showing filing results and links), Graph (entity focus, depth, time scrubber shared with Remember, day page, entity page), Remember (ask bar with effort dial and cue chips, memory lanes, then-versus-now, revisit), Settings drawer, and the app-server `bin/cli.js`.

## Why
The primary human client; see remembering.md and clients.md.

## Scope
- In: ui-kit tokens and components, panel-chat, responsive down to 400 px, keyboard access, no system vocabulary in copy, mock API mode for development and screenshots, Playwright smoke for the three views.
- Out: iPhone/Watch, offline mode.

## Dependencies
0007 (routes), 0005 (flows). Develop against a mock server first (`web/mock/`), then the hermetic gateway.

## Expected outcomes
- A note sent from Capture shows "Saved as ... Related: ..." within the run's time and the links when processed.
- Scrubbing the river changes the graph and entity cards consistently.
- Then-versus-now renders for an entity with several revisions.

## Current code reality
abstractobserver is the template for the npm/app-server layout; ui-kit 0.8.7 and panel-chat 0.4.1 provide chat, theming, sign-in; monitor-active-memory shows a ReactFlow KG explorer to learn from (see untracked/recon/assistant-clients.md).

## Validation
`npm test`, `npm run build`, Playwright screenshots of every view in three themes, manual check of copy rules.

## Metadata
- Created: 2026-10-19
- Status: completed
- Completed: 2026-10-19
- Area: web

## Completion report
- Date: 2026-10-19
- Landed: `web/` `@abstractframework/note` (b6abb77…d822da7, W7 fixes): Capture, Graph, Remember (since you last looked, cue cards, ask bar, then-now for entities and relations, evidence chains with retract, revisit), Settings; mock gateway; Playwright screenshots.
- Validation: vitest 51 tests; build green; Playwright S3 and M8 checks against the hermetic gateway; copy-rule scan test.
- Notes and residual risks: Residual: About button hidden until ui-kit knows the app id; sign-in form is custom (kit labels say Gateway).
- Reviews: untracked/reviews/01-design-review.md (design), untracked/reviews/02-gate-review.md (integrated head; fixes in W7 commits up to d5e894d).
