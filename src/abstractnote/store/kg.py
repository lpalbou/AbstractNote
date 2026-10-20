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
#: ADR 0008: the AbstractMemory closure kind of a statement a re-understanding withdrew (never written as a retraction).
REINTERPRETED_KIND = "reinterpreted"
#: The attribute naming the note-index claim of an assertion that is a later COPY of it (a statement read again with
#: another certainty, or re-opened: AbstractMemory content is append-only and a window cannot widen).
CLAIM_ATTR = "claim_id"


def supports_reinterpreted() -> bool:
    """Whether the installed AbstractMemory accepts the closure kind ``reinterpreted`` (an additive kind of
    abstractmemory 0.3.2; ADR 0008)."""
    try:
        ClosureRecord(assertion_id="probe", kind=REINTERPRETED_KIND, reason="probe")
    except ValueError:
        return False
    return True


def require_reinterpreted() -> None:
    """Fail loudly when the installed AbstractMemory lacks the closure kind (no silent mapping to a retraction)."""
    if not supports_reinterpreted():
        import abstractmemory

        raise RuntimeError(
            f"The installed AbstractMemory ({getattr(abstractmemory, '__version__', 'unknown version')}) has no closure"
            " kind 'reinterpreted', which understanding a note again needs (ADR 0008); install an abstractmemory that"
            " has it.")


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
    if payload.get("of_claim_id"):  # a copy of a note-index claim (claim.revise / claim.reopen, ADR 0008)
        attrs[CLAIM_ATTR] = payload["of_claim_id"]
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


#: Attribute naming the claim an earlier-evidence assertion belongs to (``claim.start_earlier``).
EARLIER_OF_ATTR = "earlier_evidence_of"


def earlier_evidence_assertion(payload: Dict[str, Any], rev: Dict[str, Any], subject_kind: Optional[str],
                               object_kind: Optional[str]) -> Optional[TripleAssertion]:
    """The additive assertion of a ``claim.start_earlier``: the same statement from the earlier note, valid from the
    new start to the claim's previous start (``observed_at`` = the earlier note's date). None when the window did
    not move (no known start, or it already covered the date): nothing to add. Its id is deterministic from the
    claim and the earlier note, so a replay adds nothing twice."""
    start, before = payload.get("valid_from"), payload.get("previous_valid_from")
    if not start or not before or start >= before:
        return None
    base = claim_assertion({**payload, "valid_from": start, "valid_until": before,
                            "observed_at": payload["observed_at"],
                            "claim_id": ids.claim_id(payload.get("note_id") or rev.get("note_id") or "",
                                                     payload["claim_id"], "claim.start_earlier", start)},
                           rev, subject_kind, object_kind)
    attrs = dict(base.attributes or {})
    attrs[EARLIER_OF_ATTR] = payload["claim_id"]
    return TripleAssertion(
        subject=base.subject, predicate=base.predicate, object=base.object, scope=base.scope, owner_id=base.owner_id,
        observed_at=base.observed_at, valid_from=base.valid_from, valid_until=base.valid_until,
        confidence=base.confidence, provenance=base.provenance, attributes=attrs, assertion_id=base.assertion_id)


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
            elif name == "claim.insert_before":
                # The older copy closes at the later belief's date (that belief is never closed by an older note);
                # the belief before it, already closed later, has its window moved back to where this one starts.
                closes.append({"assertion_id": p["claim_id"], "valid_until": p["valid_until"], "kind": "supersede",
                               "reason": p.get("reason") or "an earlier belief, replaced later",
                               "replacement_ids": (p["before_claim_id"],)})
                if p.get("after_claim_id") and p.get("after_valid_until"):
                    closes.append({"assertion_id": p["after_claim_id"], "valid_until": p["after_valid_until"],
                                   "kind": "supersede", "narrow": True,
                                   "reason": p.get("reason") or "replaced by an earlier-found belief",
                                   "replacement_ids": (p["claim_id"],)})
            elif name == "claim.start_earlier":
                # TripleAssertion content is append-only: the earlier evidence is an ADDITIVE assertion of the same
                # statement for the moved part of the window [new start, old start), pointing at the claim
                # (``earlier_evidence_of``), so ``active_at`` agrees with the note index; the belief before it is
                # narrowed like an insert_before's (data-model.md "AbstractMemory projection").
                earlier = earlier_evidence_assertion(p, rev, kinds.get(p["subject_id"]),
                                                     kinds.get(p.get("object_id") or ""))
                if earlier is not None:
                    adds.append(earlier)
                if p.get("after_claim_id") and p.get("after_valid_until"):
                    closes.append({"assertion_id": p["after_claim_id"], "valid_until": p["after_valid_until"],
                                   "kind": "supersede", "narrow": True,
                                   "reason": p.get("reason") or "an earlier note shows the later belief held then",
                                   "replacement_ids": (p["claim_id"],)})
            elif name == "claim.retract":
                closes.append({"assertion_id": p["claim_id"], "valid_until": p.get("valid_until") or rev["at"],
                               "kind": "retract", "reason": p.get("reason") or "retracted", "replacement_ids": ()})
            elif name == "claim.reinterpret":
                # ADR 0008: the withdrawn reading's copy closes empty (at its own start), whatever closed it before
                closes.append({"assertion_id": p.get("assertion_id") or p["claim_id"], "valid_until": p["valid_until"],
                               "kind": REINTERPRETED_KIND, "narrow": True, "replacement_ids": (),
                               "reason": p.get("reason") or "the note was understood again"})
            elif name in ("claim.revise", "claim.reopen"):
                # the statement as it now reads is a new copy (content is append-only, a window cannot widen); the
                # copy it replaces closes empty with the kind reinterpreted, naming the new one
                a = dict(p["assertion"])
                adds.append(claim_assertion(a, rev, kinds.get(a["subject_id"]), kinds.get(a.get("object_id") or "")))
                closes.append({"assertion_id": p["previous_assertion_id"], "valid_until": p["empty_at"],
                               "kind": REINTERPRETED_KIND, "narrow": True, "replacement_ids": (a["claim_id"],),
                               "reason": p.get("reason") or ("read again" if name == "claim.revise" else
                                                             "re-opened: the reading that closed it was withdrawn")})
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
        if any(c["kind"] == REINTERPRETED_KIND for c in closes):
            require_reinterpreted()
        for c in closes:
            if c["kind"] == "supersede" and not c["replacement_ids"]:
                c = dict(c, kind="retract")
            if self.store.close_assertion(c["assertion_id"], c["valid_until"]):
                closed += 1
            elif c.get("narrow") and self.narrow_assertion(c["assertion_id"], c["valid_until"]):
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

    def narrow_assertion(self, assertion_id: str, valid_until: str) -> bool:
        """Move an already-closed window EARLIER (a retrospective note placed between two beliefs). The only
        in-place change besides ``close_assertion``, and like it limited to ``valid_until``: the content, provenance,
        FTS row and vector are untouched. AbstractMemory has no public method for it (``close_assertion`` only
        closes open windows), so this writes the store's own table through its connection; a store without them
        fails loudly (seam: an additive ``narrow_assertion`` in AbstractMemory should replace this)."""
        store = self.store
        table, conn, lock = store._table, store._conn, store._lock  # noqa: SLF001 - see the docstring
        with lock:
            cur = conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            try:
                cur.execute(f"UPDATE {table} SET valid_until=? WHERE assertion_id=? AND valid_until IS NOT NULL AND"
                            " valid_until>?", (valid_until, assertion_id, valid_until))
                changed = int(cur.rowcount or 0) > 0
                cur.execute("COMMIT")
            except BaseException:
                cur.execute("ROLLBACK")
                raise
        return changed

    # ------------------------------------------------------------------ reads

    def query(self, **kwargs: Any) -> List[TripleAssertion]:
        kwargs.setdefault("owner_id", OWNER_ID)
        kwargs.setdefault("scope", SCOPE)
        kwargs.setdefault("limit", 0)
        return self.store.query(TripleQuery(**kwargs))

    def active_claim_ids(self, at: str) -> List[str]:
        """Note-index claim ids AbstractMemory considers active at ``at`` (a copy written by a re-understanding names
        its claim in ``attributes.claim_id``)."""
        rows = self.query(active_at=ids.normalize_time(at, field="at"))
        # an earlier-evidence assertion (claim.start_earlier) stands for the claim it moved back; a copy written by
        # a re-understanding names its claim
        return sorted({(a.attributes or {}).get(EARLIER_OF_ATTR) or (a.attributes or {}).get(CLAIM_ATTR)
                       or a.assertion_id for a in rows if (a.attributes or {}).get(KIND_ATTR) == "claim"})

    def count(self) -> int:
        return len(self.query())

    def embedding_pin(self) -> Optional[dict]:
        return self.store.embedding_pin()


def kinds_from_index(conn: sqlite3.Connection, rev: Dict[str, Any]) -> Dict[str, str]:
    """Entity kinds needed to type a revision's claim assertions."""
    wanted = set()
    for op in rev.get("ops") or []:
        if op["op"] in ("claim.add", "claim.revise", "claim.reopen", "claim.start_earlier"):
            p = op.get("payload") or {}
            p = p.get("assertion") or p if op["op"] != "claim.add" else p
            wanted.add(p["subject_id"])
            if p.get("object_id"):
                wanted.add(p["object_id"])
    if not wanted:
        return {}
    q = ",".join("?" for _ in wanted)
    return {r[0]: r[1] for r in conn.execute(f"SELECT entity_id, kind FROM entities WHERE entity_id IN ({q})",
                                             list(wanted))}
