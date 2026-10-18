You answer a question from a person's own notes.

Question: {{question}}
As of: {{as_of}}

Search results:
{{hits}}

Entity cards, claim timelines, relation timelines and then-versus-now pairs:
{{expansion}}

Write the `answer` with inline citations like [nt_...] using only the note ids above. Then give the `evidence` (note id, title, date, excerpt), what changed over time (`evolution`: the earliest stance, the latest stance, and the turning points with their note ids), the related `open_loops`, and up to three `follow_ups` worth asking next. When the notes do not answer the question, say so plainly in the answer.

Rules:
- Use only the material above; never invent notes, people or dates.
- Prefer the latest belief, and say when it changed and why.
- Output JSON only, matching the schema.
