# Capture, attachments and de-duplication

## Summary
Implement `note_capture` and `note_file`: normalise text, hash, exact de-dup (same note_id), store blobs once per sha256, read attachments from gateway artifacts or local paths, extract text (plain, Markdown, PDF via AbstractCore media, images via a vision model when configured, audio via STT when configured), compute note embeddings, find similar notes, enqueue with priority, and record provenance/location.

## Why
Capture must be immediate, durable and idempotent so clients can fire and forget at any cadence. See data-model.md "Identity and de-duplication".

## Scope
- In: `abstractnote.ingest` (normalise, hash, blobs, extractors with explicit `extracted_by`), `abstractnote.tools.capture`, near-duplicate link `ex:revisits` (cosine >= 0.92, same kind), size-based sectioning metadata.
- Out: the quick filing model call lives in the flow (0005); the tool only stores its result (`note_file`).

## Dependencies
0001. AbstractCore media helpers for PDF/image/audio text (optional extras; missing extractor = attachment stored, `extracted_by = none`, warning returned, never a silent drop).

## Expected outcomes
- Re-sending the same text returns `status = duplicate` with `first_saved_at`.
- Same file attached to two notes stores one blob and links the attachments.
- A 5 MB Markdown note is stored whole and sectioned for processing.

## Current code reality
Nothing yet. Prior art: the old `smartnote/` package chunked text and extracted per chunk; its artifact-first storage is replaced by the notebook blobs plus gateway artifact refs.

## Validation
`pytest tests/test_capture.py`; a test that an unknown attachment type is kept with a warning.

## Metadata
- Created: 2026-10-19
- Status: completed
- Completed: 2026-10-19
- Area: core

## Completion report
- Date: 2026-10-19
- Landed: `ingest/`, `tools/capture.py` (`note_capture`, `note_file`), idempotent ids (idempotency key, import source key), blobs by sha256, extractors with explicit `extracted_by`, revisits by same words and by cosine (fb3ca74, 5e53fcb, 23e15e8 for `about`/`answer`).
- Validation: tests/test_capture.py; B3 path refusal through the runtime executor; hermetic smoke (attachment capture, `/etc/hosts` refused, 413).
- Notes and residual risks: Residual: image description and speech extractors need a configured vision/STT model (none by default); notes captured by CLI/importers stay untitled until processed.
- Reviews: untracked/reviews/01-design-review.md (design), untracked/reviews/02-gate-review.md (integrated head; fixes in W7 commits up to d5e894d).
