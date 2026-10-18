"""Text normalisation and sectioning.

``text_norm`` is the hashing and full-text form of a note: Unicode NFC,
whitespace collapsed, lower-cased. ``sections`` splits a long text for
processing by Markdown headings, then paragraphs, packing pieces up to a
character budget; every character of the input is kept (no truncation).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Dict, List

DEFAULT_SECTION_CHARS = 12000

_HEADING = re.compile(r"^#{1,6}\s")  # Markdown heading line (document structure, not meaning)


def text_norm(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", str(text or "")).split()).lower()


def normalize_topic(topic: str) -> str:
    """Topic spelling: no leading ``#``, words joined with ``-``, lower case."""
    t = str(topic or "").strip().lstrip("#").strip()
    return "-".join(t.lower().split())


def _blocks(text: str) -> List[str]:
    """Heading-led blocks, each split further into paragraphs (blank-line separated)."""
    lines = text.splitlines(keepends=True)
    blocks: List[List[str]] = [[]]
    for line in lines:
        if _HEADING.match(line) and blocks[-1]:
            blocks.append([])
        blocks[-1].append(line)
    out: List[str] = []
    for b in blocks:
        chunk = "".join(b)
        if not chunk:
            continue
        paras = re.split(r"(\n[ \t]*\n)", chunk)
        # keep separators attached to the paragraph before them so nothing is lost
        cur = ""
        for piece in paras:
            cur += piece
            if re.fullmatch(r"\n[ \t]*\n", piece):
                out.append(cur)
                cur = ""
        if cur:
            out.append(cur)
    return out


def _split_hard(piece: str, budget: int) -> List[str]:
    """Split an over-budget paragraph at line ends, then sentence ends, then the budget itself."""
    out: List[str] = []
    rest = piece
    while len(rest) > budget:
        window = rest[:budget]
        cut = max(window.rfind("\n"), window.rfind(". "), window.rfind("? "), window.rfind("! "))
        cut = cut + 1 if cut > budget // 4 else budget
        out.append(rest[:cut])
        rest = rest[cut:]
    if rest:
        out.append(rest)
    return out


def sections(text: str, budget: int = DEFAULT_SECTION_CHARS) -> List[Dict[str, object]]:
    """``[{index, start, end, chars, text}]`` covering ``text`` exactly once, each <= budget chars."""
    text = str(text or "")
    budget = max(200, int(budget))
    if len(text) <= budget:
        return [{"index": 0, "start": 0, "end": len(text), "chars": len(text), "text": text}] if text else []
    pieces: List[str] = []
    for block in _blocks(text):
        pieces.extend(_split_hard(block, budget) if len(block) > budget else [block])
    packed: List[str] = []
    cur = ""
    for p in pieces:
        if cur and len(cur) + len(p) > budget:
            packed.append(cur)
            cur = ""
        cur += p
    if cur:
        packed.append(cur)
    out = []
    pos = 0
    for i, s in enumerate(packed):
        out.append({"index": i, "start": pos, "end": pos + len(s), "chars": len(s), "text": s})
        pos += len(s)
    assert pos == len(text), "sectioning must cover the text exactly"
    return out
