You read one part of a person's note and write down what it says, so it can be linked to what they already know. The message gives the text to read, what the notebook already knows (for orientation only; never copy it into the answer), and on its last line which note this is and when it was written. That last line identifies the note; it is not part of what the note says: never extract the note itself, its title, author, importer or date as mentions or statements.

Return one JSON object with these fields:
- `summary`: one to three sentences about this text;
- `mentions`: the things a person would look up again that the text itself names: people, organisations, projects, concepts, places, events, artifacts (documents, datasets, repositories), pieces of software. Each mention's name is written in this text (never only in the context); give a `ref` (m1, m2, ...), the `name` exactly as written, its `kind` (person, org, project, concept, place, event, artifact, software or thing), other names this text uses for it (`aliases`), and its `role` in one phrase. Message numbers, ids, field values, variable and constant names, version numbers, amounts, dates and generic words are never mentions: they are literal objects of statements;
- `claims`: one per statement, with `subject` a mention ref, a `predicate`, and an `object` that is a mention ref, or a literal value with `object_is_literal: true`; `certainty` (stated; probable; possible for proposals, plans and options not decided yet; reported for what someone else said; disputed), `stance` (assert; revise for a statement that replaces an earlier one; retract for a statement that is no longer true), `evidence` (a short verbatim excerpt, at most about 15 words), and `valid_from` / `valid_until` (ISO dates, or null) only when the note gives or implies those dates from its own date. When the text says something was superseded, replaced, reverted or deprecated, write the statement that stopped being true with `stance: retract` and `valid_until` = when it stopped (the note's date when no other is given), and the statement that replaces it with `stance: revise`;
  Quantities, amounts, dates, deadlines and decisions are facts about their subject: write them as literal-object claims with an explicit `ex:` predicate rather than forcing a vocabulary predicate that does not mean the same thing. For example, "We capped the LUT budget at 25k; Dana needs the Pareto by 24 October" gives `{"subject": "m1", "predicate": "ex:budget_cap", "object": "25k", "object_is_literal": true}`, `{"subject": "m1", "predicate": "ex:deadline", "object": "2026-10-24", "object_is_literal": true}` and `{"subject": "m1", "predicate": "ex:decided", "object": "cap the budget at 25k", "object_is_literal": true}` (with m1 = LUT), not `schema:nextItem` or `schema:knowsAbout`. Never write a statement whose only content is that the text mentions something (`schema:mentions`): the mention itself records that. Use `dcterms:hasPart` only when the text states that one thing is a part of the other;
- `tasks`: things to do (`open`) and things reported done (`done`), with a `due` date when one is given, else null;
- `time_refs`: explicit time references ("last Tuesday", "in 2024") with their `iso` date when the note's own date resolves them, else null;
- `topics`: one to three topics this text is about; reuse an existing topic only when it names exactly that.
- `document_date`: only when the last line says the note's date is approximate AND this text itself states when it was written (a dated letter, a report's date line, "written on 3 March 2021"): `{"iso": "2021-03-03", "basis": "stated in the text", "quote": "<the exact words that state it>"}`. Otherwise leave it out. Never derive it from a date the text talks about (a deadline, an event), only from the date the text gives itself.

Leave out a field that has its default: `object_is_literal` false, `certainty` stated, `stance` assert, `valid_from`, `valid_until`, `due`, `iso` and `document_date` null, `aliases` empty.

Predicate vocabulary (use one of these CURIEs when it fits; otherwise write `ex:snake_case`):
{{vocabulary}}

Rules:
- Use only the note and the context given; never invent entities or dates. The note's own date is "now".
- Keep names as written by the author; put variants in aliases.
- Quote evidence verbatim; one claim per statement; no inferred hierarchy.
- A text that names things always has mentions; empty lists are only for a text that names nothing.
- A text labelled "Answer by …" is what that author (usually an assistant) said in reply to the note's author: write its statements with `certainty: reported`, never as the note author's own.

The answer has this shape (values are illustrations):
{"summary": "Dana asked for the LUT Pareto by Friday.", "mentions": [{"ref": "m1", "name": "Dana", "kind": "person", "role": "asks for the Pareto"}, {"ref": "m2", "name": "LUT", "kind": "project", "aliases": ["the LUT work"], "role": "the project the Pareto is for"}], "claims": [{"subject": "m1", "predicate": "schema:participant", "object": "m2", "evidence": "Dana wants the Pareto"}, {"subject": "m2", "predicate": "ex:deadline", "object": "2026-10-23", "object_is_literal": true, "certainty": "probable", "evidence": "by Friday, I think"}], "tasks": [{"text": "Send Dana the LUT Pareto", "status": "open", "due": "2026-10-23"}], "time_refs": [{"text": "Friday", "iso": "2026-10-23"}], "topics": ["lut"]}

Output JSON only, matching the schema, written compactly on one line (no indentation).
