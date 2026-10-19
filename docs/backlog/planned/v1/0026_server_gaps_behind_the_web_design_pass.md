# Server gaps behind the web design pass: review, merged entries, cue evidence, archive snapshot, throughput

## Summary
Give the note read API what the web design pass (0025) had to work around: a review list of proposed
matches and disagreements with a server-side confirm action, merged entries readable as themselves,
cues and insight notes that say what they rest on, the archive snapshot axis (`as_received`: what the
archive held on a date), and the processor's throughput on the queue answer.

## Why
The 0025 report (untracked/reports/W9.md, "Endpoint gaps") listed six gaps: no review endpoint, no
way to confirm a match on the server ("Keep" was per browser), a merged entry's own name was not
readable, cues and insight notes carried no evidence ids, the archive snapshot was not a query, and
the web hard-coded "20 every 2 minutes". The design deck reflection (untracked/reviews/03-deck-reflection.md,
items 1 and 5) asks for the third date (archive-accepted) and a review surface instead of silent merges.

## Scope
- In: `src/abstractnote/store/` (archive axis, settings, entity record, review wrappers),
  `src/abstractnote/recall/` (review, cues, card and changes on the archive axis), the router,
  tests, docs/api.md and docs/data-model.md, the web client's lib and mock, re-recorded fixtures.
- Out: processing (`process/`, `flows/`, `tools/process.py`), ingest, the watch app, the web views
  (they switch to the new endpoints in their own lane).

## Dependencies
0006 (recall), 0007 (gateway seam), 0025 (the web design pass that found the gaps).

## Expected outcomes
- `GET /note/review` lists close matches (`unsure_match`: an active `skos:closeMatch` claim from the
  entry created for an unsure decision to its candidate), merges the processor made and nobody
  confirmed (`merged_by_model`), and disputed pairs (`disputed_pair`), each with the two named sides,
  evidence note ids and its actions; `POST /note/review/{id}/confirm` records a `match.confirm`
  user-action revision for the signed-in account (a close match becomes a merge; a merge is kept).
- `GET /note/entities?include_merged=true` and `GET /note/entities/{id}?as_merged=true` (the entry's
  own name, aliases, summary, notes, `merged_into` and the merge revision).
- Cues carry `evidence_note_ids` and `entity_ids`; `GET /note/notes/{id}` has `insight`.
- `as_received` on graph, entity card, changes and day; composes with `at`; data-model.md "Time"
  names the three dates (authored, valid, archive-accepted).
- `GET /note/queue` has `processor: {max_items, interval}` from the `processor_schedule` setting that
  `processor/ensure` creates the automation with.

## Current code reality
Before this item: `Notebook.cues` dropped the insight's ids, `Notebook.note` had no insight facts, the
router did not pass `include_merged`, the card always followed a merge, no read restricted by
`received_at`, and `ensure_automations` hard-coded `every 2m` with `DRAIN_DEFAULTS`.

## Validation
`pytest -q tests` (with abstractgateway installed, so the router tests run); web `npm test` (the mock
contract test reads the pinned rows of api.md); fixtures re-recorded from a hermetic gateway.

## Metadata
- Created: 2026-10-19
- Status: completed
- Completed: 2026-10-19
- Area: store, recall, gateway router

## Completion report
- Date: 2026-10-19
- Landed: 5e93f29 (cue evidence, note insight), 95b91a2 (processor throughput), e26f0cc (merged
  entries), 4c609ee (review and confirm), c0b14c7 (as_received), plus the fixtures and this item.
- Validation: pytest 199 passed (13 new: tests/test_review_and_archive.py, tests/test_gateway_w10.py);
  web vitest 61/61 and tsc clean; hermetic smoke re-recorded the 27 fixtures, 12 new fixtures recorded
  for the new reads.
- Notes and residual risks: the web views still use the per-browser "Keep" and the hard-coded
  throughput until the web lane switches (the lib has `fetchReview`, `runReviewAction` and the types);
  confirming a disagreement is refused (settle it by retracting a side); a changed
  `processor_schedule` applies when the processor automation is next created; on the archive axis, a
  claim closed by a note that arrived early but was processed after another closing note keeps the
  other note's end date (rare: two closures of one claim, processed out of arrival order);
  llms-full.txt was not regenerated (not this lane's path).
