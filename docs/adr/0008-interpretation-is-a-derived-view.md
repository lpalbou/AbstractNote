# ADR 0008: Interpretation is a derived view; nothing understood is final

**Status**: Proposed (operator ruling of 2026-10-20 pending the build and its gate)  
**Date**: 2026-10-20

## Context

A note is understood against what the notebook knows when it is processed. Notes do not arrive in the
order they happened: archives from earlier years, notes written offline, files found later, another
project's history. Ordering alone (ADR 0003's time model, the `claim.insert_before` placement of
2026-10-20) protects the timeline but not the meaning: a note from day 20 understood without the
notes of day 5 created its own entities, chose its own claims and summary, and may have been read as a
belief, a decision or a fact when it was a plan, a quote or a consequence. When day 5 arrives, the right
behaviour is to understand day 20 again with its foundation, not to file day 5 beside it. Any ingest can
change the graph and the meaning of knowledge, including its interpretation over time.

The adversarial gates of 2026-10-20 (untracked/reviews/retro-A.md, retro-B.md) measured the gap on real
runs: retro claims arrive as `new` beside the later beliefs they precede (intern, head of research and
director all believed at once), summaries written by an earlier note use later knowledge, an earlier
reinforcement moves nothing back, an alias equal to a live entity's name still splits it, communities
never recompute after an import, uploads without metadata are dated today, a note captured inline during
a drain is understood twice, and the structured-output lane corrupts one link answer in three.

## Decision

1. **Interpretation is a derived view.** A processing revision is a function of the note and of the
   context it was understood against, and it can be recomputed. "Understood" means "understood against
   the context recorded in the revision", never "final". The revision log stays the truth (ADR 0003):
   a re-interpretation is a new revision (`note.reinterpret`) that supersedes the previous one's effects
   (its claims close with the closure kind `reinterpreted`, its tasks and mentions are replaced, entities
   it alone created and that nothing references any more are retired, its topics and mentions are
   soft-removed so the as-received axis still shows what the notebook believed before). The inverse of every
   op is explicit (a table in data-model.md), and a re-interpretation that keeps a statement with the same
   subject, predicate and object but another certainty or window updates that claim rather than adding a
   second one. In AbstractMemory the closure kind is `reinterpreted` (an additive kind, released before
   AbstractNote depends on it); it is never written as a retraction.
2. **Context as of the note's date.** The packets a note is understood with show the notebook as it was
   known at the note's date (claims and summaries observed on or before it) and, separately and labelled,
   what was recorded LATER. The model may use the later block only to say that a statement is an earlier
   state of a later belief (`earlier_state_of`, placed before it) or that it reinforces an existing belief
   from earlier evidence (the belief's start, its relation's first_seen, move back to the note's date). A
   summary written for an earlier date describes the entity as of that date and is kept in the history;
   the current summary is re-derived from the dated history, never from the last note that happened to
   be processed.
3. **Every revision records what it depended on**: the entities, claims and summaries it saw (and the
   `as_of` date), so that a new earlier note can name exactly which later interpretations it invalidates.
4. **Invalidation is dependency-bounded and forward-only.** When a note with date t is applied and
   touches entities E, the later notes whose revisions USED E (an entity the revision reused, created,
   or made the subject or object of a claim or task; not merely a candidate the packet showed), or
   whose mentions now match E's names or aliases, are marked for re-understanding with the reason and
   the note that caused it. Notes earlier than t are never affected (their context excludes later
   knowledge); two notes with the same date are ordered by their received time. A person's re-date or
   archive of a note marks dependents forward from the earlier of the old and the new date. A pass
   re-understands marked notes in ascending date, each once; a re-interpretation may mark later notes
   again, never earlier ones, so the pass terminates, and a second pass without new earlier notes makes
   no model call (the unchanged check is a structural comparison of the ops, not a model answer).
5. **Re-understanding is a visible consolidation stage** ("Re-understanding 12 notes", with progress,
   ETA, what changed and what did not), scheduled like the rest of the consolidation and triggerable.
   The count and the reasons are readable before the run; the stage runs under the consolidation's
   budget contract (ADR 0007: a budget stop leaves `left_out` equal to what is still pending, never a
   silent end); a claim that a live note closed meanwhile is refused with a warning, never dropped;
   communities recompute after every import batch or drain that made them due; themes, then-and-now and
   summaries are rebuilt views of the dated history.
6. **Identity across time.** A mention whose name or alias equals a live entity's canonical name or
   alias is always a candidate; when the model does not reuse it, the pair lands in review as a close
   match; aliases met in an earlier archive persist on the reused entity. Models still decide (ADR 0004).
7. **Dates come with their basis.** An upload without metadata takes the date the model reads in the
   document (`document_date`, basis `content`, approximate when only a month or year), else the file's
   date, else the moment of capture, and the capture answer says which. A person's re-date crossing a
   belief boundary re-places the note's claims, never leaving overlapping beliefs.
8. **Nothing is understood twice or silently.** A note being understood inline is not leased by a
   drain; a model answer that is structurally corrupt (JSON fragments inside a string field, a decision
   that names no candidate) is an invalid answer: it is retried once and then recorded as a failed
   attempt, never applied in part.

## Consequences

Processing costs more on a backfill: a 2022 archive touching a project re-understands the later notes
of that project, bounded by dependency, in history order, with progress shown. Every number in the
graph can change after an ingest, and the then-and-now, themes and summaries must be read as views
over the dated history, which is what makes "how the notebook understood this over time" answerable.
The revision log grows (one revision per re-interpretation); the as-received axis makes the growth
useful. The schema gains the dependency record and the re-understanding marks (a migration flag marks
revisions written before the record existed: they are re-understood on their first mark). AbstractMemory
gains the additive closure kind `reinterpreted` (a change and release of abstractmemory that precedes the
AbstractNote release depending on it); closures of that kind are not turning points in then-and-now.
Adversarial review: untracked/reviews/retro-design-review.md (2026-10-20).

## Validation

The acceptance suite is the one of untracked/reviews/retro-A.md (10 behaviours) extended by the
foundation scenarios of 2026-10-20 (a later note whose meaning changes once its earlier foundation
arrives; a later note that created its own entity for something defined earlier; a task closed later
that an earlier note shows was another task), all graded on a separate collection against the live
gateway, plus no-model proofs for placement, invalidation and convergence (a pass terminates and a
second pass without new earlier notes changes nothing).
