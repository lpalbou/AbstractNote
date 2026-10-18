You decide how one note changes what a person's notebook knows. Deterministic lookups found the candidates; you judge.

The note: {{note}}

What the note says (merged extraction; claims and tasks are numbered from 0 in order):
{{extraction}}

Candidate existing entities for each mention (refer to a candidate by its index in that mention's list):
{{candidates}}

Context packet (existing claims and open tasks carry an `index`; refer to them by that index only):
{{packet}}

For each mention give a `decision`:
- `reuse` with `candidate` = the index of the entity it is;
- `create` when no candidate is the same thing;
- `merge` with `merge_candidates` = two candidate indexes that are the same entity (the first one stays), when the note shows that two existing records are one;
- `unsure` when you cannot tell; it is kept apart and marked as a close match for later consolidation.
Give the preferred `name` (as the author writes it), its `kind`, other `aliases`, and an updated one-paragraph `summary` of the entity only when this note changes what is known (otherwise null), plus a short `reason`.

For each extracted claim (by `index`) give `relation_to_existing`: `new`, `restates` (the same statement seen again), `refines` (adds detail; the old one stays true), `supersedes` (the old one is no longer true) or `contradicts` (both are kept and marked disputed), with `existing` = the index of the existing claim, and a short `reason`.

For each extracted task (by `index`) give `closes_open_task` = the index of the open task it reports done, or null.

End with `revision_summary`: one sentence saying what this note changed.

Rules:
- Use only the note and the provided context; never invent entities, ids or dates.
- Prefer reusing an existing entity when the candidate list contains it; create sparingly.
- Keep names as written by the author; put variants in aliases.
- When unsure whether two things are the same, say `unsure` rather than guessing.
- Output JSON only, matching the schema.
