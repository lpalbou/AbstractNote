# Gateway integration seam (tools binding, read API, app catalog)

## Summary
Implement `abstractnote.gateway` (FastAPI router for the note read API; `bound_tool_map(data_root, embedder)`; auto-approve tool names) and the explicit AbstractGateway changes: bind the note tools per plane next to `read_skill` in `hosts/bundle_host.py`, extend the approval auto-set with the note tool names, include the router under `/api/gateway/note` when `abstractnote` is importable, add the `note` app catalog row (port 3006, cookie prefix `abstractnote`, title `AbstractNote`) in `apps_manager.py`, tray `WEB_APPS`, console blurbs, docs/apps.md; ship the `abstractnote` bundle at boot via the shipped catalog.

## Why
The gateway has no plugin mechanism; an explicit optional integration is the smallest faithful seam. Per-user isolation comes from `get_gateway_service()` and the plane `data_dir`.

## Scope
- In: all routes in api.md "Note read API"; principal resolution through the gateway service; 404 for other users' ids; the gateway patch as a branch in the abstractgateway worktree with tests; `POST /note/processor/ensure` creating automations through the gateway's own automation service.
- Out: a generic plugin loader for the gateway (proposed separately in the gateway's backlog if the operator wants it).

## Dependencies
0001, 0004, 0006; abstractgateway>=0.15.4 source (sibling repo, operator's; changes land on a branch, release decided by the operator).

## Expected outcomes
- A hermetic gateway with `abstractnote` installed serves `/api/gateway/note/status` for a signed-in user and 401 anonymously; two users see different notebooks.
- A `note-capture` run in that gateway calls `note_capture` without an approval wait.
- `/apps/note/` mounts the web app.

## Current code reality
abstractgateway hosts/bundle_host.py:1810-1829 builds the per-plane tool map and the approval policy; routers are fixed in app.py:165-234; app catalog hard-coded in apps_manager.py:115-163 (see untracked/recon/gateway.md).

## Validation
Gateway tests for the router (auth, isolation), tool binding test, `abstractgateway apps` listing `note`; hermetic gateway run on a scratch port and data dir.

## Metadata
- Created: 2026-10-19
- Status: completed
- Completed: 2026-10-19
- Area: gateway

## Completion report
- Date: 2026-10-19
- Landed: abstractgateway worktree branch `abstractnote-seam` (777e03e5): `abstractnote_seam.py`, bundle_host tool binding next to read_skill, approval auto-set extension, `/api/gateway/note` router include, `note` app row (port 3006) in catalog/tray/console/TUI, docs/apps.md; `abstractnote.gateway/{router,tools,appspec}`; hermetic gateway scripts (f596c82, cd6c3f6).
- Validation: gateway worktree tests 165 passed; router tests incl. as_of over HTTP (1c66b67), 401/404 isolation; hermetic E2E by gate 02.
- Notes and residual risks: ADR 0001. Residual: the gateway release carrying the seam must be named before publication; the bundle loads into each plane's private registry (catalog bundles cannot be started by id).
- Reviews: untracked/reviews/01-design-review.md (design), untracked/reviews/02-gate-review.md (integrated head; fixes in W7 commits up to d5e894d).
