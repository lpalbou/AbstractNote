# Ledger provenance on every revision, projected assertion and closure

## Summary
Every statement AbstractNote projects into AbstractMemory, and every closure it records, names where it came
from in AbstractRuntime's ledger: the tool step that applied the revision (run, and the ledger row when the
runtime exposes it), the run and step that captured the note, the note's body and its attachments as
artifacts. Revisions persist that address so a replay or a rebuild writes the same provenance. The note body
becomes an artifact of the capture run when an artifact store is available.

## Why
AbstractMemory ADR 0003 (decisions 8-11): every memory is sourced or derived, the source reference is typed
(`SourceRef`: `run`, `step`, `artifact`, ...), and the source of a note is an artifact in the ledger. The audit
of 2026-10-27 (`abstractframework/untracked/memory-transition/audit/abstractnote.md` §8) found that none of the
21,951 triples of the operator's notebook names a run, that `note_apply_revision` drops the run, and that the
Processing page re-discovers the run that applied a revision by scanning 300 runs.

## Scope
- In: the provenance of `claim_assertion`/`label_assertion` and of the journal `ClosureRecord`s in
  `store/kg.py` in the contract shape (`untracked/memory-transition/contract.md` §2: `sources`,
  `derived_from`, `actor`, `reader`, `reason`, `extra`); the ledger address and the note's source refs on the
  revision row (schema 3); the run and step of the capture on the note and the note body as an artifact
  (`notes.body_artifact_json`); `note_apply_revision`, `note_capture` and `note_insight_write` accept `run_id`,
  `seq`, `step_id`; a revision applied inside a runtime tool step is stamped with that step when the caller
  passed nothing; the gateway binding hands the tools the plane's artifact store.
- Out: writing through `MemoryGraph.apply_revision` (0033 phase 2); grounding the revisions and notes written
  before this change (the one-time projection, operator decision pending); the reader (model id) of an
  extraction, which no flow passes yet.

## Dependencies
AbstractMemory ADR 0003 and its contract (`abstractframework/untracked/memory-transition/contract.md` §1, §2,
§7). The ledger row (`seq`) of a tool step needs an AbstractRuntime change (see Current code reality).

## Expected outcomes
A statement learned from a note captured and processed inside the gateway carries
`sources = [run or step of the applying tool, run or step of the capture, artifact of the note body,
artifacts of its attachments]`, `actor = "abstractnote"`, and `extra = {note_id, revision_id, evidence_quote,
recorded_at}`; its closure carries the same plus `revision_id`. A rebuild writes the same provenance.

## Current code reality
`main` b8b0f7b: provenance is the flat `{source: "abstractnote", note_id, revision_id, run_id, evidence_quote,
recorded_at}`, `run_id` never set by processing; closures carry `{revision_id, note_id, valid_until}`; the note
tools do not receive any run or step; `notes.run_id` exists but `note_capture` has no `run_id`.

How a tool learns its run and step in this stack (AbstractRuntime 0.13.1, branch
`feat/memory-graph-facade` 93c45b4): a `tool_calls` node calls `ToolExecutor.execute(tool_calls=...)` with the
tool calls only; no run or step reaches the tool's arguments, and a VisualFlow node cannot read the run id
(`get_context` returns `context`, `task`, `messages`). The runtime does register every effect attempt as an
`InflightEffect(run_id, node_id, step_id, ...)` in a ContextVar (`abstractruntime.core.effect_cancellation.
current_inflight_effect()`), and the tool executors copy the context into their worker threads, so a tool
reads its own `run_id` and `step_id` there. The ledger row number is not available: `LedgerStore.append(rec)`
returns nothing and `StepRecord` has no `seq`, so neither the STARTED row's `seq` nor any accessor for it
reaches the in-flight effect. The runtime change that closes it: `append` returns the row's `seq` (SQLite: the
`seq` it inserted; JSONL: the line index), `InflightEffect` carries it, and a public accessor on the runtime's
facade (`current_step() -> {run_id, seq, step_id}`) replaces the module-level read. Until then a tool step is
a `run` ref with `step_id` in `extra`, never a `step` ref with a made-up `seq`.

## Validation
- A revision applied with a run and step carries them into the triple's provenance and the closure's; the
  test goes red when the stamping line is removed.
- The headless flows (`NoteRunner`, real runtime, ScriptedLLM) stamp the run of the `note-process` step on a
  processed note's statements and the capture run on the note, with no argument passed by the flow.
- A capture with an artifact store stores the body as an artifact of the run (a real `FileArtifactStore`);
  without one the capture still works and the missing artifact is logged.
- `rebuild()` replays the same provenance; a schema-2 notebook opens and upgrades to schema 3.

## Metadata
- Created: 2026-10-27
- Status: planned
- Area: store, tools, gateway seam
