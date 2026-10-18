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

## Metadata
- Created: 2026-10-19
- Status: planned
- Area: evaluation
