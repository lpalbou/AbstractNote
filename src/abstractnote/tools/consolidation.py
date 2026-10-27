"""Consolidation tools (backlog 0032, docs/api.md "Note tools"): note_consolidation_begin,
note_consolidation_progress, note_consolidation_end, note_entities_normalize, note_entities_stale_summaries,
note_entities_set_summaries, note_then_now_refresh. They act on the notebook's open consolidation row."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..recall import consolidation as _c
from .context import ToolContext


def note_consolidation_begin(ctx: ToolContext) -> Dict[str, Any]:
    return _c.begin(ctx.nb)


def note_consolidation_progress(ctx: ToolContext, stage: str, done: int, total: int,
                                note: Optional[str] = None, skipped: Optional[List[Any]] = None,
                                reason: Optional[str] = None) -> Dict[str, Any]:
    return _c.progress(ctx.nb, stage, done, total, note or None, skipped or None, reason or None)


def note_consolidation_end(ctx: ToolContext, status: str, results: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return _c.end(ctx.nb, status, results or {})


def note_entities_normalize(ctx: ToolContext) -> Dict[str, Any]:
    return _c.normalize(ctx.nb)


def note_entities_stale_summaries(ctx: ToolContext, limit: int = _c.SUMMARY_BATCH) -> Dict[str, Any]:
    return _c.stale_summaries(ctx.nb, limit)


def note_entities_set_summaries(ctx: ToolContext, items: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    return _c.set_summaries(ctx.nb, items or [], account=ctx.account)


def note_then_now_refresh(ctx: ToolContext) -> Dict[str, Any]:
    return _c.refresh_then_now(ctx.nb)


def note_reinterpret_next(ctx: ToolContext, limit: int = _c.REINTERPRET_BATCH,
                          failed: Optional[List[Any]] = None) -> Dict[str, Any]:
    """ADR 0008: the next notes the consolidation's reinterpret stage understands again (see
    :func:`abstractnote.recall.consolidation.reinterpret_next`)."""
    return _c.reinterpret_next(ctx.nb, limit, failed or None)


def note_mark_reinterpret(ctx: ToolContext, note_ids: List[str], reason: Optional[str] = None) -> Dict[str, Any]:
    """ADR 0008: mark understood notes to be understood again (the next consolidation reads them)."""
    if not isinstance(note_ids, (list, tuple)) or not note_ids:
        raise ValueError("Give the notes to understand again as a list of note ids.")
    return ctx.nb.mark_reinterpret([str(n) for n in note_ids], reason or "")
