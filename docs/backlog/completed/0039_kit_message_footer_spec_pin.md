# Completed: web pin on the kit with the message footer spec (main only)

## Metadata
- Created: 2026-10-28
- Status: Completed on main (not released: the npm trusted publisher of @abstractframework/note is the operator's)
- Area: `web/package.json`

## Summary
The kit's message footer spec (AbstractUIC 0.8.4, panel-chat 0.6.4, backlog 0064) is adopted by every web app by pin
bump. The Note web app renders no chat message card (it uses the composer, Markdown and the replay-view projection),
so nothing changes on screen; the pin (^0.6.4 / ^0.9.5, were ^0.6.0 / ^0.9.2) keeps it on the kit the other apps ship.

## Evidence
- `npm test` and `npm run build` green on the bumped pin (see the PR). No tag: the release is the operator's.
