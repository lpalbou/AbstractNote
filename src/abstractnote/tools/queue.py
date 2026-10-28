"""``note_queue_lease``, ``note_queue_ack``, ``note_queue_stats`` (docs/api.md)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from .context import ToolContext


def note_queue_lease(ctx: ToolContext, worker_id: str, max_items: int = 1) -> Dict[str, Any]:
    out = ctx.nb.queue.lease(worker_id, max_items)
    return {"items": [{"note_id": i["note_id"], "priority": i["priority"], "attempts": i["attempts"]}
                      for i in out["items"]], "limit_applied": out["limit_applied"]}


#: The recorded reason of a note with nothing to read (note-process's ``sections`` step raises the same sentence).
NO_TEXT_ERROR = "The note has no text to read."


def note_queue_ack(ctx: ToolContext, note_id: str, outcome: str, error: Optional[str] = None,
                   revision_id: Optional[str] = None) -> Dict[str, Any]:
    """A failure of a note that has no text to read is final (one attempt, its reason recorded): whatever the
    error says, the stored note is checked (no section to read, ``process.context.note_sections``), since no
    retry can give it text (B item 11: such a note was retried three times across drains)."""
    final = False
    if outcome == "error" and ctx.nb.store.note_row(note_id) is not None:
        from ..process.context import note_sections

        final = not note_sections(ctx.nb, note_id)[0]
        if final:
            error = error or NO_TEXT_ERROR
    return ctx.nb.queue.ack(note_id, outcome, error=error, revision_id=revision_id, final=final)


def note_queue_stats(ctx: ToolContext) -> Dict[str, Any]:
    stats = ctx.nb.queue.stats()
    return {k: stats[k] for k in ("queued", "processing", "failed", "by_priority", "oldest_enqueued_at")}
