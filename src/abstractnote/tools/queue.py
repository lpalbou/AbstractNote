"""``note_queue_lease``, ``note_queue_ack``, ``note_queue_stats`` (docs/api.md)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from .context import ToolContext


def note_queue_lease(ctx: ToolContext, worker_id: str, max_items: int = 1) -> Dict[str, Any]:
    out = ctx.nb.queue.lease(worker_id, max_items)
    return {"items": [{"note_id": i["note_id"], "priority": i["priority"], "attempts": i["attempts"]}
                      for i in out["items"]]}


def note_queue_ack(ctx: ToolContext, note_id: str, outcome: str, error: Optional[str] = None,
                   revision_id: Optional[str] = None) -> Dict[str, Any]:
    return ctx.nb.queue.ack(note_id, outcome, error=error, revision_id=revision_id)


def note_queue_stats(ctx: ToolContext) -> Dict[str, Any]:
    stats = ctx.nb.queue.stats()
    return {k: stats[k] for k in ("queued", "processing", "failed", "by_priority", "oldest_enqueued_at")}
