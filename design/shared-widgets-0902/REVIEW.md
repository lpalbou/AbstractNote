# Shared Note widgets — 0902

Single-agent implementation/visual review. Live [dark](header-dark.png),
[light](header-light.png), [account menu](account-menu.png),
[Appearance](appearance-light.png), [400px mobile](mobile-light.png).

The source already adopted AbstractUIC `AfTopBarActions`, `AfAppearanceDialog`,
`AfAboutDialog`, account menu and panel-chat docs drawer in `d8cabd5` (merged).
The actual gateway-managed Node server was serving npm Note 0.1.0 assets from
October 20, before that adoption. npm currently offers 0.2.0, whose source map
also lacks docs/account-menu adoption. Reimplementing the source widgets would
not have changed that served build.

Corrected stale `.nt-account`/connection-pill smoke selectors and added
`--only-header`: production sign-in, all shared controls, account menu, theme and
typography dialog, About, docs drawer, light/dark and 1280/400px. 181 web tests,
production TypeScript/Vite build/relative URL checks, and mock header checks pass.
Installed build dependency is the shared kit 0.9.8, satisfying the source ^0.9.6
range. No widget copy, manual theme fork or per-widget app styling was added.

## Local deployment

Smoke implementation `557dc00`. Deployed the built 0.3.0 source frontend into
the existing managed app's static `dist/`. App-manager accepts registry versions
only; registry publication is outside this request. This is explicitly a local
frontend override, not an npm package upgrade or release. The installed package
and Node server remain 0.1.0; runtime app-manager metadata is unchanged.
`validation.json` records source commit, every deployed file hash and rollback.

Copied the previous complete dist to
`runtime/apps/note/0.1.0/node_modules/@abstractframework/note/.local-frontend-backups/0902-original/dist`,
then copied the new hashed assets, icon and llms.txt. Retained old hashed assets
for open tabs; switched index.html atomically last. The existing server reads
assets on each request, so no Node restart, sign-in loss, notebook mutation or
new service was needed. Deployment receipt also lives beside the installed
package in `.local-frontend.json`. A later registry reinstall/update may replace
the override; publishing the current source remains the existing 0038 release
lane (not performed here).

Live gateway-mounted Note was opened via its normal authenticated app handover.
Dark/light shared controls, username `admin`, connected status dot, one account
menu with Disconnect, Appearance, About, docs drawer and mobile fit all pass;
zero page errors. The verifier intercepted the app's automatic processor-ensure
POSTs to keep notebook state untouched; these blocked attempts are recorded,
not claimed to be absent. No note was sent or operation approved. Normal app
bootstrap behavior was not changed. [Machine receipt](validation.json).

No new ADR impact or implementation follow-up beyond the existing publication
lane. Agora unavailable (connection refused). The old 0038 registry summary is
stale: 0.2.0 is now visible, but it still predates shared-set adoption.
