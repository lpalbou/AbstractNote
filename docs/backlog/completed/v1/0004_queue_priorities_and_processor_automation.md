# Queue, priorities and the processor automation

## Summary
Implement the durable queue (`note_queue_lease`, `note_queue_ack`, `note_queue_stats`), priorities (3..0), leases with expiry, retry backoff (1 min, 10 min, 1 h; third failure = `failed`), and the processor/reflect automations creation (`processor ensure`, `POST /note/processor/ensure`).

## Why
Notes arrive at any cadence; understanding is eventual and ordered. The gateway has no job queue, and automations are its sanctioned scheduler (serial, coalescing). See cognition.md "note-drain and the queue".

## Scope
- In: queue table semantics, work-stealing safety (two drains cannot process the same note), bulk priority for imports, `note-drain` budget handling, status surfaced by `GET /note/queue`.
- Out: a gateway worker thread (not needed; recorded as a proposed alternative if automation latency proves insufficient).

## Dependencies
0001, 0005 (drain flow), 0007 (automation creation through the gateway API).

## Expected outcomes
- 100 queued notes with mixed priorities drain in priority then time order; a crashed drain's lease expires and the note is retried.
- The processor automation exists once per notebook and is idempotent to re-create.

## Current code reality
Gateway automations: `schedule@2`, `manual@1`, serial/coalesce (abstractgateway docs/automations.md). No queue primitive anywhere.

## Validation
`pytest tests/test_queue.py`; an integration test of `note-drain` headless with a scripted model.

## Metadata
- Created: 2026-10-19
- Status: completed
- Completed: 2026-10-19
- Area: core

## Completion report
- Date: 2026-10-19
- Landed: `store/queue.py`, `tools/queue.py` (8be997b); processor and reflect automations created by `POST /note/processor/ensure` (W2, W7: reflect first fires one interval after creation).
- Validation: tests/test_queue.py (priority/time/size order, concurrent leasers), hermetic smoke (run_now 200 then 409, drain, no failed runs).
- Notes and residual risks: ADR 0005. Residual: throughput is max_items per interval; a gateway worker remains proposed (0020).
- Reviews: untracked/reviews/01-design-review.md (design), untracked/reviews/02-gate-review.md (integrated head; fixes in W7 commits up to d5e894d).
