# Deploy and verify AbstractNote's shared top-right widgets

## Summary
Serve the existing shared AbstractUIC widget set in the operator's running Note.

## Why
Operator screenshot shows only legacy sun/contrast buttons. Source already adopted
AfTopBarActions, Appearance, About, docs assistant and account menu in d8cabd5;
the running Gateway-managed app is still npm Note 0.1.0 (ui-kit ^0.8.7), whereas
this checkout is 0.3.0. This is deployment drift, not a missing source adoption.

## Scope
- In: verify/build the existing source adoption, repair stale browser smoke
  selectors, deploy the current Note through the existing app-management surface,
  and check real served header/theme/about/docs/account behavior.
- Out: unrelated UI or notebook processing changes, registry publication, new services.

## Dependencies
Existing AfTopBarActions API and Gateway app manager; 0038 npm publication history.
ADR impact: none, existing shared component and thin-client contracts.

## Expected outcomes
Live Note shows shared docs, appearance, About, username/status/menu. No duplicate
legacy theme controls; sign-in, mount prefix and stored notebook data preserved.

## Current code reality
`web/src/App.tsx` already composes all four shared pieces and `web/test/top_bar_set.test.ts`
pins them. `web/scripts/screens.mjs` still waits for deleted `.nt-account` and
`.af-topbar__pill` after sign-in, so its served-build smoke is stale. Correct the
smoke and add interaction evidence; avoid reimplementing the widgets.

## Validation
Focused existing tests, production build and browser smoke against mock; live
served Note widget interactions without sending notebook changes. Scoped commits
and deployment evidence. Agora unavailable (connection refused).

## Metadata
- Created: 2026-10-30
- Status: completed
- Completed: 2026-10-30
- Area: web, deployment

## Completion report

2026-10-30: source adoption `d8cabd5` was already merged; running npm 0.1.0
predated it. `557dc00` repairs stale browser smoke selectors and adds a shared
widget production-build gate. 181 web tests, production build/relative-URL guard
and header mock browser checks pass. Live dark/light/mobile widgets and account
menu verified with no page errors. Verification blocked automatic processor-ensure
writes; no notebook mutation was performed by the checks.

Deployed current 0.3.0 frontend assets to the existing managed server's static
dist, with full backup, old hashed assets retained, atomic index switch and
`.local-frontend.json` provenance. This adjusts the intake's intended manager
update: its API only accepts registry versions, and currently published 0.2.0
also predates adoption. The Node server/installed package remain 0.1.0; no
registry release, service installation or app-manager metadata change was made.
Existing 0038 covers publication of current source, needed for future fresh
registry installs; its older claim that only 0.1.0 is published is now stale.
No new ADR or unrelated changes.

[Visual review and deployment procedure](../../../design/shared-widgets-0902/REVIEW.md),
[validation and hashes](../../../design/shared-widgets-0902/validation.json).
