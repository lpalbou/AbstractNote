"""Recall and reflect tools (docs/api.md): note_search, note_expand, note_changes, note_entity,
note_timeline, note_open_loops, note_contradictions, note_resurface, note_anniversaries,
note_insight_write."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..recall import expand as _expand
from ..recall import reflect as _reflect
from ..recall.insights import write_insight
from ..recall.search import search
from .context import ToolContext


def _blank(v: Any) -> Any:
    return None if v in ("", {}, []) else v


def note_search(ctx: ToolContext, query: str = "", since: Optional[str] = None, until: Optional[str] = None,
                kinds: Optional[List[str]] = None, entities: Optional[List[str]] = None,
                topics: Optional[List[str]] = None, limit: int = 20, as_of: Optional[str] = None,
                order: Optional[str] = None) -> Dict[str, Any]:
    return search(ctx.nb, query or "", since=_blank(since), until=_blank(until), kinds=_blank(kinds),
                  entities=_blank(entities), topics=_blank(topics), limit=int(limit or 20), as_of=_blank(as_of),
                  order=_blank(order))


def note_expand(ctx: ToolContext, note_ids: Optional[List[str]] = None, entity_ids: Optional[List[str]] = None,
                as_of: Optional[str] = None) -> Dict[str, Any]:
    return _expand.expand(ctx.nb, note_ids or [], entity_ids or [], _blank(as_of))


def note_changes(ctx: ToolContext, since: Optional[str] = None, limit: int = 50) -> Dict[str, Any]:
    return ctx.nb.changes(_blank(since), int(limit or 50))


def note_entity(ctx: ToolContext, entity_id: str, at: Optional[str] = None) -> Dict[str, Any]:
    return _expand.entity_card(ctx.nb, entity_id, _blank(at))


def note_timeline(ctx: ToolContext, since: Optional[str] = None, until: Optional[str] = None,
                  granularity: str = "day") -> Dict[str, Any]:
    return ctx.nb.timeline(_blank(since), _blank(until), granularity or "day")


def note_open_loops(ctx: ToolContext, limit: int = 20, since: Optional[str] = None) -> Dict[str, Any]:
    return _reflect.open_loops(ctx.nb, int(limit or 20), _blank(since))


def note_contradictions(ctx: ToolContext, limit: int = 20, since: Optional[str] = None) -> Dict[str, Any]:
    return _reflect.contradictions(ctx.nb, int(limit or 20), _blank(since))


def note_resurface(ctx: ToolContext, limit: int = 20, since: Optional[str] = None) -> Dict[str, Any]:
    return _reflect.resurface(ctx.nb, int(limit or 20), _blank(since))


def note_anniversaries(ctx: ToolContext, limit: int = 20, since: Optional[str] = None) -> Dict[str, Any]:
    return _reflect.anniversaries(ctx.nb, int(limit or 20), _blank(since))


def note_insight_write(ctx: ToolContext, kind: str, title: str, body: str,
                       evidence_note_ids: Optional[List[str]] = None, entity_ids: Optional[List[str]] = None,
                       run_id: Optional[str] = None) -> Dict[str, Any]:
    return write_insight(ctx.nb, kind, title, body, evidence_note_ids or [], entity_ids or [], _blank(run_id))
