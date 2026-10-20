# Memory consolidation: a scheduled, triggerable, observable pass over the notebook

## Summary
A third automation per notebook, `consolidate`, runs the `note-consolidate` flow on a schedule chosen in
Settings (or at once, "Consolidate now"): it merges duplicate entries, recomputes the communities and names
the pending or stale ones, refreshes the entity summaries that are placeholders or older than their notes and
statements, refreshes the then-and-now caches of what changed, and runs the reflect steps that write insights.
Every stage reports its progress with n of m, elapsed time and an ETA measured from the stage's own pace.

## Why
Operator intent (2026-10-20, binding): an automated consolidation of the notebook and memories, schedulable
in Settings and triggerable, with realtime progress and ETA: refresh entity summaries, update communities and
their labels when needed, keep numbers and caches fresh, write insights. Summaries written at creation are
often placeholders ("Mentioned in ..."), and an entry mentioned by many later notes keeps the summary of its
first note; communities named once go stale as the graph grows; nothing refreshed them without a person
asking.

## Scope
- In: `recall/consolidation.py` (stale summaries, packets, progress rows, ETA, begin/end, normalize and
  then-and-now stages, `state`), `tools/consolidation.py` + `tools/__init__.py` (seven tools),
  `store/schema.py` (`consolidation_runs`, kept by a rebuild), `store/notebook.py` (setting
  `consolidate_schedule`), `process/schemas.py` (`ENTITY_SUMMARIES`, `EntitySummaries`),
  `process/prompts/entity_summaries{,.system}.md`, `flows/build.py` (`note-consolidate`, the shared
  labelling code, automation defaults) and the regenerated JSON, `recall/processing.py` (step table entry),
  `gateway/consolidation.py` (ensure hook, schedule hook, health, `consolidation_view`,
  `GET /note/consolidation`), `gateway/router.py` (the include and the hook calls only),
  `scripts/hermetic_smoke.py` and `scripts/stub_llm.py` (one consolidation in the smoke), tests, docs.
- Out: the web Processing card and the Settings picker (lane W21, on the pinned shape), the watch.

## Dependencies
0030 (communities and their naming), 0031 (`GET /note/processing` and its consolidation hook), 0015
canonical names (`normalize_entities`), 0006 (reflect).

## Expected outcomes
- Stages normalize -> communities -> labels (5 per call, loop) -> summaries (8 per call, loop,
  budget-bounded) -> then_now -> reflect, each reporting `note_consolidation_progress(stage, done, total)`.
- A stale summary is found structurally: empty, the creation placeholder (the revision builder's own
  warning, nothing written since), or notes/statements recorded after the summary's revision.
- Each summary round is one `entity.summary` revision (run_id = the consolidation), so history keeps every
  version and a rebuild replays it.
- `eta_s` = the stage's items left x its measured seconds per item (the last run's measure until one item
  is done).
- `POST /note/processor/ensure` creates/repairs the automation (same self-heal rules, plus the schedule);
  `PUT /note/settings {consolidate_schedule}` re-creates it; `off` = paused; `GET /note/status` lists it;
  `GET /note/consolidation` and `GET /note/processing` serve `{automation, run, last, pending}`.

## Current code reality
Before this item summaries changed only when a note's linking rewrote them; communities were renamed only
by the weekly reflect (one call, 5 groups); nothing reported progress beyond a run's ledger.

## Validation
`pytest -q tests` (tests/test_consolidation.py: placeholder detection through the real revision builder,
empty/changed staleness, packets and once-per-run offering, ETA arithmetic and the previous run's prior,
one end-to-end run on the core tools with a ScriptedLLM filling 11 summaries and naming 2 communities with
progress through every stage, a skipped entry not re-offered, the budget bound, a tool failure and a broken
round closing the row as failed with the sentence, a model failure read as interrupted, normalize merging a
legacy duplicate, the setting; tests/test_consolidation_gateway.py: ensure creates it one interval later,
status lists it, a schedule change recreates it, off pauses and on resumes, a user pause survives a repair,
the view on its own route and on the processing page). Mutations run red then restored: the in-flight
check, the placeholder rule, the once-per-run offering. `python -m abstractnote.flows.build --check`.
Hermetic smoke (`scripts/hermetic_smoke.py`, ports 18890/18891): one consolidation completes and
`consolidation.last.status == "completed"`.

## Metadata
- Created: 2026-10-20
- Status: completed
- Completed: 2026-10-20
- Area: recall, store, tools, flows, gateway

## Completion report
- Date: 2026-10-20
- Landed: 9d94e0f (store, tools, setting, flow), a99d98d (restores 06ac5f6's tasks hunk in notebook.py
  that 9d94e0f undid), d2564b9 (automation, schedule, view, route), 86bf87a (hermetic smoke), and the docs
  commit carrying this file.
- Validation: pytest green on HEAD plus this lane's paths (384 passed); flows `--check` up to date;
  hermetic smoke passed.
- Notes and residual risks: a model call that fails ends the run as an effect failure, so its row is left
  open and reads as `interrupted` rather than `failed` with the sentence. "Changed" staleness counts every
  mention after the summary, so a busy entry is refreshed on every run (bounded by the budget). No real
  model has written summaries yet (no model loads in this lane). The retrospective-insertion lane's
  `retro_state` marks (uncommitted while this landed) are not read by `note_entities_stale_summaries`.
