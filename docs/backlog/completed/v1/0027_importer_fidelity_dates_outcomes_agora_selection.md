# Importer fidelity: faithful dates, prompt authorship, agora selection

## Summary
Make imported notes say when things happened and who wrote them: memory files dated by their
origin session, Markdown files by their own `Created:`/`Date:` lines and their first add through
moves, a recorded `date_basis` on every note with a histogram in every report, prompt notes
authored by the operator alone with the model's answer as a separate outcome, and agora imports
selectable by kind and channel with retracted messages opt-in.

## Why
The first corpus evaluation (backlog 0011, untracked/eval/REPORT.md section 4.2, items 3-6) found
memory files dated by file mtime (12 of 30 off by up to 20 days), 255 of 601 Markdown files dated
by a repo's first commit while 58 carried an ignored `Created:` line and moved files lost their
history, 81 % of prompt-note text being the assistant's answer recorded with the operator as
author, and no way to pick agora kinds or leave channels out except by editing the database.

## Scope
- In: `src/abstractnote/importers/` (memory, Markdown, prompt, agora, report), the `abstractnote
  import` flags, `tests/test_importers.py`, `docs/importers.md`.
- Out: updating notes already imported (ids are unchanged, so a re-import is a duplicate; a fresh
  notebook gets the fixes), the processing context passing `outcome_author` to the model
  (process/context.py, another lane), `docs/api.md` CLI synopsis (another lane).

## Dependencies
- 0011 (the evaluation findings), 0008 (the importers).

## Expected outcomes
- Memory `occurred_at`: origin session first record, else front matter `modified`, else git first
  add, else mtime; `source.origin.date_basis` names the basis.
- Markdown `occurred_at`: front matter date, else `Created:` line, else `Date:` line near the top,
  else first add following moves, else mtime; `Completed:` kept in `source.origin.completed`.
- Prompt notes: `source.author` = the operator; `source.origin.outcome` + `outcome_author` = the
  model's answer and id; the answer still in the text after a separator naming the model.
- Agora: `--kinds`, `--include-channels`/`--exclude-channels`, `--include-retracted`; counts by kind.
- Every report (dry run too) has `date_basis` and `approximate_dates`.

## Current code reality
Before: `claude_sessions._memory_drafts` used `st_mtime`; `_front_matter` read top-level keys
only, so the nested `metadata.originSessionId` was never seen; `markdown._git_index` took the add
at the current path (a move restarted history) and never read in-file field lines; prompt text
was `prompt + "## Outcome" + answer` under the operator; agora had `--channels` only and always
skipped retracted messages.

## Validation
- `pytest -q tests/test_importers.py` (15 tests), each fix with a fixture test that goes red when
  the fix is removed (the rename chain was mutated once to confirm).
- Dry runs on the real corpora: memory files 71 (13 origin_session, 35 front_matter_modified, 23
  file_mtime); root backlog 552 (122 created_line, 39 date_line, 362 git_added, 29 file_mtime; 51
  with `completed`); agora `--kinds decision,thread` 1961 notes.

## Metadata
- Created: 2026-10-19
- Status: completed
- Completed: 2026-10-19
- Area: importers, CLI

## Completion report
Landed in five commits (memory dates, Markdown dates, prompt outcome, agora selection, date-basis
histogram) plus docs. Ids did not change anywhere: every source key is the same, so re-importing
into an existing notebook reports duplicates and leaves those notes as they were; the fixes reach
a fresh notebook. Details and seams: untracked/reports/W11.md.
