"""``note_context``, ``note_resolve_candidates``, ``note_apply_revision`` (docs/api.md)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..process.context import note_context as _context
from ..process.resolve import resolve_candidates
from ..process.revise import apply_note_revision
from .context import ToolContext


def note_context(ctx: ToolContext, note_id: str, max_chars: Optional[int] = None) -> Dict[str, Any]:
    return _context(ctx.nb, note_id, max_chars)


def note_resolve_candidates(ctx: ToolContext, note_id: str, mentions: List[Dict[str, Any]]) -> Dict[str, Any]:
    return resolve_candidates(ctx.nb, note_id, mentions)


def note_apply_revision(ctx: ToolContext, note_id: str, extraction: Any, linking: Dict[str, Any]) -> Dict[str, Any]:
    return apply_note_revision(ctx.nb, note_id, extraction, linking)
