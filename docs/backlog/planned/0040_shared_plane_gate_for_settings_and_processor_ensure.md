# Shared plane: `PUT /settings` and `POST /processor/ensure` ask for admin or the plane owner

## Summary
Two of the gateway router's fourteen write routes act on the WHOLE plane, not on one account's knowledge:
`PUT /api/gateway/note/settings` sets the filing and answering models and the schedules for the plane, and
`POST /api/gateway/note/processor/ensure` archives the processor automations another account may have created and
recreates them under the caller. In a plane of one account that is fine. In a SHARED plane (a runtime an admin bound
several accounts to, or an entity's plane) any member can run them today. The handler should require admin or the
plane owner there; the other twelve routes stay member-level (account-stamped, reversible knowledge edits that a
shared notebook exists for).

## Why
AbstractGateway security incident 2026-10-29 (gateway `docs/security.md`, PR #55): the fourteen routes had no
authorization decision in the gateway's contract; they were reachable by any signed-in principal on every real
install. The gateway decided all fourteen user-level, because an admin row there would break the Note app for every
member; the shared-plane gate for these two therefore belongs here, in the handler that knows the plane.

## Scope
- `gateway/router.py`: `note_settings_put` and `note_processor_ensure` refuse (403, a sentence naming the rule) when
  the plane is shared and the caller is neither admin nor the plane's owner. "Shared" and "owner" come from the
  gateway's principal/plane seam (`get_gateway_service()` / `_principal`), never from a client-supplied name.
- Member-level stays for: archive, redate, refile, retract, close, confirm, unmerge, normalize, ack, retry, priority,
  recompute.
- `docs/api.md`: the two routes say who may call them.

## Dependencies
- Gateway: PR #55 (decision recorded), planned 0972 (CI installs abstractnote so the contract covers these routes).
- A way to ask the gateway "is this plane shared, who owns it" from the seam; if none exists, raise it to the
  gateway as a blocked ask rather than guessing from paths.

## Expected outcomes
A member in a shared plane gets a 403 sentence on the two routes and keeps every other note action; an admin and the
plane owner are unaffected; a one-account plane is unaffected.

## Current code reality
`_run` resolves the principal and the plane and runs the handler; no handler checks a role. The gateway's
`GATEWAY_ROUTE_POLICIES` has no note rows by decision (PR #55).

## Validation
- Tests: member in a shared plane → 403 on both routes, 200 on retract/close; admin and owner → 200; single plane →
  200 for its account. Mutation check: removing the guard turns the member test red.
- Gateway's `tests/test_gateway_route_authorization_contract.py` keeps the fourteen rows user-level (the guard is in
  the handler, so the gateway row does not change).

## Metadata
- Status: planned · Priority: P1 (security) · Created: 2026-10-29 · Source: AbstractGateway PR #55 review (worker B)
- Cross-package: AbstractGateway (seam, incident note), AbstractFramework recurrent 1060.
