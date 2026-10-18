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

The Remember view combines four instruments on one screen.

1. **The ask bar** with the effort dial and cue chips: *when* (a date, a range, "around" an
   entity's active period), *who/what* (entities), *kind* (idea, decision, task, ...), *topic*.
   Typing shows quick results immediately; pressing Ask runs standard or deep.
2. **Memory lanes**: a horizontal time river. Each lane is an entity or topic you chose or that
   the results involve; dots are notes, thicker segments are periods of activity, vertical links
   are revisions that connected two lanes (a relationship formed), and markers are
   supersessions (a belief changed). Clicking a point opens the day; dragging a window sets the
   time cue. The river is also the Graph view's time scrubber, so the two views share state.
3. **Then versus now**: for each entity in focus, the earliest and the latest summary side by
   side, with the turning points between them listed with their notes. This card is generated
   once per entity revision and cached; it is the fastest way to see how your understanding of a
   person, a project or an idea evolved.
4. **Revisit**: what the reflect flow surfaced and what the moment suggests: open loops, unread
   insights, "on this day" notes, entities that went silent but relate to what you added this
   week, contradictions awaiting a decision. Each item has one action (open, acknowledge, add a
   note about it).

Answers at standard and deep effort show the answer text with citations; each citation opens the
note in a side panel with its attachments and the revision it caused. The evidence list can be
turned into a new note ("save this answer"), which keeps the answer with provenance.

## The Graph view

- **Focus and depth**: pick one or several entities; see their neighbourhood up to depth three,
  edges labelled by predicate with first and last seen dates and counts.
- **Time scrubber**: the same river as Remember. Moving it changes `as_of`: entities and edges
  that did not exist yet fade out, superseded claims show as dashed; the entity card shows the
  beliefs at that moment.
- **Day page**: "what happened on day X": notes captured, revisions applied with their one-line
  summaries, entities created or merged, claims superseded.
- **Entity page**: card, aliases, summary history, claims timeline (active and superseded),
  relations, notes, open tasks, and then-versus-now.

## The Capture view

A chat. You type or paste, attach files, drop images, or dictate. The answer tells you what was
saved, how it was filed, which earlier notes and entities it relates to, and when you first said
something similar. When the notebook is quiet the answer includes the links the processor made;
otherwise it says the note is queued and the links appear in the thread when the processor is
done. Nothing in the answer uses system vocabulary: no run ids, no ledger, no gateway.

## For agents and entities

- `note_search` at quick effort is cheap enough to call before every task.
- `note-recall` at standard effort returns the latest decision and why it changed, with
  citations, in one call; agents should quote the note ids in their own outputs so provenance
  survives.
- Deep recall returns a dossier note id; an agent can wait for the run or come back later.
- Agents capture notes through the same `note-capture` flow with `source.client = "agent"` and
  `source.author = <agent name>`, so the notebook records who learned what and when.
