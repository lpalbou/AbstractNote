"""``note_context``, ``note_resolve_candidates``, ``note_apply_revision`` (docs/api.md).

ADR 0008 (lane R2): a note that was understood before is read again (``note.reinterpret``) when the apply is asked to
(``reinterpret=True``) or when a consolidation pass has taken its re-understanding mark: that is the one place the
``already_applied`` answer of a processed note is decided. Its context leaves out its own previous reading and shows
again what that reading had closed (:func:`abstractnote.process.revise.reinterpret_view`). The mention queries a
resolve step answered are kept in the stored packet so a later no-call check can ask them again.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..process.context import load_packet, save_packet
from ..process.context import note_context as _context
from ..process.resolve import resolve_candidates
from ..process.revise import apply_note_revision, apply_reinterpretation, reinterpret_view
from .context import ToolContext


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


def note_apply_revision(ctx: ToolContext, note_id: str, extraction: Any, linking: Dict[str, Any],
                        reinterpret: bool = False, pass_id: Optional[str] = None) -> Dict[str, Any]:
    nb = ctx.nb
    if reinterpret:
        return apply_reinterpretation(nb, note_id, extraction, linking, pass_id=pass_id)
    if nb.store.current_interpretation(note_id) is not None:
        taken = nb.store.q1("SELECT pass_id FROM reinterpret_marks WHERE note_id=? AND consumed_at IS NULL AND pass_id"
                            " IS NOT NULL ORDER BY mark_id DESC LIMIT 1", (note_id,))
        if taken is not None:  # a consolidation pass took this note's mark: the reading is a re-understanding
            return apply_reinterpretation(nb, note_id, extraction, linking, pass_id=taken[0])
    return apply_note_revision(nb, note_id, extraction, linking)
