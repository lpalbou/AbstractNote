# Upgrades keep the same automations: follow the latest note workflows instead of archiving and recreating

## Summary
Each AbstractNote upgrade replaces the notebook's processor, reflect and consolidate automations with new ones:
`ensure_automations` sees that the old automation targets `abstractnote@<old>:<flow>`, archives it, and creates a new
automation under a new request id (`-<generation>`). That repair exists only because the gateway loads just the
installed version of the note workflows, so the old target vanishes at restart (gateway backlog
[1147](../../../../abstractgateway/docs/backlog/planned/1147_every_installed_workflow_version_stays_loadable.md)).
Once the gateway keeps every version loadable and lets an automation created with an unversioned `bundle_ref`
follow the latest version at each occurrence, Notes keeps ONE automation per job across upgrades: same id, same
history, the latest workflow on every new run.

## Why
- Operator rule (2026-11-07): no older workflow version is deleted; new tasks and runs always use the last version.
- Archive-and-recreate leaves a trail of archived automations and new sessions on every upgrade, and the old ones
  are what other apps showed as "Workflow version unavailable" (AbstractCode, before its 0.15.3 workaround).
- Live proof of the break: the processor controller `runtime/run_bbc08b4b-2261-52bd-9462-9e97505b7df5.json` FAILED on
  2026-11-07 08:57 UTC, `Workflow 'abstractnote@0.3.0:note-drain' not found in registry`, right after 0.3.1 was
  packed; the queue stopped draining until `POST /note/processor/ensure` recreated the automations on 0.3.1.

## Scope
- In:
  - `gateway/router.py` `ensure_automations` and `gateway/consolidation.py` `ensure_consolidation`: an automation
    whose only problem is an older target version is kept (it follows the latest by itself once gateway 1147 ships);
    archive and recreate only for real faults (missing, archived, wrong tool list, wrong flow).
  - `gateway/health.py` `automation_health`: an older version on a follow-latest automation is healthy, not
    "targets an old version"; a pinned automation on an older version stays unhealthy with its sentence.
  - Create automations as follow-latest explicitly (the gateway's field from 1147), never relying on an implicit default.
  - One migration on first ensure after the upgrade: existing pinned automations are switched to follow-latest in
    place (gateway `automation.revise` with `changes.target`, `routes/automations.py:1409`), no new ids.
  - Docs: `docs/api.md` (`/processor/ensure` actions), CHANGELOG.
- Out:
  - Deleting archived automations already created by earlier upgrades (history stays).
  - Any change to how the gateway packs or loads the note bundle (gateway 1147 owns that).

## Dependencies
- Gateway [1147](../../../../abstractgateway/docs/backlog/planned/1147_every_installed_workflow_version_stays_loadable.md)
  steps 1-5 (versions kept, follow-latest automations). Until it ships, today's repair must stay: without it the
  queue stops draining after every upgrade.

## Expected outcomes
- After `pip install -U abstractnote` and a gateway restart, `GET /note/status` reports the same automation ids as
  before, healthy, and the next processor tick runs the new version.
- No new archived Notes automation per upgrade.

## Current code reality
- Target frozen at creation: `health.py:3-9` (module doc says the gateway serves only the installed version and the
  automation's next occurrence fails); `expected_workflow_id` compare at `health.py:55`.
- Repair: `router.py:760` `ensure_automations`; `_archive(svc, principal, previous)` at `router.py:807`;
  new request id `f"{request_id}-{generation}"` at `router.py:811`; target `{"bundle_ref": BUNDLE_ID, "flow_id": …}`
  (unversioned) at `router.py:817`; same pattern in `consolidation.py:124-127`.
- The installed package names the workflows: `flows/bundle.py:12-15` (`bundle_version()` = `abstractnote.__version__`),
  packed by the gateway seam into `<data dir>/extensions/abstractnote/<hash>/abstractnote@<version>.flow`. Live:
  four packs on disk (0.1.0, 0.1.1, 0.3.0, 0.3.1), only 0.3.1 served.
- `abstractgateway apps update` updates the Note web app only; the workflows move with the Python package.

## Validation
- Red-first test: an automation on `abstractnote@0.3.0` with 0.3.1 installed and 0.3.0 still loadable: `ensure`
  answers `kept` (fails today: `recreated`).
- Pinned automation on an old version: still repaired, with its reason.
- Migration test: pinned automations become follow-latest in place, ids unchanged, idempotent on a second ensure.
- Hermetic gateway (scratch data dir, never :8080): upgrade 0.3.0 → 0.3.1 across a restart, the processor keeps its
  id and its next run is `abstractnote@0.3.1:note-drain`.

## Metadata
- Created: 2026-11-07
- Status: planned
- Area: gateway seam (automations), upgrades
- Priority: P0, with gateway 1147
