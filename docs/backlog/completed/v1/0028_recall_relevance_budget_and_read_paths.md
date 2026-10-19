# Recall relevance, the standard packet budget, contradiction candidates and read-only reads

## Summary
Fix the recall and read-path findings of the first corpus evaluation (0011): quick search returns
entity and statement rows only when they are relevant and carried by a returned note, can answer
"when did we first mention X", the standard recall packet is chosen within a budget instead of
dumped, contradiction candidates are only the disagreements a note recorded, open loops say how old
they are, and reads never finish another process's half-applied revision.

## Why
untracked/eval/REPORT.md: entity and claim results were irrelevant in all 15 quick questions and
there was no earliest-first answer (section 2); the 11 contradiction candidates were all false,
from multi-valued predicates (section 2, 4.3 item 5); CLI reads ran recovery and could finish a
revision another process was applying (4.3 item 6); standard recall passed 80-167k characters and
degenerated at 167k (4.3 item 7); open loops need an age (4.4).

## Scope
- In: `src/abstractnote/recall/` (search, expand, reflect), `src/abstractnote/tools/recall.py` and
  the `note_search` / `note_expand` / `note_contradictions` wrappers in `tools/__init__.py`, recovery
  and `tasks` in `store/notebook.py`, the CLI read commands and `abstractnote recover`, tests,
  docs/api.md, docs/remembering.md (and the one `note_contradictions` row of docs/cognition.md).
- Out: processing prompts (`process/`, lane W8: the false open loop below), the flows (`flows/`:
  passing `query` to `note_expand`), the gateway router (`gateway/`: `order` on `GET /note/search`),
  importers, web, watch.

## Dependencies
0006 (recall), 0011 (the evaluation and its notebook).

## Expected outcomes
- Quick search: every hit has `relevance` in [0, 1] (rarity-weighted share of the query words,
  raised never lowered by the cosine); entity and statement hits need `HIT_MIN_SCORE` (0.5) and a
  returned note that carries them, list those `note_ids`, entities with `first_at` / `latest_at`;
  notes list `matched_entities` and `matched_claims`.
- `order=earliest` on `note_search`, `Notebook.search` and `abstractnote search --order earliest`:
  notes reaching `EARLIEST_MIN_SCORE` (0.75), oldest first.
- `note_expand` within `EXPAND_BUDGET_CHARS` (24,000 JSON characters): focus entities first as one
  line each, notes with excerpts around the match, other entities one line each, timelines,
  then-versus-now, relations and neighbours for the focus entities only; `omitted` counts.
- `note_contradictions`: disputed pairs only (processing records no single-valued predicates).
- `GET /note/tasks` and `note_open_loops`: `age_days` and `stale` (`STALE_DAYS` 60).
- `Notebook.open` does not recover; capture, filing, processing, user actions, archiving and
  `abstractnote recover` do.

## Current code reality
Before this item: entity and claim hits were ranked by reciprocal rank only, so the best of an
irrelevant lot always showed (with a 146-entity notebook every entity matched "what" or "for", and
every cosine passed 0.2); `expand` returned up to 10 full entity cards (all beliefs, relations, ten
notes each), full claim timelines for every entity of every note, all relation pairs and full
then-versus-now payloads (144k characters for the agora-hub question); `contradictions` grouped
every active (subject, predicate) with several objects; `Notebook.open(recover=True)` was the
default for every caller, the CLI reads included.

## Validation
`pytest -q tests`: synthetic tests in tests/test_recall.py and tests/test_read_paths_recovery.py,
and tests/test_recall_eval.py on a copy of `untracked/eval/notebook` (skipped with its reason when
that notebook is absent) with the 15 evaluation questions as a fixture
(tests/fixtures/eval_quick_questions.json). Each new test was run against the code before its fix
and failed there.

## Metadata
- Created: 2026-10-19
- Status: completed
- Completed: 2026-10-19
- Area: recall, store (recovery), tools, CLI

## Completion report
- Date: 2026-10-19
- Landed: 480d66c (reads never recover, `abstractnote recover`), d3ce895 (contradictions are
  disputed pairs only), 5d2909d (open loops age and stale), 4064d89 (relevance floor, grouping,
  earliest order), 35d0a69 (standard packet budget).
- Validation: pytest 216 passed, 3 skipped (abstractgateway is not installed: the router, w10 and
  one flows-compile case); 37 tests are new or changed in this item.
- Notes and residual risks: the evaluation copy has no query embedder here, so its questions run on
  full text only; the vector channel can only raise a hit (never carry it past the floor alone), so
  the floor holds with vectors too, but semantic-only entity hits are no longer shown. Lexical
  relevance cannot tell a topical "mention" from the word in "when did we first mention", so
  earliest order is documented as "search for the subject". `GET /note/search` does not pass `order`
  yet (gateway lane) and the recall flow does not pass `query` to `note_expand` yet (flows lane); the
  search answer that the synthesis prompt also carries is still about 22k characters. The false open
  loop of REPORT section 3 item 7 is an extraction issue, handed to the prompt lane.
