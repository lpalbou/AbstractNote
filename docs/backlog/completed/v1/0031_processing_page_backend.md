# Processing page backend

## Summary
One read, `GET /note/processing`, tells what the processing is doing right now: the processor and
reflect automations with their last run, the queue with an ETA, the pace over the last two hours,
the runs active now with their step and the note they are on, the last revisions with their
durations, the failed notes, the model and (when its module is installed) the consolidation. One
action, `POST /note/queue/{note_id}/priority`, moves a waiting note in the line.

## Why
Operator ask (2026-10-19): a processing page that shows the work as it happens. The lead pinned
the contract in `untracked/briefs/processing-contract.md`; the web lane (W21) polls it every 3 s.

## Scope
- In: `src/abstractnote/recall/processing.py` (new), `Notebook.processing()` and
  `Notebook.set_priority()`, the two router endpoints, `tests/test_processing.py`, docs/api.md.
- Out: the web page (W21), the consolidation flow, automation and `consolidation_view` (W22),
  any flow change.

## Dependencies
The gateway's run store (`list_run_index`, `latest_occurrence_row`, `load`) and ledger
(`abstractgateway.ledger_tail.resolve_ledger_tail`), AbstractGateway 0.15.11; the consolidation
lane's `abstractnote.gateway.consolidation.consolidation_view` (optional: null without it).

## Expected outcomes
Every number is read from the note index or the plane's runs and ledger, no model; the answer is
cheap enough to poll every 3 s and carries an ETag (304 when unchanged).

## Current code reality
- SQL part: queue stats, pace (processing revisions by `recorded_at`, 24 buckets of 5 minutes),
  `eta_s` = queued x 3600 / understood in the last hour, last 20 processing revisions with counts,
  failed notes, communities.
- Plane part (`GatewayPlane`, one per plane, cached): RUNNING and WAITING runs of the six note
  flows; the step = latest started effect's node id through `STEP_OF_NODE`; a subflow parent takes
  the active child's note, step and sections; sections from the `note_context` result and the
  completed `extract` effects; ledgers read incrementally by tail; the last occurrence of each
  automation (a run completed with `success: false` is `failed`; insights from the run's
  `insight_note_ids` or, while running, `reflect_written`); revision durations from the completed
  note-process run whose output names the revision (newest 300 completed runs searched, results
  cached, negative after 2 minutes).
- Model: the notebook's `filing_model` when set (the pin the processor actually runs with), else
  the server's text default (`core_config.text_default`, cached 30 s).

## Validation
`tests/test_processing.py` (10): SQL numbers, ETA, model pin, priority rules and no revision, the
step table covers every effect node of the generated flows, the gateway endpoint (401, keys, ETag
and 304, the consolidation hook null / value / failure), priority 401/404 isolation/400/422, a
real drain + note-process driven over the scratch gateway's own stores and stopped after two of
three sections (step, sections, note, thinking, child link, Bob sees nothing), the revision
duration after it finishes, the last reflect occurrence and its insights. Seven mutations of
processing.py each turn the file red.

## Metadata
- Created: 2026-10-20
- Status: completed
- Completed: 2026-10-20
- Area: recall, store, gateway router

## Completion report
- Date: 2026-10-20
- Validation: pytest 354 passed (344 before); timing on a copy of the 4k-note notebook through the
  in-process gateway: 30 ms on the first call (imports, text default), then 2-3 ms; 304 in 2 ms.
- Notes and residual risks: the duration search lists COMPLETED runs, which on a file run store
  stats every run file once per new revision (cached afterwards); WAITING runs that never resume
  (an orphaned child) stay listed as active, which is what the store says. The model rule puts the
  notebook pin before the server default (the brief said server default first; the pin is what
  runs).
