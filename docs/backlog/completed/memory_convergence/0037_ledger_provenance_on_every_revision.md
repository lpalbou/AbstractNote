# Ledger provenance on every revision, projected assertion and closure

## Summary
Every statement AbstractNote projects into AbstractMemory, and every closure it records, names where it came
from in AbstractRuntime's ledger: the tool step that applied the revision (its ledger row when the runtime
reports it), the step that captured the note, the note's body and its attachments as artifacts. Revisions
persist that address so a replay or a rebuild writes the same provenance. The note body becomes an artifact of
the capture run when an artifact store is available.

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
  revision row; the capture step on the note and the note body as an artifact (`notes.body_artifact_json`);
  the tools learn their run and step from AbstractRuntime's step context; the gateway binding hands the tools
  the plane's artifact store.
- Out: writing through `MemoryGraph.apply_revision` (0033 phase 2); grounding the revisions and notes written
  before this change, and writing their bodies as artifacts (the one-time projection, operator decisions 1 and
  4); the reader (model id) of an extraction, which no flow passes yet; naming the extraction `llm_call` steps.

## Dependencies
AbstractMemory ADR 0003 and its contract (`abstractframework/untracked/memory-transition/contract.md` §1, §2,
§7). AbstractRuntime's step context and `LedgerStore.get_record` (branch `feat/memory-graph-facade`, 76b1319 +
7b0d031, its ADR 0009 and item 0853), to be released as 0.14.0; AbstractMemory 0.4.0 for the parity shape.

## Expected outcomes
A statement learned from a note captured and processed inside a runtime carries
`sources = [step of the applying tool, step of the capture, artifact of the note body, artifacts of its
attachments]`, `actor = "abstractnote"`, and `extra = {note_id, revision_id, evidence_quote, recorded_at}`; its
closure carries the same shape with the closing revision and the window's new end in `extra`. A rebuild writes
the same provenance.

## Current code reality
Before (`main` b8b0f7b): provenance was the flat `{source: "abstractnote", note_id, revision_id, run_id,
evidence_quote, recorded_at}`, `run_id` never set by processing; closures carried `{revision_id, note_id,
valid_until}`; the note tools received no run or step; `notes.run_id` existed but `note_capture` had no `run_id`.

After (branch `feat/memory-graph-thin-client-1`):
- `abstractnote/ledger.py`: the ledger address `{run_id, seq?, step_id?}` (`step_address`, checked, never
  guessed), source refs (`step` when the row is known, else `run` with the step id in `extra.step_id`;
  `artifact`), `provenance(rev, **extra)` in the contract shape, `note_columns` (a note is never filed under a
  run its step does not belong to), `runtime_step_reader()` over
  `abstractruntime.core.step_context.current_step_context()` (an AbstractRuntime without it fails at bind time).
- `store/kg.py`: assertions and closures carry only the six keys; `abstractmemory.graph.Provenance.from_dict`
  reads them unchanged (tested).
- `store/notebook.py`: `Notebook.step_reader` and `Notebook.artifact_store`, set by `tools.bind`; a revision,
  a capture or an insight written with no address of its own is stamped with the runtime step it runs in; the
  revision row keeps the address (`ledger_run_id`, `ledger_seq`, `ledger_step_id`) and the note's sources
  (`sources_json`) so a replay or a rebuild projects the same provenance.
- `ingest/capture.py`: the note keeps its capture step (`run_id`, `ledger_seq`, `ledger_step_id`) and, with an
  artifact store, its body as an artifact of the capture run (`store_text`, tags `kind=note_body`, `note_id`,
  run-namespaced id) in `body_artifact_json`; a store that fails leaves a warning and a log line, never a lost
  note; no store (CLI, importers): no body artifact.
- `tools/__init__.py` `bind(..., artifact_store=None)`; `gateway/tools.bound_tool_map` passes the host's artifact
  store (the gateway already hands it over: `abstractnote_seam.note_tool_map`); `NoteRunner.local(...,
  artifact_store=...)` shares one store between the runtime and the tools.
- Schema: no version change (lead decision, memory transition decision 1 is the operator's): the seven columns
  are nullable, added on open through `_ADDED_COLUMNS`, NULL on older rows.
- The tools take no `run_id`/`seq`/`step_id` arguments: a ledger address a model could pass is one it could
  invent. `note_insight_write` keeps its optional `run_id`, which must name the step's own run when both exist.

How a tool learns its run and step (the open question of this item), verified 2026-10-27 on AbstractRuntime
`feat/memory-graph-facade` 7b0d031 and AbstractGateway `main` 227c689:
- `Runtime._execute_effect_with_retry` (`core/runtime.py` ~3792-3856) keeps the `seq` that
  `LedgerStore.append` returns for the attempt's STARTED record and installs
  `effect_step_context(StepContext(run_id, step_id, seq, node_id, attempt, effect_type))` around the handler
  call (`_invoke_registered`), on the thread that runs the handler.
- The TOOL_CALLS handler calls the executor synchronously (`integrations/abstractcore/effect_handlers.py`
  ~3089-3125: `tools.execute(...)` / `execute_approved(...)`); the gateway's executor chain
  (`hosts/bundle_host.py` ~1878-1889: `note_tool_map` → `MappingToolExecutor`, under `McpRoutingToolExecutor`
  and the approval executor) calls it in the same thread.
- The two thread hops inside `MappingToolExecutor` both carry the context: `_call_with_timeout`
  (`tool_executor.py` ~159-171, a bare `threading.Thread` when a tool timeout is set) runs the tool under
  `contextvars.copy_context()`, and the parallel batch (`tool_executor.py` ~595-606, `ThreadPoolExecutor`)
  submits `copy_context().run` per call (and the note tools are not in `_PARALLEL_SAFE_TOOL_NAMES`, so they run
  sequentially anyway). No process hop exists on this path.
- Finding: the tool sees the full step context, `seq` included; nothing is lost between the runtime and the tool.
  Before 7b0d031 (the step context defined but not installed) the tool saw nothing, and the end-to-end test fails
  there (`notes.run_id` None), which is the check that it depends on the runtime and not on a guess.

## Validation
- `tests/test_ledger_provenance.py` (16 tests): the projected claim, name and closure in the contract shape and
  read by `abstractmemory.graph.Provenance.from_dict` (parity); a step without its row is a `run` ref with the
  step id in `extra`; a revision outside any run names no source; the address is checked; the runtime step is
  stamped and kept on the revision row; a given address wins; `rebuild()` projects the same provenance; a
  notebook without the columns opens at schema 2 with NULLs and unchanged provenance; the body artifact with a
  real `FileArtifactStore` (content, run namespace, tags, no second artifact for a duplicate), none without a
  store, a failing store keeps the note; the bound tools read AbstractRuntime's real step context (with and
  without its `seq`); end to end, `NoteRunner.local` + `ScriptedLLM` on a real Runtime runs `note-capture` with
  `note-process` inline (also through the executor's timeout thread): the note row names the capture run's
  `note_capture` STARTED row, the revision row names the child run's `note_apply_revision` STARTED row (both
  read back with `runtime.ledger_store.get_record(run_id, seq)`), the body artifact is the capture run's, and the
  projected claim's `sources` are exactly `[step:<process run>#<seq>, step:<capture run>#<seq>,
  artifact:<body>]`.
- Mutants (each turns the file red): `bind` without the step reader (3 failed), `provenance` without the
  applying step (6 failed), capture without the body artifact (4 failed); the end-to-end against AbstractRuntime
  76b1319 (no step context installed) fails.
- Full suite: 440 passed, 24 skipped (main in the same interpreter: 424 passed, 24 skipped).

## Metadata
- Created: 2026-10-27
- Status: completed
- Completed: 2026-10-27
- Area: store, tools, gateway seam

## Completion report
- Date: 2026-10-27
- Landed: branch `feat/memory-graph-thin-client-1` (not merged; not pushed).
- Notes and residual risks:
  - The floors `abstractmemory>=0.4.0` and `abstractruntime>=0.14.0` are written ahead of those releases; a
    release of this branch waits for both on PyPI (root ADR-0034). Until then it resolves only against the
    editable feature branches.
  - Statements from notes captured outside a runtime without an artifact store (the CLI, importers, tests) name
    no source and would be refused by `MemoryGraph.apply_revision` (`ProvenanceRequired`): phase 2 (0033) must
    decide their ground (for importers: an import run, or the imported file as an artifact).
  - Existing notebooks keep NULL ledger columns; their bodies are not artifacts (operator decision 4).
  - The extraction and linking `llm_call` steps (and the reader model) are not named; the applying step's
    STARTED row holds the extraction and linking verbatim as the tool's arguments, which is what a
    reconstruction reads today.
