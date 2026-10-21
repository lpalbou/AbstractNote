"""``note_context``, ``note_resolve_candidates``, ``note_apply_revision`` (docs/api.md).

ADR 0008 (lane R2): a note that was understood before is read again (``note.reinterpret``) when the apply is asked to
(``reinterpret=True``) or when a consolidation pass has taken its re-understanding mark: that is the one place the
``already_applied`` answer of a processed note is decided. Its context leaves out its own previous reading and shows
again what that reading had closed (:func:`abstractnote.process.revise.reinterpret_view`). The mention queries a
resolve step answered are kept in the stored packet so a later no-call check can ask them again.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ..process.context import load_packet, save_packet
from ..process.context import note_context as _context
from ..process.resolve import resolve_candidates
from ..process import split, validate
from ..process.merge import merge_extractions
from ..process.revise import _consume, apply_note_revision, apply_reinterpretation, reinterpret_view
from .context import ToolContext

_log = logging.getLogger("abstractnote.process")


def note_context(ctx: ToolContext, note_id: str, max_chars: Optional[int] = None) -> Dict[str, Any]:
    nb = ctx.nb
    out = _context(nb, note_id, max_chars)
    digest = out.get("context")
    if isinstance(digest, dict):  # R1's digest: kept with the packet so the apply records it (context_json)
        packet = load_packet(nb, note_id)
        if packet is not None and packet.get("context") != digest:
            packet["context"] = digest
            save_packet(nb, note_id, packet)
    packet = load_packet(nb, note_id)
    similar = [n.get("note_id") for n in out.get("similar_notes") or [] if isinstance(n, dict) and n.get("note_id")]
    if packet is not None and packet.get("similar_notes") != similar:  # the notes the reading was shown (ADR 0008)
        packet["similar_notes"] = similar
        save_packet(nb, note_id, packet)
    if nb.store.current_interpretation(note_id) is not None:
        out = reinterpret_view(nb, note_id, out)
    return out


def note_resolve_candidates(ctx: ToolContext, note_id: str, mentions: List[Dict[str, Any]]) -> Dict[str, Any]:
    nb = ctx.nb
    out = resolve_candidates(nb, note_id, mentions)
    packet = load_packet(nb, note_id)
    if packet is not None:
        packet["mention_queries"] = [{"ref": str(m.get("ref")), "name": str(m.get("name")),
                                      "kind": m.get("kind"), "aliases": [str(a) for a in m.get("aliases") or []]}
                                     for m in mentions if isinstance(m, dict)]
        save_packet(nb, note_id, packet)
    if nb.store.current_interpretation(note_id) is not None:  # its own previous statements are not context
        own = {r[0] for r in nb.store.q("SELECT claim_id FROM claims WHERE note_id=?", (note_id,))}
        out = {**out, "existing_claims": [v for v in out.get("existing_claims") or [] if v.get("claim_id") not in own],
               "mentions": [{**m, "candidates": [{**c, "active_claims": [v for v in c.get("active_claims") or []
                                                                          if v.get("claim_id") not in own]}
                                                 for c in m.get("candidates") or []]}
                            for m in out.get("mentions") or []]}
    return out


def _linked(linking: Any) -> bool:
    """True when a linking answer decides something (the flow skips the linking of a document it splits, W30)."""
    return isinstance(linking, dict) and bool(linking.get("mentions") or linking.get("claims") or linking.get("tasks"))


def note_apply_revision(ctx: ToolContext, note_id: str, extraction: Any, linking: Dict[str, Any],
                        reinterpret: bool = False, pass_id: Optional[str] = None) -> Dict[str, Any]:
    """Apply a note's reading. W30: an extraction that reads the note as a document of dated parts (``moments``)
    splits it (``process/split.py``) instead of applying its statements; a container read again stays as it is (its
    parts are notes of their own, each understood again by itself) and its marks are settled as unchanged. A reading
    the notebook refuses is logged on ``abstractnote.process`` with its note and reason (ADR 0007, W32) and raised."""
    try:
        return _apply(ctx, note_id, extraction, linking, reinterpret, pass_id)
    except Exception as exc:
        _log.warning("note_apply_revision: the reading of %s was not applied (%s: %s)", note_id,
                     type(exc).__name__, exc)
        raise


def _apply(ctx: ToolContext, note_id: str, extraction: Any, linking: Dict[str, Any], reinterpret: bool,
           pass_id: Optional[str]) -> Dict[str, Any]:
    nb = ctx.nb
    if split.is_container(nb, note_id) and nb.store.current_interpretation(note_id) is not None:
        return settle_container(nb, note_id, reinterpret=reinterpret, pass_id=pass_id)
    if isinstance(extraction, list):
        extraction, _ = merge_extractions(extraction)
    if not reinterpret and nb.store.current_interpretation(note_id) is None and isinstance(extraction, dict):
        decided = split.plan(nb, note_id, extraction)
        if decided["split"]:
            validate.check_apply(nb, note_id, extraction, {}, None)
            return split.apply_split(nb, note_id, extraction, decided)
        if decided["warning"]:
            extraction = dict(extraction, warnings=[*(extraction.get("warnings") or []), decided["warning"]])
        if extraction.get("moments") and extraction.get("mentions") and not _linked(linking):
            # the flow skipped the linking because it read the parts as splittable; the apply did not find them
            raise ValueError(decided["warning"] or split.NOT_FOUND)
    elif isinstance(extraction, dict) and extraction.get("moments") and split.can_split(nb.store.note_row(note_id)):
        # a note understood as one note before W30 (or before its entries were read) is not split on a re-reading
        extraction = dict(extraction, warnings=[*(extraction.get("warnings") or []), split.NOT_SPLIT_REREAD])
    if reinterpret:
        return apply_reinterpretation(nb, note_id, extraction, linking, pass_id=pass_id)
    if nb.store.current_interpretation(note_id) is not None:
        taken = nb.store.q1("SELECT pass_id FROM reinterpret_marks WHERE note_id=? AND consumed_at IS NULL AND pass_id"
                            " IS NOT NULL ORDER BY mark_id DESC LIMIT 1", (note_id,))
        if taken is not None:  # a consolidation pass took this note's mark: the reading is a re-understanding
            return apply_reinterpretation(nb, note_id, extraction, linking, pass_id=taken[0])
    return apply_note_revision(nb, note_id, extraction, linking)


def settle_container(nb: Any, note_id: str, *, reinterpret: bool, pass_id: Optional[str]) -> Dict[str, Any]:
    """A container note read again (W30): nothing changes (its parts are notes of their own); its re-understanding
    marks are settled as ``unchanged`` and the stage's claim released, as an unchanged re-reading does."""
    current = nb.store.current_interpretation(note_id)
    taken = pass_id
    if not reinterpret and taken is None:
        row = nb.store.q1("SELECT pass_id FROM reinterpret_marks WHERE note_id=? AND consumed_at IS NULL AND pass_id"
                          " IS NOT NULL ORDER BY mark_id DESC LIMIT 1", (note_id,))
        taken = row[0] if row is not None else None
    if reinterpret or taken is not None:
        packet_row = nb.store.q1("SELECT created_at FROM context_packets WHERE note_id=?", (note_id,))
        _consume(nb, note_id, taken, "unchanged", None, packet_row[0] if packet_row else None)
        if taken:
            nb.queue.release(note_id, "reinterpret", taken)
        return {"revision_id": None, "unchanged": True, "previous_revision_id": current,
                "summary": "A document of dated parts: each part is understood as a note of its own.",
                "entities_created": [], "entities_merged": [], "claims_added": [], "claims_reinforced": [],
                "claims_superseded": [], "claims_retracted": [], "tasks_opened": [], "tasks_closed": [],
                "claims_inserted_before": [], "retrospective": [], "warnings": [], "marked": 0,
                "reinterpret_marked": {"count": 0, "notes": [], "by_entity": []}}
    out = nb.apply_revision({"revision_id": current, "at": nb.store.revision_row(current)["at"]})
    out["warnings"] = list(out.get("warnings") or []) + ["This note was already processed; the earlier revision stands."]
    out["already_applied"] = True
    return out
