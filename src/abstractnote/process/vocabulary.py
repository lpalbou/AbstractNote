"""The predicate vocabulary from the AbstractSemantics registry, with one-line glosses."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

EX_PREDICATE = re.compile(r"^ex:[a-z][a-z0-9_]*$")


@lru_cache(maxsize=1)
def _registry():
    from abstractsemantics import load_semantics_registry

    return load_semantics_registry()


def vocabulary() -> List[Dict[str, str]]:
    """``[{predicate, gloss}]`` for every registry predicate (the extraction prompt's list)."""
    out = []
    for p in _registry().predicates:
        gloss = (p.description or p.label or "").strip()
        out.append({"predicate": p.id, "gloss": gloss})
    return out


def predicate_words(predicate: str) -> str:
    """Plain words for a predicate, for text a person reads: the registry label (``schema:worksFor``
    -> "works for"), else the spelled-out local name (``ex:led_by`` -> "led by"). Never a CURIE."""
    raw = str(predicate or "").strip()
    for p in _registry().predicates:
        if p.id == raw and (p.label or "").strip():
            return p.label.strip()
    local = raw.split(":", 1)[1] if ":" in raw else raw
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", local).replace("_", " ").strip().lower()
    return " ".join(words.split()) or "related to"


def predicate_ids() -> frozenset:
    return frozenset(_registry().predicate_ids())


def _snake(text: str) -> str:
    """``worksFor`` / ``works for`` -> ``works_for`` (spelling only)."""
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(text or "").strip())
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_").lower()
    if not s or not s[0].isalpha():
        s = "p_" + s
    return s


def normalize_predicate(predicate: str) -> Tuple[str, Optional[str]]:
    """Map an extracted predicate to a registry CURIE (exact or a registry alias); anything
    else becomes ``ex:snake_case``. Returns ``(predicate, warning or None)``; CURIE case is kept."""
    from abstractsemantics import normalize_kg_predicate

    raw = str(predicate or "").strip()
    if not raw:
        return "ex:related_to", "A statement had no predicate; it was kept as ex:related_to."
    canonical = normalize_kg_predicate(raw, _registry())
    if canonical:
        return canonical, None
    if EX_PREDICATE.match(raw):
        return raw, f"The predicate {raw} is outside the vocabulary."
    local = raw.split(":", 1)[1] if ":" in raw else raw
    mapped = "ex:" + _snake(local)
    return mapped, f"The predicate {raw} is outside the vocabulary; kept as {mapped}."
