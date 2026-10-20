# ADR 0007: Nothing stops or drops silently

**Status**: Accepted (operator ruling of 2026-10-20)  
**Date**: 2026-10-20

## Context

The framework's root ADR-0026 ("Truncation policy and contract") forbids lossy truncation on critical paths,
requires every lossy site to be tagged `#[WARNING:TRUNCATION]` and every truncation to be warned with
attribution, and allows selection under a budget only when it is transparent: what was selected, why, and what
was left out.

On 2026-10-20 the operator's consolidation `cs_a3f198272d674cfc8571` refreshed 80 summaries in 10 rounds,
then the 11th model call answered `{"summaries": []}`; the tool stored nothing without a warning, the loop
condition (`stored > 0`) ended the stage, and the run closed as "completed: 80 summaries refreshed" while 536
summaries were still stale. Nothing said so: not the run row, not the log, not the Processing page, whose
pending count silently disagreed with "completed". A second run refreshed all 536, so the stop was the empty
answer, not a bound. The same audit found other silent losses: summaries over 600 characters and labels over
4 words cut or dropped, topics past the per-note bound dropped from a revision, clamps of a caller's limit
applied without a word, packet and list selections that did not say how much they left out.

The operator's ruling: "we usually require smarter strategies than just capping and even when justified, this
must ALWAYS be justified in the code and send a message or a warning, it must NEVER be silent to developers and
backends (and for end users, subtle messages)".

## Decision

1. **An empty or partial model answer never ends a stage.** A consolidation round (labels or summaries) whose
   answer leaves out an offered item asks once more with the same prompt (a fresh call, like the processing
   lane's empty-extraction retry). What is still unanswered is *skipped for this run*: recorded on the stage
   (`stages_json[stage]`: `skipped`, `reason`, `reasons`, `skipped_ids`), logged as a warning on
   `abstractnote.consolidation` naming the stage, the items and the reason, and never offered again in that run
   (the run row's offered list for summaries, the `exclude` argument of `note_communities_pending` for groups).
   A stage ends only when nothing it did not skip is left, or when the time budget is spent.
2. **A run that leaves something out says so.** `note_consolidation_end` checks at the close what is still
   pending of what the run skipped, adds what the budget did not reach (`budget_spent`, `not_reached`), never
   counts more than `pending` shows, and closes the run as `completed_with_gaps` with `left_out = {summaries,
   labels, reasons}`; `completed` means nothing was left out. `GET /note/consolidation` `last` carries
   `left_out`, `budget_spent` and `notice`, one sentence for end users ("12 summaries could not be refreshed
   this time; they will be tried again next time."). The Processing page shows it as a muted line, never a
   modal; its state sentence reads "Consolidated with gaps · …" when the notebook is otherwise idle.
3. **Nothing is dropped for its length.** A summary over 600 characters or a label over 4 words is kept whole
   with an `over_length` warning (the prompt asks for the length; the store does not enforce it by cutting).
4. **A selection says what it left out.** Choosing items under a bound (top-k, a packet budget, a per-note
   bound) is allowed when the result counts the rest (`omitted`, `topics_not_assigned`, `waiting`) or the
   response names it (a revision warning and summary sentence).
5. **A clamp of a caller's request is answered.** `clamp_limit` logs every clamp on `abstractnote.limits` with
   the function that clamped; results answer it where their shape allows (`limit_applied`, a search warning).
6. **A lossy preview is tagged and marked.** Every slice of content carries `#[WARNING:TRUNCATION]` in the code,
   ends with the "…" marker, and comes with the id that opens the whole content. A preview may be context in a
   prompt; it never stands in for the content on a correctness path (what a structured answer, a memory
   ingestion or a provenance record stores).

## Classification of the package's bounds (2026-10-20)

| Site | Bound | Class | Record |
|---|---|---|---|
| consolidation labels and summaries rounds | 5 groups, 8 entries per call | selection (batch) | retry, skip list, `left_out`, `completed_with_gaps`, `notice` |
| `recall/consolidation.py` `SUMMARY_MAX_CHARS` | 600 | was a drop, now keep + warn | `over_length`, revision warning, log |
| `recall/communities.py` `LABEL_MAX_WORDS` | 4 | was a cut, now keep + warn | `over_length` warning, log |
| consolidation packet `PACKET_CLAIMS`, `PACKET_NOTES` | 8, 3 | selection | packet `omitted {claims, notes}` |
| communities pending `PENDING_CLAIMS`, `PENDING_EXCERPTS`, `PENDING_LIMIT` | 8, 3, 5 (1..20) | selection | item `omitted`, `waiting`, `limit_applied` |
| `store/notes.py` `TOPICS_PER_NOTE` | 3 | selection | `topics_not_assigned` in the result, revision warning and summary, log |
| `ingest/capture.py` `MAX_FILING_TOPICS` | 4 | selection | `note_file` `topics_not_assigned`, log |
| `ingest/capture.py` `SIMILAR_LIMIT` | 5 nearest above 0.35 | selection (filing context) | the criterion is the record (nearest first) |
| `store/notes.py` `note_vector_text` | `NOTE_EMBED_BUDGET` | lossy (embedder input only) | tagged, logged; full text stored and searchable by words |
| `recall/shapes.py` `excerpt`, note list excerpt | 280 | lossy preview | tagged, "…", full text on get |
| `recall/expand.py` excerpts, one-line cards | 600, 200, 3 aliases | lossy preview | tagged, "…", "+n more" |
| `recall/expand.py` packet budget and part bounds | 24000 (4000..200000), 5, 20, 12, 6, 10, 10 | selection | `omitted` for every part; `budget_chars` is the applied budget |
| `recall/search.py` `ENTITY_LIMIT`, `CLAIM_LIMIT`, facets | 10, 10, 20 | selection | `omitted {entities, claims, facet_entities}`; `considered` |
| `recall/search.py` limit | 1..200 | clamp | search warning, log |
| `recall/stats.py` `TOP_ENTITIES`, `TOP_PREDICATES`, `QUIET_LIMIT` | 20, 15, 20 | selection | `omitted` |
| `recall/reflect.py` candidate lists | `limit` 1..200 | selection | `omitted` |
| `store/notes.py` graph | `limit` 1..2000, depth 1..3 | selection, clamp | `omitted {nodes, edges}`, log |
| paged lists (notes, entities, revisions, changes) | `limit` | selection with paging | `next_cursor` / `cursor`, clamp logged |
| `store/queue.py` lease | 1..100 | clamp | `limit_applied`, log |
| `recall/processing.py` `RECENT_LIMIT`, `FAILED_LIMIT` | 20, 50 | selection (page window) | the queue's `failed` count is the total |
| `recall/processing.py` `ACTIVE_SCAN_LIMIT`, `DURATION_SCAN_LIMIT` | 500, 300 | scan bound | warning when reached; a duration not found is null |
| `store/queue.py` `MAX_FAILURES`, `store/notebook.py` `PROCESSOR_MAX_ITEMS` | 3, 200 | policy, validation | refused with a sentence, not clamped |
| `flows/build.py` `SECTION_CHARS` | 6000 | chunking (every section is read) | not lossy |
| dates `[:10]`, ids `[:20]` | | formatting | not governed (root ADR-0026 non-examples) |

`process/packets.py` and `process/context.py` (the processing packets) are classified in the W26 report; their
`omitted` lines land after lane W25.

## Consequences

A consolidation that meets an empty answer goes on and says what it could not do; the Processing page and the
API agree with `pending`. Some responses carry new fields (`left_out`, `notice`, `omitted`, `limit_applied`,
`topics_not_assigned`). Logs carry more warnings, each attributable to its module. New bounds must say which
class they are in, and a check must go red when their record is removed.

## Validation

`tests/test_consolidation_gaps.py` (the incident replayed with a scripted model: an empty answer once, twice,
labels unanswered, the skip list and the exclusion, the notice, the gaps status, over-length kept, the skip
log) and `tests/test_no_silent_drops.py` (topics, filing topics, clamps, the vector bound, graph, packet,
search, stats and reflect records); `web/test/processing.test.ts` (the notice and the state sentence). Each
check goes red when the record it checks is removed (mutation runs in the W26 report).
