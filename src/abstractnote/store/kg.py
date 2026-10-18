"""The AbstractMemory projection: ``ClaimStore`` over ``kg.sqlite3``.

Every claim becomes a ``TripleAssertion`` (``assertion_id = claim_id``); entity
names, aliases and merges become ``schema:name``, ``skos:altLabel`` and
``schema:sameAs`` assertions. The triple store is append-only: the only
in-place change is ``close_assertion(assertion_id, valid_until)`` (an additive
AbstractMemory method), always paired with a journal ``ClosureRecord`` so that
``TripleQuery(active_at=...)`` and ranked retrieval both agree with the note
index. Every query passes ``owner_id="abstractnote"``.

Vectors are never computed here: the note index embeds each canonical text
once (``VectorCache``) and the triple store reads them through
:class:`_LookupEmbedder`, so a rebuild does not call the embedder.
"""

from __future__ import annotations

import sqlite3
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from abstractmemory import ClosureRecord, SQLiteJournal, SQLiteTripleStore, TripleAssertion, TripleQuery
from abstractmemory.canonical_text import canonical_text

from .. import ids
from ..model import CERTAINTY_CONFIDENCE
from .embeddings import VectorCache, vector_key

OWNER_ID = "abstractnote"
SCOPE = "global"
KIND_ATTR = "abstractnote_kind"  # claim | name | alias | same_as


class _LookupEmbedder:
    """Serves vectors the note index already computed; a miss is an error."""

    def __init__(self, cache: VectorCache) -> None:
        self._cache = cache
        self.model_id = cache.model_id

    def embed_texts(self, texts: Sequence[str]) -> List[List[float]]:
        out = []
        for t in texts:
            v = self._cache.get(vector_key(t, self._cache.model_id))
            if v is None:
                raise KeyError("A triple text has no vector in the note index (embed it first).")
            out.append(v)
        return out


def claim_assertion(payload: Dict[str, Any], rev: Dict[str, Any], subject_kind: Optional[str],
                    object_kind: Optional[str], *, run_id: Optional[str] = None) -> TripleAssertion:
    """The ``TripleAssertion`` of one ``claim.add`` payload (docs/data-model.md, projection table)."""
    literal = payload.get("object_id") is None
    attrs: Dict[str, Any] = {
        KIND_ATTR: "claim", "certainty": payload["certainty"], "stance": payload.get("stance") or "assert",
    }
    if subject_kind:
        attrs["subject_type"] = subject_kind
    if object_kind and not literal:
        attrs["object_type"] = object_kind
    if literal:
        attrs["literal"] = True
    if payload.get("evidence"):
        attrs["evidence_quote"] = payload["evidence"]
    prov = {
        "source": "abstractnote", "note_id": payload.get("note_id") or rev.get("note_id"),
        "revision_id": rev["revision_id"], "run_id": run_id or rev.get("run_id"),
        "evidence_quote": payload.get("evidence"), "recorded_at": rev["recorded_at"],
    }
    return TripleAssertion(
        subject=payload["subject_id"], predicate=payload["predicate"],
        object=payload["object_literal"] if literal else payload["object_id"],
        scope=SCOPE, owner_id=OWNER_ID, observed_at=payload["observed_at"],
        valid_from=payload.get("valid_from"), valid_until=payload.get("valid_until"),
        confidence=CERTAINTY_CONFIDENCE.get(payload["certainty"], 0.5),
        provenance={k: v for k, v in prov.items() if v is not None}, attributes=attrs,
        assertion_id=payload["claim_id"],
    )


def label_assertion(entity_id: str, predicate: str, value: str, kind: str, rev: Dict[str, Any]) -> TripleAssertion:
    literal = kind in ("name", "alias")
    attrs: Dict[str, Any] = {KIND_ATTR: kind}
    if literal:
        attrs["literal"] = True
    return TripleAssertion(
        subject=entity_id, predicate=predicate, object=value, scope=SCOPE, owner_id=OWNER_ID,
        observed_at=rev["at"], valid_from=rev["at"] if kind == "same_as" else None,
        provenance={"source": "abstractnote", "revision_id": rev["revision_id"], "recorded_at": rev["recorded_at"],
                    **({"note_id": rev["note_id"]} if rev.get("note_id") else {})},
        attributes=attrs, assertion_id=ids.kg_assertion_id(entity_id, predicate, value),
    )


def _journal_time(ts: str) -> str:
    return ts[:-1] + "+00:00" if ts.endswith("Z") else ts


class ClaimStore:
    """AbstractMemory ``SQLiteTripleStore`` + ``SQLiteJournal`` for one notebook."""

    def __init__(self, path: Path, cache: VectorCache) -> None:
        self.path = Path(path)
        self.cache = cache
        pin: Dict[str, Any] = {"model_id": cache.model_id, "source": "creation"}
        if cache.dimension is not None:
            pin["dimension"] = cache.dimension
        self._embedder = _LookupEmbedder(cache)
        self.store = SQLiteTripleStore(self.path, embedder=self._embedder, embedding_pin=pin)
        self.journal = SQLiteJournal(self.path)

    def close(self) -> None:
        self.store.close()
        self.journal.close()

    # ------------------------------------------------------------------ projection

    def plan(self, rev: Dict[str, Any], kinds: Dict[str, str]) -> Tuple[List[TripleAssertion], List[Dict[str, Any]]]:
        """The assertions to add and the closures to apply for one revision."""
        adds: List[TripleAssertion] = []
        closes: List[Dict[str, Any]] = []
        for op in rev.get("ops") or []:
            name, p = op["op"], op.get("payload") or {}
            if name == "entity.create":
                adds.append(label_assertion(p["entity_id"], "schema:name", p["name"], "name", rev))
                for alias in p.get("aliases") or []:
                    if str(alias).strip() and ids.fold(alias) != ids.fold(p["name"]):
                        adds.append(label_assertion(p["entity_id"], "skos:altLabel", str(alias).strip(), "alias", rev))
            elif name == "entity.alias":
                if str(p.get("alias") or "").strip():
                    adds.append(label_assertion(p["entity_id"], "skos:altLabel", str(p["alias"]).strip(), "alias", rev))
            elif name == "entity.rename":
                adds.append(label_assertion(p["entity_id"], "schema:name", p["name"], "name", rev))
                if p.get("previous"):
                    adds.append(label_assertion(p["entity_id"], "skos:altLabel", p["previous"], "alias", rev))
            elif name == "entity.merge":
                adds.append(label_assertion(p["from_id"], "schema:sameAs", p["into_id"], "same_as", rev))
            elif name == "entity.unmerge":
                closes.append({"assertion_id": ids.kg_assertion_id(p["from_id"], "schema:sameAs", p["entity_id"]),
                               "valid_until": rev["at"], "kind": "retract", "reason": p.get("reason") or "unmerged",
                               "replacement_ids": ()})
            elif name == "claim.add":
                adds.append(claim_assertion(p, rev, kinds.get(p["subject_id"]),
                                            kinds.get(p.get("object_id") or "")))
            elif name == "claim.supersede":
                closes.append({"assertion_id": p["claim_id"], "valid_until": p["valid_until"], "kind": "supersede",
                               "reason": p.get("reason") or "superseded by a later note",
                               "replacement_ids": (p["by_claim_id"],) if p.get("by_claim_id") else ()})
            elif name == "claim.retract":
                closes.append({"assertion_id": p["claim_id"], "valid_until": p.get("valid_until") or rev["at"],
                               "kind": "retract", "reason": p.get("reason") or "retracted", "replacement_ids": ()})
        return adds, closes

    def texts_for(self, adds: Sequence[TripleAssertion]) -> List[str]:
        return [canonical_text(a) for a in adds]

    def project(self, rev: Dict[str, Any], kinds: Dict[str, str]) -> Dict[str, int]:
        """Write one revision to AbstractMemory. Idempotent: ids are deterministic,
        ``add`` ignores known ids, ``close_assertion`` only closes open windows and
        journal closures with a known id are no-ops."""
        adds, closes = self.plan(rev, kinds)
        if adds:
            self.cache.ensure(self.texts_for(adds))
            with warnings.catch_warnings():
                # A vector miss must fail the write, never degrade it to vectorless.
                warnings.filterwarnings("error", message=r"#FALLBACK: embedder failed", category=RuntimeWarning)
                self.store.add(adds)
        closed = 0
        for c in closes:
            if c["kind"] == "supersede" and not c["replacement_ids"]:
                c = dict(c, kind="retract")
            if self.store.close_assertion(c["assertion_id"], c["valid_until"]):
                closed += 1
            self.journal.append_closure(ClosureRecord(
                assertion_id=c["assertion_id"], kind=c["kind"], reason=c["reason"],
                replacement_ids=tuple(c["replacement_ids"]), observed_at=_journal_time(rev["recorded_at"]),
                actor="abstractnote",
                provenance={"revision_id": rev["revision_id"], "note_id": rev.get("note_id"),
                            "valid_until": c["valid_until"]},
                closure_id=ids.closure_id(rev["revision_id"], c["assertion_id"], c["kind"]),
            ))
        return {"added": len(adds), "closed": closed}

    # ------------------------------------------------------------------ reads

    def query(self, **kwargs: Any) -> List[TripleAssertion]:
        kwargs.setdefault("owner_id", OWNER_ID)
        kwargs.setdefault("scope", SCOPE)
        kwargs.setdefault("limit", 0)
        return self.store.query(TripleQuery(**kwargs))

    def active_claim_ids(self, at: str) -> List[str]:
        """Claim assertion ids AbstractMemory considers active at ``at``."""
        rows = self.query(active_at=ids.normalize_time(at, field="at"))
        return sorted(a.assertion_id for a in rows if (a.attributes or {}).get(KIND_ATTR) == "claim")

    def count(self) -> int:
        return len(self.query())

    def embedding_pin(self) -> Optional[dict]:
        return self.store.embedding_pin()


def kinds_from_index(conn: sqlite3.Connection, rev: Dict[str, Any]) -> Dict[str, str]:
    """Entity kinds needed to type a revision's claim assertions."""
    wanted = set()
    for op in rev.get("ops") or []:
        if op["op"] == "claim.add":
            p = op.get("payload") or {}
            wanted.add(p["subject_id"])
            if p.get("object_id"):
                wanted.add(p["object_id"])
    if not wanted:
        return {}
    q = ",".join("?" for _ in wanted)
    return {r[0]: r[1] for r in conn.execute(f"SELECT entity_id, kind FROM entities WHERE entity_id IN ({q})",
                                             list(wanted))}
