Fixtures for the watch client tests. Format: {"request": {"method", "path", "body"?}, "status", "body"}.

- recorded/: answers RECORDED from the hermetic gateway by lane W2 (copied unchanged from
  abstractnote/untracked/fixtures/gateway/ on 2026-10-19; the gateway's own copies are scrubbed of
  scratch paths). These are the S5 check (review 01): RecordedFixtureTests replays them through the
  live client. When W2 records again, copy the files over these (the test compares the two when
  the untracked folder exists and fails on drift).
- the files at this level: hand-written on 2026-10-19 from docs/api.md, kept only for what the
  recordings do not show yet (insight and cue items, acknowledging, closing a loop, a run waiting
  on a person, a failed run).
