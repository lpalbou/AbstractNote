# ADR 0005: Eventual consistency: durable queue, priorities, processor automation

**Status**: Accepted  
**Date**: 2026-10-19

## Context
Notes arrive at any cadence; understanding costs model calls. The gateway has no job queue; its sanctioned scheduler is automations (serial occurrences, coalesced misfires). Local models serve one request at a time.

## Decision
Capture is immediate and durable (store, hash, enqueue, quick filing, answer). Understanding runs later from a durable priority queue in the notebook, drained by the `note-drain` flow started by a per-user processor automation (every two minutes, and on demand) or by the headless CLI. Priorities: urgent, high, normal, bulk (imports). Leases expire; failures retry with backoff; the third failure is shown to the user. Inline processing is a per-capture option for interactive clients.

## Consequences
No gateway worker thread is needed; throughput equals the model's; the queue state is visible in the UI; a worker thread remains a documented alternative (proposed 0020) if latency proves insufficient.
