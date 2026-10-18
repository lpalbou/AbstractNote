You read the search results for one step of a deep search through a person's notes.

Main question: {{question}}
This step: {{step}}

Search results:
{{hits}}

Return the `facts` with the note ids that support each one (`citations`), what `changes` over time the results show, and the `gaps`: what is missing to answer the step.

Rules:
- Use only these results; cite note ids exactly as given.
- Output JSON only, matching the schema.
