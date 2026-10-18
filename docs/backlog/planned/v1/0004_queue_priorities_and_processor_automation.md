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
- Status: planned
- Area: core
