You read one section of a person's note and write down what it says, so it can be linked to what they already know.

The note: {{note}}

Section to read:
{{section}}

What the notebook already knows (for orientation only; do not copy it into the output):
- similar notes: {{similar_notes}}
- entities that may be mentioned: {{candidate_entities}}
- open tasks: {{open_tasks}}
- existing topics: {{topics}}

Predicate vocabulary (use one of these CURIEs when it fits; otherwise write `ex:snake_case`):
{{vocabulary}}

Return:
- `summary`: one to three sentences about this section;
- `mentions`: every person, organisation, project, concept, place, event, artifact (document, dataset, repository), piece of software or other thing the section talks about, each with a `ref` (m1, m2, ...), the `name` exactly as written, its `kind`, other names used for it in the note (`aliases`), and its `role` in one phrase;
- `claims`: one per statement, with `subject` a mention ref, a `predicate`, and an `object` that is a mention ref, or a literal value with `object_is_literal: true`; `certainty` (stated, probable, possible, reported, disputed), `stance` (assert; revise when the note corrects earlier knowledge; retract when it says something is no longer true), `evidence` (a verbatim excerpt), and `valid_from` / `valid_until` only when the note gives or implies those dates from its own date;
- `tasks`: things to do (`open`) and things reported done (`done`), with a due date when one is given;
- `time_refs`: explicit time references ("last Tuesday", "in 2024") with their ISO date when the note's own date resolves them;
- `topics`: the topics the section belongs to, preferring existing ones.

Rules:
- Use only the note and the provided context; never invent entities or dates.
- Keep names as written by the author; put variants in aliases.
- Quote evidence verbatim; one claim per statement; no inferred hierarchy.
- Output JSON only, matching the schema.
