"""Attachment text extraction with an explicit ``extracted_by``.

Extractors: ``plain`` and ``markdown`` (decoded text), ``pdf`` (AbstractCore's
PDF processor when its optional dependency is installed), ``vision:<model>``
(an injected vision callable), ``stt:<model>`` (an injected speech-to-text
callable), ``media-metadata`` (audio and video without a speech model) and
``none``. A missing extractor keeps the attachment and returns a warning; it
never drops it.

Injected callables: ``vision(data, mime, filename)`` and ``stt(data, mime,
filename)`` return ``(text, model_id)`` or a plain string.
"""

from __future__ import annotations

import mimetypes
import os
import tempfile
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Tuple

VisionFn = Callable[[bytes, str, Optional[str]], Any]
SttFn = Callable[[bytes, str, Optional[str]], Any]

_TEXT_MIMES = {"application/json", "application/xml", "application/x-yaml", "application/yaml", "application/toml",
               "application/javascript", "application/x-sh", "application/sql", "application/x-ndjson"}
_MARKDOWN_EXT = {".md", ".markdown", ".mdx"}


@dataclass
class ExtractResult:
    text: str = ""
    extracted_by: str = "none"
    warnings: List[str] = field(default_factory=list)


def guess_mime(filename: Optional[str], given: Optional[str] = None) -> str:
    if given and given.strip() and given.strip() != "application/octet-stream":
        return given.strip().lower()
    if filename:
        if os.path.splitext(filename)[1].lower() in _MARKDOWN_EXT:
            return "text/markdown"
        guessed, _ = mimetypes.guess_type(filename)
        if guessed:
            return guessed
    return (given or "application/octet-stream").strip().lower()


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


def _model_result(value: Any, prefix: str) -> Tuple[str, str]:
    if isinstance(value, tuple) and len(value) == 2:
        text, model = value
    elif isinstance(value, dict):
        text, model = value.get("text", ""), value.get("model")
    else:
        text, model = value, None
    return str(text or ""), f"{prefix}:{model or 'unknown'}"


def _pdf_text(data: bytes, filename: Optional[str]) -> Optional[str]:
    try:
        from abstractcore.media.processors.pdf_processor import PDFProcessor
    except Exception:
        return None
    with tempfile.TemporaryDirectory(prefix="abstractnote-pdf-") as d:
        path = os.path.join(d, os.path.basename(filename or "document.pdf") or "document.pdf")
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        with open(path, "wb") as f:
            f.write(data)
        try:
            result = PDFProcessor().process_file(path)
        except Exception:
            return None
        content = getattr(getattr(result, "media_content", None), "content", None)
        if not getattr(result, "success", False) or not isinstance(content, str):
            return None
        return content


def _metadata_text(data: bytes, mime: str, filename: Optional[str]) -> str:
    return f"{filename or 'unnamed file'} ({mime}, {len(data)} bytes)"


def extract(data: bytes, mime: Optional[str], filename: Optional[str], *, vision: Optional[VisionFn] = None,
            stt: Optional[SttFn] = None) -> ExtractResult:
    m = guess_mime(filename, mime)
    name = filename or "the attachment"
    ext = os.path.splitext(filename or "")[1].lower()
    if m == "text/markdown" or ext in _MARKDOWN_EXT:
        return ExtractResult(_decode(data), "markdown")
    if m.startswith("text/") or m in _TEXT_MIMES or m.endswith("+json") or m.endswith("+xml"):
        return ExtractResult(_decode(data), "plain")
    if m == "application/pdf":
        text = _pdf_text(data, filename)
        if text is None:
            return ExtractResult("", "none", [f"Kept {name}; its text could not be read here (PDF reading is not "
                                              "installed on this machine)."])
        return ExtractResult(text, "pdf")
    if m.startswith("image/"):
        if vision is None:
            return ExtractResult("", "none", [f"Kept {name}; no image model is configured to describe it."])
        text, by = _model_result(vision(data, m, filename), "vision")
        return ExtractResult(text, by)
    if m.startswith("audio/") or m.startswith("video/"):
        if stt is not None:
            text, by = _model_result(stt(data, m, filename), "stt")
            return ExtractResult(text, by)
        what = "recording" if m.startswith("audio/") else "video"
        return ExtractResult(_metadata_text(data, m, filename), "media-metadata",
                             [f"Kept the {what} {name} with its details; no speech model is configured to transcribe it."])
    return ExtractResult("", "none", [f"Kept {name}; its content type ({m}) cannot be read as text."])
