"""``note_capture`` and ``note_file`` (docs/api.md, "Note tools").

Attachments are ``{"$artifact": id, "run_id"?: ...}`` gateway references,
resolved through the injected ``artifact_reader(ref) -> (bytes, mime,
filename)``, or ``{"path": ...}`` local files, accepted only when the tools
were bound with ``allow_paths_under=ROOT`` and the resolved path is inside it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from .context import ToolContext


def resolve_attachments(ctx: ToolContext, attachments: Optional[List[Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for i, item in enumerate(attachments or []):
        if not isinstance(item, dict):
            raise ValueError(f"Attachment {i + 1} must be an object with $artifact or path.")
        if "$artifact" in item:
            ref = {"$artifact": str(item["$artifact"])}
            if item.get("run_id"):
                ref["run_id"] = str(item["run_id"])
            if ctx.artifact_reader is None:
                raise ValueError("This notebook cannot read gateway attachments: no artifact reader was bound to "
                                 "the note tools (abstractnote.tools.bind(..., artifact_reader=...)).")
            data, mime, filename = ctx.artifact_reader(ref)
            if not isinstance(data, (bytes, bytearray)):
                raise ValueError(f"The attachment {ref['$artifact']} could not be read.")
            out.append({"data": bytes(data), "mime": item.get("mime") or mime,
                        "filename": item.get("filename") or filename,
                        # the file's modification time as the client knows it (web: File.lastModified, epoch ms)
                        "last_modified": item.get("last_modified"),
                        "artifact_ref": {"artifact_id": ref["$artifact"], **({"run_id": ref["run_id"]}
                                                                             if "run_id" in ref else {})}})
        elif "path" in item:
            if ctx.allow_paths_under is None:
                raise ValueError("Local file paths are not accepted here; upload the file as an attachment instead.")
            root = Path(ctx.allow_paths_under).expanduser().resolve()
            path = Path(str(item["path"])).expanduser()
            if not path.is_absolute():
                path = root / path
            path = path.resolve()
            if path != root and root not in path.parents:
                raise ValueError("That file is outside the folder this notebook may read from.")
            if not path.is_file():
                raise ValueError(f"There is no file at {item['path']}.")
            out.append({"data": path.read_bytes(), "mime": item.get("mime"), "filename": item.get("filename") or path.name,
                        "last_modified": item.get("last_modified"), "artifact_ref": None})
        else:
            raise ValueError(f"Attachment {i + 1} must have $artifact (or path when reading local files is allowed).")
    return out


def note_capture(ctx: ToolContext, text: str = "", idempotency_key: Optional[str] = None, title: Optional[str] = None,
                 occurred_at: Optional[str] = None, location: Optional[Dict[str, Any]] = None,
                 source: Optional[Dict[str, Any]] = None, priority: Optional[int] = None,
                 attachments: Optional[List[Any]] = None, session_id: Optional[str] = None,
                 about: Optional[Dict[str, Any]] = None, answer: Optional[Dict[str, Any]] = None,
                 process_inline: bool = False) -> Dict[str, Any]:
    def blank(v: Any) -> Any:  # flows pass "" / {} / [] for unset start pins
        return None if v in ("", {}, []) else v

    saved_answer = _answer(blank(answer))
    resolved = resolve_attachments(ctx, blank(attachments))
    priority = blank(priority)
    result = ctx.nb.capture(
        text, title=blank(title), occurred_at=blank(occurred_at), location=blank(location), source=blank(source),
        priority=None if priority is None else int(priority), attachments=resolved,
        idempotency_key=blank(idempotency_key), session_id=blank(session_id), account=ctx.account, about=blank(about),
        default_client=ctx.default_client, vision=ctx.extractors.get("vision"), stt=ctx.extractors.get("stt"),
        inline=bool(process_inline), inline_run_id=_current_run_id() if process_inline else None,
    )
    out = result.model_dump()
    if saved_answer is not None and result.status == "new":
        out["warnings"] = list(out.get("warnings") or []) + _file_answer(ctx.nb, result.note_id, saved_answer)
    return out


def _current_run_id() -> Optional[str]:
    """The id of the run whose tool call this is, when AbstractRuntime runs it (the inline claim names its run;
    the claim's liveness is its time, so a capture outside a runtime is claimed under ``inline@<time>``)."""
    try:
        from abstractruntime.core.effect_cancellation import current_inflight_effect
    except ImportError:
        return None
    effect = current_inflight_effect()
    return getattr(effect, "run_id", None) if effect is not None else None


def _answer(answer: Any) -> Optional[Dict[str, Any]]:
    """``context.answer = {question, evidence_note_ids}`` (the web app's "Save this answer"), checked."""
    if answer is None:
        return None
    if not isinstance(answer, dict) or not str(answer.get("question") or "").strip():
        raise ValueError("A saved answer needs the question it answers.")
    evidence = answer.get("evidence_note_ids") or []
    if not isinstance(evidence, list) or not all(isinstance(x, str) for x in evidence):
        raise ValueError("The evidence of a saved answer must be a list of note ids.")
    return {"question": str(answer["question"]).strip(), "evidence_note_ids": list(dict.fromkeys(evidence))}


def _file_answer(nb: Any, note_id: str, answer: Dict[str, Any]) -> List[str]:
    """File a saved answer as a ``synthesis`` note linked (``dcterms:references``) to each evidence
    note, the same link ``context.about`` makes; evidence ids outside the notebook are left out."""
    from .. import ids
    from ..store.notes import _j

    warnings: List[str] = []
    now = ids.utc_now()
    detail = _j({"kind": "answer", "question": answer["question"]})
    with nb.store.tx() as c:
        c.execute("UPDATE notes SET kind='synthesis' WHERE note_id=?", (note_id,))
        for nid in answer["evidence_note_ids"]:
            if nid == note_id or nb.store.note_row(nid) is None:
                warnings.append(f"The evidence note {nid} is not in this notebook; it was left out.")
                continue
            c.execute("INSERT OR IGNORE INTO note_links(src_note_id, dst_note_id, predicate, score, matched_by,"
                      " detail_json, created_at) VALUES (?,?,?,1.0,?,?,?)",
                      (note_id, nid, "dcterms:references", "answer", detail, now))
    return warnings


def note_file(ctx: ToolContext, note_id: str, title: Optional[str] = None, kind: Optional[str] = None,
              topics: Optional[List[str]] = None, urgency: Optional[str] = None, language: Optional[str] = None,
              one_line: Optional[str] = None) -> Dict[str, Any]:
    def blank(v: Any) -> Any:
        return None if v in ("", {}, []) else v

    row = ctx.nb.store.note_row(note_id)
    if row is not None and row["kind"] == "synthesis":
        kind = None  # a saved answer stays a synthesis whatever the quick filing proposes
    ctx.nb.queue.heartbeat(note_id)  # an inline capture's claim runs from here into its note-process
    return ctx.nb.file_note(note_id, title=blank(title), kind=blank(kind), topics=topics or [],
                            urgency=blank(urgency), language=blank(language), one_line=blank(one_line))
