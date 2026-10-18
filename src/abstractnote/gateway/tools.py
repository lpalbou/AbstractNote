"""The note tools bound to one gateway plane (review 01 B3, M1, M10).

``bound_tool_map`` is what the gateway's bundle host merges into its per-plane tool map next to
``read_skill``. Inside the gateway the tools never see a filesystem path: attachments are only
``{"$artifact": id}`` references, resolved through the HOST's artifact store (the one the
runtime already uses for uploads), and ``source.account`` is stamped with the plane's account.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from ..flows.build import NOTE_TOOLS

#: The tool names the gateway adds to its approval auto-approve set (they read and write the
#: caller's own notebook only; a capture must never park on an approval wait).
AUTO_APPROVE_TOOL_NAMES: Tuple[str, ...] = tuple(NOTE_TOOLS)

NOTEBOOK_DIRNAME = "abstractnote"


def notebook_dir(data_root: Path) -> Path:
    """The notebook of a plane: ``<plane data dir>/abstractnote``."""
    return Path(data_root) / NOTEBOOK_DIRNAME


class ArtifactReader:
    """``reader(ref) -> (bytes, mime, filename)`` over the host's artifact store.

    Accepts only ``{"$artifact": id, ...}`` (the upload API's reference). Anything else, notably
    ``{"path": ...}``, is refused: inside the gateway a tool runs as the gateway's own user, so a
    path would let any signed-in user read the machine.
    """

    def __init__(self, artifact_store: Any) -> None:
        if artifact_store is None or not callable(getattr(artifact_store, "load", None)):
            raise ValueError("The note tools need the gateway's artifact store.")
        self._store = artifact_store

    def __call__(self, ref: Any) -> Tuple[bytes, str, Optional[str]]:
        if not isinstance(ref, Mapping):
            raise ValueError("An attachment must be an uploaded file reference.")
        if "path" in ref:
            raise ValueError("Files must be uploaded first; paths are not accepted here.")
        artifact_id = str(ref.get("$artifact") or ref.get("artifact_id") or "").strip()
        if not artifact_id:
            raise ValueError("An attachment must be an uploaded file reference.")
        artifact = self._store.load(artifact_id)
        if artifact is None:
            raise ValueError("That attachment was not found; upload it again.")
        meta = artifact.metadata
        tags = dict(getattr(meta, "tags", None) or {})
        filename = tags.get("filename") or ref.get("filename") or None
        mime = str(getattr(meta, "content_type", "") or ref.get("content_type") or "application/octet-stream")
        return bytes(artifact.content), mime, filename


def bound_tool_map(data_root: Path, embedder: Any, artifact_store: Any, account: Optional[str]) -> Dict[str, Callable[..., Any]]:
    """The note tools bound to ``<data_root>/abstractnote`` for one plane.

    ``data_root`` is the plane's data dir (the host's ``data_root``), ``embedder`` the plane's
    embedding route (``None`` keeps the notebook's own pin), ``artifact_store`` the host's
    artifact store and ``account`` the plane's user id. Local paths are never allowed here.
    """
    from abstractnote.tools import bind  # core lane: an ImportError here is a real gap, surfaced

    tools = dict(bind(
        notebook_dir(data_root), embedder=embedder, artifact_reader=ArtifactReader(artifact_store), account=account,
    ))
    missing = sorted(set(AUTO_APPROVE_TOOL_NAMES) - set(tools))
    if missing:
        raise RuntimeError("abstractnote.tools.bind does not provide " + ", ".join(missing))
    return {name: tools[name] for name in AUTO_APPROVE_TOOL_NAMES}
