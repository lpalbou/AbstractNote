# Corpus evaluation: the framework's own history

## Summary
Import the Claude Code sessions, the memory files, the root ADRs/backlog and the agora decisions into a scratch notebook, process a representative subset with the recommended small model, and write an evaluation report: entity and relation quality, de-duplication precision, examples of evolution (then-versus-now), forgotten items resurfaced, throughput and cost.

## Why
The operator asked for this exact test case; it validates the design on real, messy, multi-month data.

## Scope
- In: headless processing with the GPU lock protocol; sample-based manual grading; the report under `untracked/eval/`; fixes fed back into prompts/schemas.
- Out: processing every note (hours); a full run can continue in the background after the report.

## Dependencies
0005, 0006, 0010.

## Expected outcomes
- A report with measured numbers and at least five concrete "we had forgotten this" findings with citations.

## Current code reality
Models available locally: mlx-community/Qwen3.6-35B-A3B-4bit (cached), embeddings via LM Studio or the gateway embedding route.

## Validation
The report exists, numbers reproduce from the notebook, graded samples are listed.

## Findings (2026-10-19, first run)
- Imported 3987 notes in 476 s with no importer error; processed 29 (8 empty), 1 failed. The model was too slow for the planned 300-note subset: median 63 s per note, runaways up to 845 s, other GPU users on the machine. Notebook and evidence: `untracked/eval/` (W5 report).
- Quick recall already works at the note level (expected note first for 13 of 15 questions, 77 % of top-5 hits relevant); the graph does not yet carry signal.
- Prompt and schema: the required-summary rule fails 37 % of attempts and fires on wrong reuses; 6 of 9 sampled reuses are wrong and they add wrong aliases; one topic (`smartnote`) on every note; `schema:mentions` is 30 % of claims; 10 of 40 sampled entities are junk; certainty is always `stated`; names leak in from the orientation context; with thinking off the extraction comes back empty.
- Runner and store: every model call inherits 81920 output tokens from the model card; the length retry repeats a clamped generation; link prompts have a median of 22k tokens (candidate cards 55 %, static part 6 %); structured output takes AbstractCore's native lane, which reports no usage, finish reason or prompt cache; prompts begin with a per-call timestamp, so no prefix is shared.
- Importers: memory files are dated by mtime (up to 20 days off); 42 % of Markdown notes take the root repo's first-commit date; prompt notes are 81 % assistant text recorded under the operator's name; the repr and `--embed-*` bugs were fixed at 23e15e8.
- Forgotten or changed: the Team-page verify-transcript was dropped after 2026-07-14; the protocol pin planned in July did not prevent the agora/0.3 vs 0.4 skew in August; the run_input provenance defect was flagged on 07-15 and never fixed; the operator's composer line-reset request has no visible close; the MLX eject leak conclusion was rewritten in place; ADR 001 is superseded but its claims still read as current.

## Metadata
- Created: 2026-10-19
- Status: planned
- Area: evaluation
