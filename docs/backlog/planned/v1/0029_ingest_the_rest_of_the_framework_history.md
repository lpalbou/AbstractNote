# Ingest the rest of the framework's history into the operator's notebook

## Summary
The operator's notebook on the local test gateway holds 503 notes: 3 captured live and 500 imported, of which 200 are
understood in the first pass (the operator limited the pass to 200 on 2026-10-19; the other 300 stay queued at priority 0) (72 memory files, 49 ADRs, 120 agora decisions, 60 operator messages, 199 typed prompts spread
over July–October). The remaining 3,510 imported notes (other agora threads, claims and work items, subagent and
session summaries, the root backlog) were removed unprocessed on 2026-10-19 at the operator's request, to keep the
first pass to 500 notes. Re-import and process them later, in priority order, once the 500 are judged.

## Why
Speed and judgement first: 500 fully understood notes show whether the graph is right before hours of model time
go into the rest. The operator did not ask for the whole history yet.

## Scope
- In: the same importers with the same source keys (ids are stable, so the 503 existing notes count as duplicates and
  nothing is doubled); priority 0 for the bulk; process in batches with a drain run (`thinking: off`) and grade a
  sample per batch; keep the evaluation report's findings (untracked/eval/REPORT.md) as the rubric.
- Out: new importers; processing through the 2-minute processor automation (too slow for thousands of notes).

## Dependencies
0011 (evaluation rubric), the gateway with the AbstractNote seam (0.15.11), a filing model pinned per user.

## Expected outcomes
Every remaining source note present with its true date and provenance, understood in batches, with a per-batch
quality sample recorded here.

## One command to resume the full ingest (run on the gateway host; NB = the user's notebook directory, GW/TOKEN = the gateway and the user's token)

```
NB=<data>/users/default/<user>/runtime/abstractnote; E="--embed-base-url http://127.0.0.1:1234/v1 --embed-model text-embedding-qwen3-embedding-0.6b"; abstractnote import agora /Users/albou/.agora/agora.db --kinds claim,work,thread --notebook $NB $E && abstractnote import claude-sessions /Users/albou/.claude/projects/-Users-albou-tmp-abstractframework --notebook $NB $E && abstractnote import markdown /Users/albou/tmp/abstractframework/docs/backlog --notebook $NB $E && curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"bundle_id":"abstractnote","flow_id":"note-drain","session_id":"drain-all","input_data":{"max_items":5000,"budget_s":86400,"thinking":"off","session_id":"drain-all"}}' $GW/api/gateway/runs/start
```

(Already-imported notes are duplicates and are skipped; the drain processes everything queued, highest priority first; expect roughly 35 s per short note and up to 15 min per long document on an M5 Max with Qwen3.6-35B-A3B.)

## Current code reality
Commands that re-create the removed set (notebook = the user's plane `<data>/users/default/<user>/runtime/abstractnote`,
embedder flags = the notebook's pin):
`abstractnote import agora /Users/albou/.agora/agora.db --kinds claim,work,thread --notebook NB <embed flags>`,
`abstractnote import claude-sessions /Users/albou/.claude/projects/-Users-albou-tmp-abstractframework --notebook NB <embed flags>`
(summaries, subagents and titles come with the prompts; the prompts already present are duplicates),
`abstractnote import markdown /Users/albou/tmp/abstractframework/docs/backlog --notebook NB <embed flags>`,
then `POST /api/gateway/runs/start {bundle_id: abstractnote, flow_id: note-drain, input_data: {max_items: N, budget_s: S, thinking: "off"}}`.
A pre-trim copy of the notebook index is at untracked/local/note-before-trim.sqlite3.bak (local only).

## Validation
Counts after import match the import reports; a 20-note blind grading per batch; no failed notes left in the queue.

## Metadata
- Created: 2026-10-19
- Status: planned
- Area: evaluation
