# Processing quality and speed on a local model

## Summary
Make `note-process` bounded and fast on a local model (mlx-community/Qwen3.6-35B-A3B-4bit on Apple silicon)
without losing quality: verify the structured-output path, structure every prompt as a static cacheable prefix
plus a dynamic suffix and prove the prompt-cache hit, bound the linking packet, make the entity summary rule
soft, choose thinking per step, and reach a median under 40 s per typical note.

## Why
The corpus evaluation (0011, untracked/eval/LOG.md) measured 2-4 minutes per note with thinking on, link
prompts of 78-92k characters (25 mentions x 5 candidate cards with active claims), 6 of 7 empty extractions with
thinking off, and the required-summary rule failing 4 of 8 notes. At that speed a 4k-note notebook takes days.

## Scope
- In: `src/abstractnote/process/` (prompts, schemas, context, resolve, revise, merge, vocabulary),
  `src/abstractnote/flows/` (build, graph, json, runner), `src/abstractnote/tools/process.py`, their tests,
  docs/cognition.md; measurements on a copy of the 0011 evaluation notebook.
- Out: the store, ingest, recall, web, watch and gateway lanes; changes inside AbstractCore or AbstractRuntime
  (gaps there are reported, not patched).

## Dependencies
0005 (processing), 0006 (flows), 0011 (the evaluation notebook and its findings).

## Expected outcomes
- Structured output path recorded (which AbstractCore strategy runs on MLX here, and why thinking off emptied
  extractions), with the cause fixed when it is ours.
- Prompts split into a static system prefix and a dynamic user suffix; per-call `prompt_cache` evidence over 20
  consecutive notes.
- Link prompt under 12k characters for a typical note, under 25k worst case; no truncation of the note text.
- A missing summary is a warning plus a placeholder, never a failed note.
- Thinking chosen per step and documented.
- Median under 40 s per note of at most 3k characters; supersession test and revision quality unchanged.

## Current code reality
See the completion report below when done (before/after tables live in untracked/reports/W8.md).

## Validation
`pytest -q tests` green; regenerated flow JSON (`python -m abstractnote.flows.build --check`); measurements on the
same 100-note sample before and after.

## Metadata
- Created: 2026-10-19
- Status: planned
- Area: processing
