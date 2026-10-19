# Remembering

Search engines answer queries. Memory answers cues. AbstractNote's recall is built around the
ways people (and agents) actually try to remember, and around how much effort they are willing
to spend. This page explains the design that the **Remember** view, the recall tools and the
`note-recall` flow implement.

## How we remember

| Cue | What you have | What you want | AbstractNote answer |
|---|---|---|---|
| Time | "around the release in September", "last winter" | what was going on then | the day and period views: notes, revisions, entities touched, what you believed then |
| Person or thing | "Dana", "the LUT project" | everything you know, and how it changed | the entity card as of a moment, the claims timeline, then-versus-now |
| Fragment | three words you are sure were in the note | the note | quick recall: full-text plus vector, the entities and statements grouped under the notes that carry them, each entity with its first and latest date |
| First mention | "when did we first talk about X?" | the oldest note | quick recall in earliest order: the notes that match X, oldest first |
| Feeling or shape | "that idea I was excited about", "a warning I wrote myself" | the note or the insight | kinds and topics as filters, insights as first-class notes |
| Association | "it came up together with X" | the neighbours | graph expansion from the hits, relations with first and last seen |
| Absence | "did I ever decide this?" "what have I dropped?" | the gap | open loops, contradictions, resurfacing, the reflect insights |
| Evolution | "what did I used to think?" | the arc | earliest stance, latest stance, turning points with the notes that caused them |

Agents have the same needs with different vocabulary: an agent asked to continue work needs the
latest decision and the reason it changed; an agent starting a task needs the entities involved
and their current state; an agent writing a report needs the timeline with citations. The same
structures serve both, which is why every answer is returned as structured JSON with note ids as
citations, and the web app renders that JSON.

## Effort levels

| Level | Budget | What runs | Returns |
|---|---|---|---|
| **quick** | under a second, no model | full-text (FTS5, BM25) plus vector search over notes, entities and claims, with a relevance floor; entities and statements grouped under the notes that carry them; relevance or earliest order; facets | hits with excerpts, matched entities with their first and latest date, matched claims, facets |
| **standard** | seconds, one model call | quick retrieval, then graph expansion (entity cards as of the requested moment, claim timelines, neighbours, then-versus-now pairs), then one synthesis | an answer with citations, the evidence, the evolution summary, open loops, follow-ups |
| **deep** | minutes, several model calls, runs in the background | a mining plan (sub-questions, time slices, entities, contradictions), several retrieval rounds with findings per slice, a dossier | the dossier as an answer and as a stored `synthesis` note; the web app and the watch show it when it is ready |

The level is a parameter everywhere: the Remember view has an effort dial, the watch asks at
standard by default, agents pass it explicitly, and the CLI takes `--effort`. A deep search is
never blocking: the run starts, the client gets a run id, and the dossier appears as an insight.

### Quick relevance

Ranking fuses full-text and vector ranks, which orders hits but cannot tell a good hit from the best
of a bad lot. So every hit also gets a relevance from 0 to 1: the share of the question's words it
contains, each word weighted by how rare it is among your notes (the square of its inverse document
frequency, so "Tailscale" counts far more than "when" or "first"; a word no note contains counts for
nothing), raised but never lowered by the vector similarity. An entity or a statement is shown only
when its relevance reaches 0.5 and one of the returned notes carries it (the note mentions the
entity, or made or repeated the statement); it is shown under those notes, and an entity with the
first and the latest date among the matching notes that carry it. Earliest order (`order=earliest`)
lists the notes whose relevance reaches 0.75 oldest first: search for the subject ("Tailscale
HTTPS") to answer "when did we first mention it". The thresholds are `HIT_MIN_SCORE` and
`EARLIEST_MIN_SCORE` in `abstractnote.recall.search`.

## The Remember view

The Remember view opens on what changed and lets you dig from there. Its first screen (since you
last looked beside the cue cards, then the ask bar) fits a 1280x800 window; the rest follows below.

1. **Since you last looked** (the default screen): what the notebook learned since your last
   visit: new entities, beliefs that were replaced ("Replaced") or withdrawn ("Withdrawn"), loops
   that closed, insights, notes that failed to process. It is a system-time delta from a cursor, so it is the same call an agent
   makes at the start of every session (`GET /note/changes?since=`). Each item opens its subject.
   The web app shows it as a compact list, one line per change with a word for its kind
   (Replaced, Withdrawn, Closed, Needs attention, New); new notes fold into one line that opens.
2. **Cue cards**: a few prompts the reflect flow generated from the graph, each answerable by
   adding a note: a person who went quiet, a decision that is a year old, a task mentioned three
   times and never closed. Answering a cue writes a note linked to its subject; the answered
   rate is the measure of whether the cards are worth showing. Cue cards are insights, so an
   insight shown as a cue is not repeated under Revisit or in the delta. Every cue and insight has
   **Why this surfaced**: its kind in plain words (a reminder, a connection, a contradiction, an
   evolution, ...) and the notes it is based on.
3. **The ask bar** with the effort dial and cue chips: *when* (a date, a range, "around" an
   entity's active period), *who/what* (entities), *kind* (idea, decision, task, ...), *topic*.
   Typing shows quick results immediately; with Quick chosen there is no Ask button, the
   matching notes follow the typing. Ask runs standard or deep and shows its steps as they happen
   (searched your notes, looked closer at what they connect, wrote the answer). An answer has three
   layers, each named and drawn differently: the **Synthesis** (what the selected notes suggest,
   with citations), the **Original** (the notes it rests on, quoted as you wrote them) and the
   **Interpretation** (what the notebook understood: how it changed, what is still open). Each
   citation opens the note with its attachments and the revision it caused. "Save this answer"
   turns it into a note with provenance; the note dialog shows such notes as synthesis, never as
   something you wrote.
4. **Then versus now**: for an entity or for a relationship between two entities, the earliest
   and the latest summary side by side, with the turning points between them listed with their
   notes. Cached per revision; the fastest way to see how your understanding of a person, a
   project, an idea or a link evolved.
5. **Why do I believe this**: every claim, and every sentence of an answer, opens its evidence
   chain: the claim, what it superseded or was superseded by, the notes, the attachment excerpt.
   One tap says "this is wrong" and writes a retract revision with your account as the source.
6. **Revisit**: open loops with a close action and their age (an open loop still open after 60
   days is marked stale, so it can be closed or left to age out), unread insights, "on this day" notes, entities
   that went silent but relate to what you added this week, contradictions awaiting a decision.
7. **To review** (only when there is something, `GET /note/review`): what the notebook decided
   on its own, each with why, the two sides, the notes behind them and its own actions. A merge it
   made: **Keep** (recorded for your account, `POST /note/review/{id}/confirm`) or **Undo the
   match** (separates them again; the joined entry's own name and notes come from
   `GET /note/entities/{id}?as_merged=true`). A close match it was unsure about: **Same** (joins
   them) or **Not the same** (withdraws the close match). Two notes that disagree: say which one is
   wrong (withdraws that statement).

Each entity card carries a small activity sparkline. A multi-lane time river is a candidate for
a later version once real notebooks show which lanes deserve a lane; the time scrubber in the
Explore view already covers "what did this look like then".

## The Explore view (the graph)

- **Focus and depth**: pick one or several entities; see their neighbourhood up to depth three,
  edges labelled by predicate with first and last seen dates and counts.
- **Picture or List**: the same connections drawn, or as a readable table (from, link, to, since,
  believed or no longer believed); each row opens the link's history or the entity.
- **Time scrubber and date field**: moving the slider or choosing a date changes `as_of` (the date
  field reaches any day without dragging). Beside it, **True then** reads the date as what your
  notes said was true then (`at`); **Saved by then** reads it as what the notebook held on that
  date (`as_received`: only notes received by then), so a June note imported in October is absent
  from a September snapshot. The layout stays where it is: entities and edges that did not exist yet fade
  out, superseded claims show as dashed; the entity card shows the beliefs at that moment. An edge
  opens the relation timeline between its two entities.
- **Day page**: "what happened on day X": notes captured, revisions applied with their one-line
  summaries, entities created or merged, claims superseded.
- **Entity page** (the topic portrait): name, aliases and activity, then five tabs. **How it
  evolved** comes first: the first note, the dated views from the summary history (earlier view,
  refined, latest expressed view) and the changes between them (a belief replaced or withdrawn),
  oldest first, each opening its note or its evidence; with a date chosen, the milestones after it
  stay in place, faded. Then **Changes** (what you believe or believed then, then-versus-now),
  **Open actions**, **Sources** (the notes) and **Links** (relations). An entity that absorbed
  others names them ("Includes Dana R. (1 note)", from each entry's own record). An entity asked
  for at a date before it existed says so.

## The Capture view

A stream of your notes as you wrote them, with the composer at the bottom. You type or paste,
attach files (images show a preview), drop images, or dictate; Enter saves and Shift+Enter starts
a new line; with your permission the browser attaches your location. Under each note one short
acknowledgement shows one state at a time, as a word and a symbol: **Sending**, **Received**,
**Organizing**, then **Organized** or **Needs attention**. Organized reads like "Added to #lut ·
event · 2 links" and opens on what was filed: the note's title, topics, linked entities, when you
said the same thing before, and what it changed. Until the note is organized the details show
the notebook's reply. With "Instant linking" on (Settings) the note is processed right away;
otherwise the app re-reads the note until it is. Nothing uses system vocabulary: no run ids, no
ledger, no gateway.

## For agents and entities

- `note_changes` (since you last looked) is the first call of a session; `note_search` at quick
  effort is cheap enough to call before every task.
- `note-recall` at standard effort returns the latest decision and why it changed, with
  citations, in one call; agents should quote the note ids in their own outputs so provenance
  survives.
- Deep recall returns a dossier note id; an agent can wait for the run or come back later.
- Agents capture notes through the same `note-capture` flow with `source.client = "agent"` and
  `source.author = <agent name>`, so the notebook records who learned what and when.
