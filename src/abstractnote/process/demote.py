"""Structural demotion of mentions that are values, not things (backlog 0011 eval2 D8).

A mention whose whole name is a bare number, a version, a message or thread id, a URL or an invite or
hash token, or one generic word from :data:`GENERIC_WORDS_FILE` is kept as a literal object of its
statements, never an entity. These are checks on the SHAPE of the name (digits, dots, a scheme, a
fixed id form) and on an explicit, editable word list, never a reading of what the name means
(ADR 0004, amended by the operator's ruling on the 2026-10-20 data check).
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import FrozenSet, Optional

from .. import ids

GENERIC_WORDS_FILE = Path(__file__).resolve().parent / "generic_words.txt"

_NUMBER = re.compile(r"^[#№]?[+-]?\d+(?:[.,:/\-]\d+)*%?$")
_VERSION = re.compile(r"^v?\d+(?:\.\d+)+(?:[.\-+]?[a-z0-9]+)*$|^v\d+$", re.IGNORECASE)
_MESSAGE_ID = re.compile(r"^[a-z]{1,3}\d{3,}$|^[a-z][\w\-]*#\d+$|^dm:[\w.\-]+(?:--[\w.\-]+)?$", re.IGNORECASE)
_URL = re.compile(r"^(?:[a-z][a-z0-9+.\-]*://|www\.)\S+$", re.IGNORECASE)
_TOKEN = re.compile(r"^(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{16,}$|^[0-9a-f]{7,64}$")


@lru_cache(maxsize=1)
def generic_words() -> FrozenSet[str]:
    out = set()
    for line in GENERIC_WORDS_FILE.read_text(encoding="utf-8").splitlines():
        w = line.strip()
        if w and not w.startswith("#"):
            out.add(ids.fold(w))
    return frozenset(out)


def literal_reason(name: str) -> Optional[str]:
    """Why a mention named ``name`` is a value rather than an entity (a short phrase), or None."""
    n = " ".join(str(name or "").split())
    if not n:
        return None
    if _VERSION.match(n) and (n.count(".") >= 2 or n[0] in "vV" or not _NUMBER.match(n)):
        return "a version"  # 0.9.0, v15, 2.15.0rc1 (a lone 3.14 is a number)
    if _NUMBER.match(n):
        return "a number"
    if _URL.match(n):
        return "a link"
    if _MESSAGE_ID.match(n):
        return "a message id"
    if _TOKEN.match(n) and any(ch.isdigit() for ch in n):
        return "a token"
    if " " not in n and ids.fold(n) in generic_words():
        return "a generic word"
    return None


__all__ = ["GENERIC_WORDS_FILE", "generic_words", "literal_reason"]
