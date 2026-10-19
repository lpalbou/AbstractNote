"""Prompt templates (Markdown with ``{{var}}`` placeholders) and their tiny renderer.

Every model step has two files (backlog 0024, docs/cognition.md "Prompts"):

- ``<name>.system.md``: the STATIC system prompt (instructions, the output shape, rules; the
  extraction one also lists the predicate vocabulary, filled in when the flows are generated).
  Its bytes are the same for every note, so a local model's prompt cache reuses it.
- ``<name>.md``: the DYNAMIC user message, rendered inside the flow from the note and its packet.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict

_DIR = Path(__file__).resolve().parent
_VAR = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)(?:\.[a-zA-Z0-9_.]+)?\s*\}\}")

NAMES = ("quick_filing", "extract", "link", "synthesize", "mine_plan", "mine_findings", "mine_dossier", "reflect")
#: Placeholders a system prompt may use; they are filled when the flows are generated, never at run time.
SYSTEM_VARS = {"extract": {"vocabulary"}}


def load(name: str) -> str:
    """The user-message template of a step."""
    if name not in NAMES:
        raise ValueError(f"There is no prompt named {name!r}.")
    return (_DIR / f"{name}.md").read_text(encoding="utf-8")


def load_system(name: str) -> str:
    """The static system prompt of a step (build-time placeholders still in it)."""
    if name not in NAMES:
        raise ValueError(f"There is no prompt named {name!r}.")
    return (_DIR / f"{name}.system.md").read_text(encoding="utf-8")


def variables(template: str) -> set:
    """Root names a template reads (``{{packet.header}}`` reads ``packet``)."""
    return set(_VAR.findall(template))


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=1)


def _lookup(values: Dict[str, Any], path: str) -> Any:
    cur: Any = values
    for part in path.split("."):
        cur = cur.get(part) if isinstance(cur, dict) else None
    return cur


def render(template: str, values: Dict[str, Any]) -> str:
    """Replace ``{{var}}`` / ``{{var.key}}`` with the value (JSON for non-strings); a missing root is an error."""
    missing = variables(template) - set(values)
    if missing:
        raise ValueError(f"The prompt needs values for: {', '.join(sorted(missing))}.")
    return re.sub(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_.]*)\s*\}\}", lambda m: _text(_lookup(values, m.group(1))), template)


def vocabulary_lines(vocab: Any) -> str:
    """The predicate vocabulary as ``- CURIE: gloss`` lines (the extraction system prompt's list)."""
    lines = []
    for v in vocab or []:
        gloss = " ".join(str(v.get("gloss") or "").split())
        lines.append(f"- {v['predicate']}" + (f": {gloss}" if gloss else ""))
    return "\n".join(lines)


def system_prompt(name: str) -> str:
    """The system prompt as the flows carry it: build-time placeholders filled."""
    text = load_system(name).strip() + "\n"
    if "{{vocabulary}}" in text:
        from ..vocabulary import vocabulary

        text = text.replace("{{vocabulary}}", vocabulary_lines(vocabulary()))
    left = variables(text)
    if left:
        raise ValueError(f"The {name} system prompt has placeholders that are never filled: {', '.join(sorted(left))}.")
    return text
