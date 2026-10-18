You plan a deep search through a person's notes to answer a question.

Question: {{question}}
Time window: since {{since}} until {{until}}

First search results:
{{hits}}

Return up to six `steps` (each a sub-question, with an optional time slice `since` / `until` and the names of entities to trace), the `contradictions_to_test`, and the number of search `rounds` (1 to 8).

Rules:
- Plan only searches the notes can answer; never invent entities or dates.
- Output JSON only, matching the schema.
