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
                        "artifact_ref": None})
        else:
            raise ValueError(f"Attachment {i + 1} must have $artifact (or path when reading local files is allowed).")
    return out


def note_capture(ctx: ToolContext, text: str = "", idempotency_key: Optional[str] = None, title: Optional[str] = None,
                 occurred_at: Optional[str] = None, location: Optional[Dict[str, Any]] = None,
                 source: Optional[Dict[str, Any]] = None, priority: Optional[int] = None,
                 attachments: Optional[List[Any]] = None, session_id: Optional[str] = None,
                 about: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    resolved = resolve_attachments(ctx, attachments)
    result = ctx.nb.capture(
        text, title=title, occurred_at=occurred_at, location=location, source=source,
        priority=None if priority is None else int(priority), attachments=resolved,
        idempotency_key=idempotency_key, session_id=session_id, account=ctx.account, about=about,
        default_client=ctx.default_client, vision=ctx.extractors.get("vision"), stt=ctx.extractors.get("stt"),
    )
    return result.model_dump()


def note_file(ctx: ToolContext, note_id: str, title: Optional[str] = None, kind: Optional[str] = None,
              topics: Optional[List[str]] = None, urgency: Optional[str] = None, language: Optional[str] = None,
              one_line: Optional[str] = None) -> Dict[str, Any]:
    return ctx.nb.file_note(note_id, title=title, kind=kind, topics=topics, urgency=urgency, language=language,
                            one_line=one_line)
