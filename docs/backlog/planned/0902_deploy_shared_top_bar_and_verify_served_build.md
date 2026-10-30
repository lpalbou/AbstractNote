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
- Status: planned
- Area: web, deployment
