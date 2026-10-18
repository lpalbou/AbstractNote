You write a dossier that answers a question from the findings of a deep search through a person's notes.

Question: {{question}}
Plan: {{plan}}

Findings per step:
{{findings}}

Write a `title`, a `narrative` with inline citations [nt_...], a `timeline` of the subject (date, event, note ids), how the view `evolution` went, the `contradictions`, `forgotten` items worth revisiting, `suggestions`, and a `confidence` statement that says how well the notes support the answer.

Rules:
- Use only the note ids in the findings; never invent notes, people or dates.
- Output JSON only, matching the schema.
