"""Prompt templates the flow generator uses when the core lane's prompt files are absent.

The canonical prompts are Markdown files under ``abstractnote/process/prompts/<name>.md``
(core lane). Each template is rendered INSIDE the flow by a ``string_template`` node with
``{{var}}`` placeholders; ``FLOW_PROMPT_VARS`` names the variables each flow wires, and the
generator refuses a template that uses a placeholder the flow does not provide.
"""

from __future__ import annotations

from typing import Dict, FrozenSet

RULES = (
    "Rules:\n"
    "- Use only the note and the provided context; never invent entities or dates.\n"
    "- Prefer reusing an existing entity when the candidate list contains it; create sparingly.\n"
    "- Keep names as written by the author; put variants in aliases.\n"
    "- Quote evidence verbatim; one claim per statement; no inferred hierarchy.\n"
    "- When unsure whether two things are the same, answer `unsure` rather than guessing.\n"
    "- Output JSON only, matching the schema.\n"
)

FALLBACK_PROMPTS: Dict[str, str] = {
    "quick_filing": (
        "File this note quickly.\n\n"
        "Note title (may be empty): {{title}}\n"
        "Note text:\n{{text}}\n\n"
        "Similar earlier notes: {{similar}}\n"
        "Earlier notes with the same words: {{revisits}}\n"
        "Existing topics (prefer these, at most 4): {{topics}}\n\n"
        "Return a short title (at most 80 characters), the kind of note, its topics, how urgent it is "
        "(urgent or high only for deadlines, warnings, or anything the author marks urgent; bulk never "
        "for a live note), its language as a BCP-47 tag, and the note in one line.\n\n" + RULES
    ),
    "extract": (
        "Read one section of a note and extract what it says.\n\n"
        "Note: {{note}}\n\n"
        "Section to read:\n{{section}}\n\n"
        "What the notebook already knows (for orientation, do not copy it into the output):\n"
        "- similar notes: {{similar_notes}}\n"
        "- entities that may be mentioned: {{candidate_entities}}\n"
        "- open tasks: {{open_tasks}}\n"
        "- topics: {{topics}}\n\n"
        "Predicate vocabulary (use these CURIEs when one fits, else ex:snake_case):\n{{vocabulary}}\n\n"
        "Return a 1-3 sentence summary, the mentions (ref m1, m2, ...; name as written; kind; aliases seen; "
        "role in the note), the claims (subject and object are mention refs unless object_is_literal), "
        "the tasks opened or completed, the time references, and the topics.\n\n" + RULES
    ),
    "link": (
        "Decide how this note changes what the notebook knows.\n\n"
        "Note: {{note}}\n\n"
        "Extraction from the note:\n{{extraction}}\n\n"
        "Candidate existing entities per mention (refer to them by index in each list):\n{{candidates}}\n\n"
        "Context packet (entities with their active claims, open tasks, similar notes; refer to existing "
        "claims and open tasks by their index in these lists):\n{{packet}}\n\n"
        "For each mention decide reuse (give the candidate index), create, merge (give two candidate "
        "indexes) or unsure. For each extracted claim (by index) say whether it is new, restates, refines, "
        "supersedes or contradicts an existing claim (give its index). For each extracted task (by index) "
        "say which open task it closes, if any. Write an updated one-paragraph summary for an entity only "
        "when the note changes what is known, and one sentence summarising the revision.\n\n" + RULES
    ),
    "synthesize": (
        "Answer the question from the notes.\n\n"
        "Question: {{question}}\n"
        "As of: {{as_of}}\n\n"
        "Search results:\n{{hits}}\n\n"
        "Entity cards, claim timelines, relation timelines and then-versus-now pairs:\n{{expansion}}\n\n"
        "Write the answer with inline citations like [nt_...] using only the note ids above. List the "
        "evidence, what changed over time (earliest stance, latest stance, turning points with note ids), "
        "the related open loops, and up to three follow-up questions. If the notes do not answer the "
        "question, say so plainly.\n\nOutput JSON only, matching the schema.\n"
    ),
    "mine_plan": (
        "Plan a deep search through the notes to answer the question.\n\n"
        "Question: {{question}}\n"
        "Time window: since {{since}} until {{until}}\n\n"
        "First search results:\n{{hits}}\n\n"
        "Return up to six steps (a sub-question each, with an optional time slice and the names to trace), "
        "the contradictions worth testing, and the number of search rounds (1-8).\n\n"
        "Output JSON only, matching the schema.\n"
    ),
    "mine_findings": (
        "Read the search results for one step of a deep search.\n\n"
        "Main question: {{question}}\n"
        "This step: {{step}}\n\n"
        "Search results:\n{{hits}}\n\n"
        "Return the facts with the note ids that support them, what changed over time, and what is "
        "missing.\n\nOutput JSON only, matching the schema.\n"
    ),
    "mine_dossier": (
        "Write a dossier that answers the question from the findings of a deep search.\n\n"
        "Question: {{question}}\n"
        "Plan: {{plan}}\n\n"
        "Findings per step:\n{{findings}}\n\n"
        "Write a title, a narrative with inline citations [nt_...], a timeline, how the view evolved, the "
        "contradictions, forgotten items worth revisiting, suggestions, and a confidence statement. Use "
        "only the note ids in the findings.\n\nOutput JSON only, matching the schema.\n"
    ),
    "reflect": (
        "Look over these candidates from the notebook and choose what deserves the author's attention.\n\n"
        "Candidates (refer to them by index):\n{{candidates}}\n\n"
        "Choose at most {{limit}} items. For each, give the candidate index, the kind (warning, reminder, "
        "connection, contradiction or evolution), a short title and a body of two or three sentences that "
        "says why it matters now. Choose nothing when nothing deserves attention.\n\n"
        "Output JSON only, matching the schema.\n"
    ),
}

#: The variables each flow wires into each template (the contract with the prompt files).
FLOW_PROMPT_VARS: Dict[str, FrozenSet[str]] = {
    "quick_filing": frozenset({"title", "text", "similar", "revisits", "topics"}),
    "extract": frozenset({"note", "section", "similar_notes", "candidate_entities", "open_tasks", "topics", "vocabulary"}),
    "link": frozenset({"note", "extraction", "candidates", "packet"}),
    "synthesize": frozenset({"question", "as_of", "hits", "expansion"}),
    "mine_plan": frozenset({"question", "since", "until", "hits"}),
    "mine_findings": frozenset({"question", "step", "hits"}),
    "mine_dossier": frozenset({"question", "plan", "findings"}),
    "reflect": frozenset({"candidates", "limit"}),
}
