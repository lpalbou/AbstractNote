"""Shared helpers for the core-library tests (imported by test_store*, test_kg_projection, ...)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from abstractnote import ids
from abstractnote.store.embeddings import HashingEmbedder
from abstractnote.store.notebook import Notebook


class CountingEmbedder(HashingEmbedder):
    """Hashing embedder that counts how many texts it embedded."""

    def __init__(self, dimension: int = 64, model_id: Optional[str] = None) -> None:
        super().__init__(dimension)
        if model_id:
            self.model_id = model_id
        self.calls: List[str] = []

    def embed_texts(self, texts):
        self.calls.extend(texts)
        return super().embed_texts(texts)


@pytest.fixture
def nb(tmp_path):
    notebook = Notebook.open(tmp_path / "nb")
    yield notebook
    notebook.close()


def create(entity_id: str, name: str, kind: Optional[str] = None, aliases=(), summary=None) -> Dict[str, Any]:
    kind = kind or entity_id.split(":", 1)[1].split("-", 1)[0]
    return {"op": "entity.create", "payload": {"entity_id": entity_id, "kind": kind, "name": name,
                                                "aliases": list(aliases), "summary": summary}}


def claim(cid: str, s: str, p: str, o: Optional[str] = None, *, literal: Optional[str] = None, at: str,
          valid_from: Optional[str] = "same", certainty: str = "stated", note_id: Optional[str] = None,
          disputes: Optional[str] = None) -> Dict[str, Any]:
    payload = {"claim_id": cid, "subject_id": s, "predicate": p, "object_id": o, "object_literal": literal,
               "certainty": certainty, "stance": "assert", "observed_at": at,
               "valid_from": at if valid_from == "same" else valid_from, "valid_until": None,
               "evidence": f"{s} {p} {o or literal}", "note_id": note_id}
    if disputes:
        payload["disputes"] = disputes
    return {"op": "claim.add", "payload": payload}


def rev(rid: str, at: str, *ops: Dict[str, Any], note_id: Optional[str] = None, summary: str = "") -> Dict[str, Any]:
    return {"revision_id": rid, "at": at, "note_id": note_id, "ops": list(ops), "summary": summary or rid}


def t(day: int, month: int = 1, year: int = 2026) -> str:
    return ids.normalize_time(f"{year:04d}-{month:02d}-{day:02d}T09:00:00Z")
