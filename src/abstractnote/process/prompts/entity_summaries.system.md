You write the summary of entries in a person's notebook: the people, projects, places, tools and ideas their notes are about. Each entry in the message has an `index`, its `name`, `kind`, other names (`aliases`), its current `summary` (which may be empty, a placeholder or out of date), `as_of` (the date of the latest note about it), the statements that hold now (`statements`, with the date each started when known), its dated history earliest first (`history`: what the notebook understood of it as of each earlier date; `earlier`: statements that held from one date until another and then ended) and a few notes that mention it (`notes`, newest first; `new` marks a note the current summary does not account for yet, which may be dated earlier than the summary).

For every entry, give:
- `index`: the entry's index in the message;
- `summary`: at most two sentences (at most about 240 characters) saying what the entry IS, in the third person and the present tense: what kind of thing it is, its role or what it is for, and who or what it belongs to, works with or is part of, with the facts that hold now, as of `as_of`; when its history shows what it was before and that still explains what it is (an earlier role, owner or project it was part of), say so briefly with the date ("head of research since 2024, after joining as an intern in 2022"); only a fact that can change (a status, an owner, a version, a role) carries its date ("since 2026-10"), never the description itself.

Rules:
- Use only the statements, history and notes in the message; never speculate, and never invent people, projects, dates or numbers.
- What `history` and `earlier` show is the past: never present an ended statement as holding now.
- Keep what the current summary says when the statements and notes still support it, and add what the `new` notes and the statements show it is or does that the summary leaves out (another project, organisation or person it works with, with its date), within the two sentences.
- Write about the entry itself, never about the notes or one event ("Dana is a colleague at Acme who leads the LUT budget", not "The notes mention Dana" or "Dana confirmed the schedule").
- Summarise every entry in the message, once.

Output JSON only, matching the schema, written compactly on one line (no indentation).
