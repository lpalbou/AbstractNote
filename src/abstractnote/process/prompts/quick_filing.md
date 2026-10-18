You file one note the moment it arrives. Be quick and literal.

Note title (may be empty): {{title}}

Note text:
{{text}}

Similar earlier notes: {{similar}}
Earlier notes with the same words: {{revisits}}
Existing topics (prefer these): {{topics}}

Return:
- `title`: a short title of at most 80 characters, in the note's own words where possible;
- `kind`: idea, task, decision, fact, event, question, reflection, reference, log, conversation or personal;
- `topics`: at most 4, chosen from the existing topics first; propose a new topic only when none fits;
- `urgency`: urgent or high only for deadlines, warnings, or anything the author marks as urgent; normal otherwise (never bulk for a note written now);
- `language`: the note's language as a BCP-47 tag (for example `en`, `fr`);
- `one_line`: the note in one plain sentence.

Rules:
- Use only the note and the context above; never invent people, dates or facts.
- Keep names as the author wrote them.
- Output JSON only, matching the schema.
