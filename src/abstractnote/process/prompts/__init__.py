"""Prompt templates (Markdown with ``{{var}}`` placeholders) and their tiny renderer."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict

_DIR = Path(__file__).resolve().parent
_VAR = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")

NAMES = ("quick_filing", "extract", "link", "synthesize", "mine_plan", "mine_findings", "mine_dossier", "reflect")


def load(name: str) -> str:
    if name not in NAMES:
        raise ValueError(f"There is no prompt named {name!r}.")
    return (_DIR / f"{name}.md").read_text(encoding="utf-8")


def variables(template: str) -> set:
    return set(_VAR.findall(template))


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=1)


def render(template: str, values: Dict[str, Any]) -> str:
    """Replace ``{{var}}`` with the value (JSON for non-strings); a missing variable is an error."""
    missing = variables(template) - set(values)
    if missing:
        raise ValueError(f"The prompt needs values for: {', '.join(sorted(missing))}.")
    return _VAR.sub(lambda m: _text(values[m.group(1)]), template)
