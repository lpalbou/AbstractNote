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
| Fragment | three words you are sure were in the note | the note | quick recall: full-text plus vector, grouped by entity and date |
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
| **quick** | under a second, no model | full-text (FTS5, BM25) plus vector search over notes, entities and claims; grouping by entity and month; facets | hits with excerpts, matched entities, matched claims, facets |
| **standard** | seconds, one model call | quick retrieval, then graph expansion (entity cards as of the requested moment, claim timelines, neighbours, then-versus-now pairs), then one synthesis | an answer with citations, the evidence, the evolution summary, open loops, follow-ups |
| **deep** | minutes, several model calls, runs in the background | a mining plan (sub-questions, time slices, entities, contradictions), several retrieval rounds with findings per slice, a dossier | the dossier as an answer and as a stored `synthesis` note; the web app and the watch show it when it is ready |

The level is a parameter everywhere: the Remember view has an effort dial, the watch asks at
standard by default, agents pass it explicitly, and the CLI takes `--effort`. A deep search is
never blocking: the run starts, the client gets a run id, and the dossier appears as an insight.

## The Remember view

The Remember view opens on what changed and lets you dig from there.

1. **Since you last looked** (the default screen): what the notebook learned since your last
   visit: new entities, beliefs that were replaced ("Replaced") or withdrawn ("Withdrawn"), loops
   that closed, insights, notes that failed to process. It is a system-time delta from a cursor, so it is the same call an agent
   makes at the start of every session (`GET /note/changes?since=`). Each item opens its subject.
2. **Cue cards**: a few prompts the reflect flow generated from the graph, each answerable by
   adding a note: a person who went quiet, a decision that is a year old, a task mentioned three
   times and never closed. Answering a cue writes a note linked to its subject; the answered
   rate is the measure of whether the cards are worth showing.
3. **The ask bar** with the effort dial and cue chips: *when* (a date, a range, "around" an
   entity's active period), *who/what* (entities), *kind* (idea, decision, task, ...), *topic*.
   Typing shows quick results immediately; pressing Ask runs standard or deep. Answers show the
   text with citations; each citation opens the note with its attachments and the revision it
   caused. "Save this answer" turns it into a note with provenance.
4. **Then versus now**: for an entity or for a relationship between two entities, the earliest
   and the latest summary side by side, with the turning points between them listed with their
   notes. Cached per revision; the fastest way to see how your understanding of a person, a
   project, an idea or a link evolved.
5. **Why do I believe this**: every claim, and every sentence of an answer, opens its evidence
   chain: the claim, what it superseded or was superseded by, the notes, the attachment excerpt.
   One tap says "this is wrong" and writes a retract revision with your account as the source.
6. **Revisit**: open loops with a close action, unread insights, "on this day" notes, entities
   that went silent but relate to what you added this week, contradictions awaiting a decision.

Each entity card carries a small activity sparkline. A multi-lane time river is a candidate for
a later version once real notebooks show which lanes deserve a lane; the time scrubber in the
Graph view already covers "what did this look like then".

## The Graph view

- **Focus and depth**: pick one or several entities; see their neighbourhood up to depth three,
  edges labelled by predicate with first and last seen dates and counts.
- **Time scrubber**: moving it changes `as_of`: entities and edges that did not exist yet fade
  out, superseded claims show as dashed; the entity card shows the beliefs at that moment. An edge
  opens the relation timeline between its two entities.
- **Day page**: "what happened on day X": notes captured, revisions applied with their one-line
  summaries, entities created or merged, claims superseded.
- **Entity page**: card, aliases, summary history, claims timeline (active and superseded),
  relations, notes, open tasks, and then-versus-now.

## The Capture view

A chat. You type or paste, attach files, drop images, or dictate; with your permission the
browser attaches your location. The answer tells you what was saved, how it was filed, which
earlier notes and entities it relates to, and when you said the same thing before. With
"Instant linking" on (Settings) the note is processed right away and the answer includes the
links the processor made; otherwise it says the note is queued, and the thread updates that
message when the note is processed (the app re-reads the
note until it is). Nothing in the answer uses system vocabulary: no run ids, no ledger, no gateway.

## For agents and entities

- `note_changes` (since you last looked) is the first call of a session; `note_search` at quick
  effort is cheap enough to call before every task.
- `note-recall` at standard effort returns the latest decision and why it changed, with
  citations, in one call; agents should quote the note ids in their own outputs so provenance
  survives.
- Deep recall returns a dossier note id; an agent can wait for the run or come back later.
- Agents capture notes through the same `note-capture` flow with `source.client = "agent"` and
  `source.author = <agent name>`, so the notebook records who learned what and when.
