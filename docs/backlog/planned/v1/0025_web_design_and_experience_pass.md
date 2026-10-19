# Web app: design and experience pass

## Summary
Give the web app the framework look the design deck verified and tighten its flows: kit-token-only
visual identity (near-black surfaces, Inter, fine borders, a restrained indigo, cyan and mint accent),
a Capture stream with one short acknowledgement per note, an Explore view whose entity panel opens on
"How it evolved", a Remember first screen that fits one window with "why this surfaced" and answers in
three layers, a small review inbox for matches the notebook made on its own, and accessibility
(text size, focus, words and symbols for every state, list equivalents, date fields).

## Why
Operator: "aesthetically pleasing user interface and clean intuitive flows / user experience". The
design deck (2026-10-19, untracked/reviews/03-deck-reflection.md) set the vocabulary (Capture, Recall,
Explore; Received, Organizing, Organized, Needs attention; original, interpretation, synthesis) and
the temporal composition (topic portrait with milestones). The live screenshots of 0008 showed noise:
the capture reply repeated what the links showed, links and chips all in the accent colour, a
Remember view several screens long.

## Scope
- In: `web/` (styles, shell, Capture, Explore, Remember, dialogs, mock, Playwright screens), the web
  section of docs/clients.md and the parts of docs/remembering.md the UI changes.
- Out: server code (gaps are recorded, not built), the iPhone and watch apps, new endpoints.

## Dependencies
0008 (the web app), the note read API as it is (docs/api.md), ui-kit 0.8.7 tokens and `AfTabs`.

## Expected outcomes
- Every colour is a kit token or a derivation (`color-mix`, relative colour); all kit themes work.
- Capture: Sending, Received, Organizing, then Organized or Needs attention, one at a time, as a word
  and a symbol; "Added to #lut · event · 2 links" expands into what was filed; previews; Enter saves.
- Explore: How it evolved, Changes, Open actions, Sources, Links; Picture or List; a date field;
  stable layout when the date changes.
- Remember: the first screen fits 1280x800; why this surfaced on cues and insights; Synthesis,
  Original and Interpretation drawn apart; Quick follows the typing; Standard and Deep show steps.
- To review: merges to keep or undo, shown only when there is one.

## Current code reality
Before: panel-chat thread with a long reply per note, the entity panel as one long column, Remember
as five stacked full-width sections, accent-coloured links and chips everywhere.

## Validation
`npm test` (61 tests, including design.test.ts and the extended copy rule), `npm run build`,
`node scripts/screens.mjs` against the mock (all checks pass; screenshots in
untracked/screens/web-v2/, three themes at 1280x800 and 400 px), screenshots reviewed by eye.

## Metadata
- Created: 2026-10-19
- Status: completed
- Completed: 2026-10-19
- Area: web

## Completion report
- Date: 2026-10-19
- Landed: commits 8903850 (goal 1, look), 236eb78 (goal 2, Capture), f1c0ec8 (goal 3, Explore),
  1150ef7 (goals 4 and 5, Remember and To review), 5bdc58b (goal 6, accessibility), 4308b3b
  (screens), plus the docs commit.
- Validation: vitest 61/61; build green; Playwright smoke and screenshots all checks passed.
- Notes and residual risks: endpoint gaps (no review endpoint, no confirm for a match, no
  closeMatch pairs, cues carry no evidence ids, the merged entry's own name is not readable) are in
  untracked/reports/W9.md; "Keep" in the review inbox is per browser; Inter is used only when
  installed (no font is bundled); near-black needs relative colour (older browsers keep the theme's
  page colour); llms-full.txt was not regenerated (not this lane's path).
