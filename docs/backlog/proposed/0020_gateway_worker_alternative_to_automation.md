# Gateway worker as an alternative to the processor automation

## Idea
If the two-minute automation cadence proves too slow for interactive use, add a per-plane daemon worker in the gateway modelled on the email worker that drains the queue with a nudge from `note_capture`. Keep the automation as the default; measure first (0011).

## Status
Proposed; not an implementation commitment. Promote to planned with a code-reality section when scheduled.

## Metadata
- Created: 2026-10-19
- Status: proposed
