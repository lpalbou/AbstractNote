"""``Notebook``: one directory, two stores, one revision log.

Layout of a notebook directory (``<plane>/abstractnote/`` inside a gateway)::

    note.sqlite3   the note index (truth tables + projections)
    kg.sqlite3     AbstractMemory SQLiteTripleStore + SQLiteJournal (claims)
    blobs/         content-addressed attachment bytes
    extracted/     text extracted from attachments

Revisions are the truth. :meth:`Notebook.apply_revision` is the only path that
changes entities, claims, relations, topics and tasks, and it is idempotent.
Write order: revision row (intent) -> index projection (one transaction that
sets ``applied_index``) -> vectors -> AbstractMemory projection -> ``applied_kg``.
:meth:`Notebook.recover` replays revisions whose flags are not both set. Reads never recover:
only writes (capture, filing, processing, user actions) and an explicit ``abstractnote recover``
do, so a read in one process never finishes a revision another process is still applying.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from .. import __version__, ids
from ..model import Revision, RevisionOp
from .embeddings import (
    HashingEmbedder,
    TextEmbedder,
    VectorCache,
    embedder_for_pin,
    embedder_model_id,
    vector_key,
)
from .kg import ClaimStore, claim_assertion, kinds_from_index
from .names import merge_ops, merge_plan, reuse_taken_names
from .notes import REINTERPRET_ACTION, NoteStore, _j, _uj, entity_text, note_vector_text
from .schema import DEPENDENCY_RECORD_META, KEPT_TABLES, NAME_KEY_REPAIR_META

NOTE_DB = "note.sqlite3"
KG_DB = "kg.sqlite3"
NOTE_EMBED_BUDGET = 8000  # characters of a note handed to the embedder (the stored note is never cut)

DEFAULT_SETTINGS: Dict[str, Any] = {
    "filing_model": {"provider": None, "model": None},
    "answering_model": {"provider": None, "model": None},
    "reflect_schedule": {"every": "7d"},
    # The processor automation's throughput: up to ``max_items`` notes per drain, one drain ``every``.
    # Equal to abstractnote.flows.build DRAIN_DEFAULTS / the note-drain trigger (a test keeps them equal).
    "processor_schedule": {"every": "2m", "max_items": 20},
    "process_inline_default": False,
    # The consolidation automation's interval (backlog 0032): "<n>h" or "<n>d", or "off" (kept paused).
    "consolidate_schedule": {"every": "1d"},
}
PROCESSOR_MAX_ITEMS = 200
#: Retrospective notes since the last community computation that make a recomputation due (retro_state).
RETRO_COMMUNITY_NOTES = 20
#: The stored context packet keys today's ``note_context`` and ``note_resolve_candidates`` write.
PACKET_KEYS = ("candidate_entities", "existing_claims", "mentions")


def context_digest(store: NoteStore, note_row: Any, packet: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The dependency record of a revision (ADR 0008 §3, ``revisions.context_json``): what it SAW, ``{as_of,
    entities, claims, summaries: {entity_id: revision_id}, notes (the similar notes shown), mention_queries,
    later_claims, omitted, looked_up}``, from the stored context packet as the apply finds it (``note_context`` and
    ``note_resolve_candidates`` both add to it; the dated digest of ``process.context`` (``packet.digest``, or the
    revision builder's ``context``) is merged in: its as_of, entities, claims, known-then summaries, the LATER
    claims offered and what the packet budget left out). A packet without its keys is an error (never a silent empty
    record); a note applied without any packet records ``looked_up: false``. What the revision USED is
    ``note_dependencies``, the invalidation key."""
    as_of = note_row["occurred_at"]
    if packet is None:
        return {"as_of": as_of, "entities": [], "claims": [], "summaries": {}, "notes": [], "mention_queries": [],
                "looked_up": False}
    missing = [k for k in PACKET_KEYS if k not in packet]
    if missing:
        raise RuntimeError("The note's stored context has no " + ", ".join(missing)
                           + "; it cannot record what the revision depended on (ADR 0008).")
    r1 = next((d for d in (packet.get("context"), packet.get("digest")) if isinstance(d, dict)), {})
    entities: List[str] = []
    for eid in [*(r1.get("entities") or []), *packet["candidate_entities"],
                *(e for ids_ in packet["mentions"].values() for e in ids_)]:
        if eid and eid not in entities:
            entities.append(eid)
    claims: List[str] = []
    for cid in [*(r1.get("claims") or []), *packet["existing_claims"]]:
        if cid and cid not in claims:
            claims.append(cid)
    summaries: Dict[str, Any] = dict(r1.get("summaries") or {})
    for eid in entities:  # the summary known at the note's date (the latest written for a date on or before it)
        if eid not in summaries:
            r = store.q1("SELECT revision_id FROM entity_summaries WHERE entity_id=? AND at<=? ORDER BY at DESC,"
                         " rowid DESC LIMIT 1", (eid, r1.get("as_of") or as_of))
            summaries[eid] = r[0] if r else None
    notes = [n for n in [*(r1.get("notes") or []), *(packet.get("similar_notes") or [])] if n]
    return {"as_of": r1.get("as_of") or as_of, "entities": entities, "claims": claims, "summaries": summaries,
            "notes": list(dict.fromkeys(notes)), "mention_queries": list(packet.get("mention_queries") or []),
            "later_claims": list(r1.get("later_claims") or []), "omitted": dict(r1.get("omitted") or {}),
            # ADR 0008 §4a: the ideas this note relies on that were understood differently, as known at its date when
            # it was read again through a propagated mark (the no-call check compares them)
            "related": dict(r1.get("related") or {}),
            "looked_up": True}


def _check_consolidate_schedule(value: Any) -> Dict[str, Any]:
    """``{every: "<n>h|d" | "off"}`` or a plain sentence (ValueError)."""
    if not isinstance(value, dict) or set(value) != {"every"}:
        raise ValueError("The consolidation schedule is {every}, for example {\"every\": \"1d\"} or \"off\".")
    every = str(value["every"]).strip().lower()
    if every != "off" and not (every[:-1].isdigit() and every[-1:] in ("h", "d") and int(every[:-1]) > 0):
        raise ValueError("The consolidation interval must be a number of hours or days (12h, 1d, 7d) or off.")
    return {"every": every}


def _check_processor_schedule(value: Any) -> Dict[str, Any]:
    """``{every: "<n>s|m|h|d", max_items: 1..200}`` or a plain sentence (ValueError)."""
    if not isinstance(value, dict) or set(value) - {"every", "max_items"}:
        raise ValueError("The processor schedule is {every, max_items}.")
    every = str(value.get("every", DEFAULT_SETTINGS["processor_schedule"]["every"])).strip()
    if not (every[:-1].isdigit() and every[-1:] in ("s", "m", "h", "d") and int(every[:-1]) > 0):
        raise ValueError("The processor interval must be a number with s, m, h or d, for example 2m.")
    max_items = value.get("max_items", DEFAULT_SETTINGS["processor_schedule"]["max_items"])
    if isinstance(max_items, bool) or not isinstance(max_items, int) or not 1 <= max_items <= PROCESSOR_MAX_ITEMS:
        raise ValueError(f"The processor takes between 1 and {PROCESSOR_MAX_ITEMS} notes at a time.")
    return {"every": every, "max_items": max_items}


class _Keys:
    """Vector keys for the index projection of one revision."""

    def __init__(self, cache: VectorCache, rev: Dict[str, Any]) -> None:
        self.cache = cache
        self.rev = rev
        self.texts: List[str] = []

    def entity(self, text: str) -> str:
        self.texts.append(text)
        return vector_key(text, self.cache.model_id)

    def claim(self, payload: Dict[str, Any], conn: sqlite3.Connection) -> str:
        from abstractmemory.canonical_text import canonical_text

        kinds = {r[0]: r[1] for r in conn.execute(
            "SELECT entity_id, kind FROM entities WHERE entity_id IN (?, ?)",
            (payload["subject_id"], payload.get("object_id") or ""))}
        text = canonical_text(claim_assertion(payload, self.rev, kinds.get(payload["subject_id"]),
                                              kinds.get(payload.get("object_id") or "")))
        self.texts.append(text)
        return vector_key(text, self.cache.model_id)


class Notebook:
    """A notebook directory: the note index, the AbstractMemory projection, blobs and the queue."""

    def __init__(self, root: Path, store: NoteStore, cache: VectorCache, kg: ClaimStore) -> None:
        self.root = Path(root)
        self.store = store
        self.cache = cache
        self.kg = kg
        self._apply_lock = threading.RLock()
        from .queue import Queue

        self.queue = Queue(store)
        self._normalizing = False
        from .vector_index import NoteVectorIndex

        self.note_vectors = NoteVectorIndex(store)
        # Injected processing helpers (bound by abstractnote.tools.bind or by callers)
        self.extractors: Dict[str, Callable[..., Any]] = {}

    # ------------------------------------------------------------------ open / close

    @classmethod
    def open(cls, root: "str | os.PathLike[str]", *, embedder: Optional[TextEmbedder] = None,
             recover: bool = False) -> "Notebook":
        """Open (and create when missing) a notebook directory.

        ``embedder`` selects the embedding space. A new notebook without one uses
        the deterministic :class:`HashingEmbedder`. A notebook pinned to another
        model must be opened with that model (or rebuilt with ``reembed=True``).

        Opening never replays half-applied revisions unless ``recover=True``: the
        write paths (capture, filing, processing, user actions) recover first, and
        reads leave another process's revision alone (:meth:`recover_pending`).
        """
        root = Path(root).expanduser()
        root.mkdir(parents=True, exist_ok=True)
        (root / "blobs").mkdir(exist_ok=True)
        (root / "extracted").mkdir(exist_ok=True)
        store = NoteStore(str(root / NOTE_DB))
        try:
            cache = cls._make_cache(store, embedder)
            kg = ClaimStore(root / KG_DB, cache)
        except BaseException:
            store.close()
            raise
        nb = cls(root, store, cache, kg)
        if recover and nb.cache.embedder is not None:
            nb.recover()
        return nb

    @staticmethod
    def _make_cache(store: NoteStore, embedder: Optional[TextEmbedder], *, force_new_pin: bool = False
                    ) -> VectorCache:
        pin = None if force_new_pin else store.meta_get("embedding_pin")
        if pin:
            if embedder is None:
                embedder = embedder_for_pin(pin)
                if embedder is None:
                    # Read-only for meaning: everything that needs no new vector still works.
                    cache = VectorCache(store.conn, store.lock, None, pin.get("model_id"), pin.get("dimension"))
                    cache.missing_embedder = (
                        f"This notebook's vectors were made with the embedding model {pin.get('model_id')!r}; "
                        "open it with that embedder to save notes, process them or search by meaning, or rebuild "
                        "it with reembed to change models.")
                    return cache
            known = embedder_model_id(embedder)
            if known and pin.get("model_id") and known != pin["model_id"] and pin["model_id"] != "unknown":
                raise ValueError(
                    f"This notebook is pinned to the embedding model {pin['model_id']!r} but the embedder given is "
                    f"{known!r}; rebuild with reembed to change models."
                )
            return VectorCache(store.conn, store.lock, embedder, pin.get("model_id"), pin.get("dimension"))
        if embedder is None:
            embedder = HashingEmbedder()
        model_id = embedder_model_id(embedder) or "unknown"
        dimension = getattr(embedder, "dimension", None)
        cache = VectorCache(store.conn, store.lock, embedder, model_id, dimension)
        store.meta_set("embedding_pin", {"model_id": model_id, "dimension": dimension,
                                         "pinned_at": ids.utc_now()})
        return cache

    def _sync_pin(self) -> None:
        pin = self.store.meta_get("embedding_pin") or {}
        if pin.get("dimension") is None and self.cache.dimension is not None:
            pin["dimension"] = self.cache.dimension
            self.store.meta_set("embedding_pin", pin)

    def close(self) -> None:
        self.kg.close()
        self.store.close()

    def __enter__(self) -> "Notebook":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @property
    def embedder(self) -> Optional[TextEmbedder]:
        return self.cache.embedder

    @property
    def read_only(self) -> Optional[str]:
        """Why this notebook cannot embed (opened without the embedder it is pinned to), or None."""
        return self.cache.missing_embedder if self.cache.embedder is None else None

    def require_embedder(self) -> None:
        """Raise the plain sentence when an operation would need new vectors and there is no embedder."""
        if self.cache.embedder is None:
            raise ValueError(self.cache.missing_embedder)

    # ------------------------------------------------------------------ revisions

    def apply_revision(self, revision: "Revision | Dict[str, Any]", *, _fail_after_index: bool = False
                       ) -> Dict[str, Any]:
        """Apply a revision to both stores. Re-applying the same ``revision_id`` is a no-op
        that returns the stored result. Raises on invalid ops (nothing is applied)."""
        rev = self._rev_dict(revision)
        self.require_embedder()
        with self._apply_lock:
            existing = self.store.revision_row(rev["revision_id"])
            if existing is not None:
                if existing["abandoned"]:
                    raise ValueError(f"The revision {rev['revision_id']} failed before and was set aside.")
                if existing["applied_index"] and existing["applied_kg"]:
                    return self._result(existing, already_applied=True)
                return self._finish(self.store.revision_dict(existing), _fail_after_index=_fail_after_index)
            # One canonical name per entity: a create of a taken canonical name becomes a reuse, whatever
            # the model decided (store.names); the stored revision is the rewritten one, so a replay agrees.
            rev = reuse_taken_names(self.store, rev)
            given = rev.get("context")
            if rev.get("note_id") and rev.get("action") in (None, REINTERPRET_ACTION) and (
                    given is None or "looked_up" not in given):
                from ..process.context import load_packet

                note_row = self.store.note_row(rev["note_id"])
                if note_row is None:
                    raise ValueError("That note is not in this notebook.")
                packet = load_packet(self, rev["note_id"])
                if isinstance(given, dict) and packet is not None:  # R1's digest, from the revision builder
                    packet = {**packet, "context": given}
                rev = {**rev, "context": context_digest(self.store, note_row, packet)}
            self.store.insert_revision(rev)
            return self._finish(rev, _fail_after_index=_fail_after_index)

    def _rev_dict(self, revision: "Revision | Dict[str, Any]") -> Dict[str, Any]:
        if isinstance(revision, Revision):
            data = revision.model_dump()
        else:
            data = dict(revision)
        data.setdefault("recorded_at", None)
        data["recorded_at"] = data["recorded_at"] or ids.utc_now()
        data["at"] = ids.normalize_time(data["at"], field="revision time")
        ops = []
        for op in data.get("ops") or []:
            ops.append(op.model_dump() if isinstance(op, RevisionOp) else RevisionOp(**op).model_dump())
        data["ops"] = ops
        if not data.get("revision_id"):
            raise ValueError("A revision needs a revision_id.")
        return data

    def _finish(self, rev: Dict[str, Any], *, _fail_after_index: bool = False) -> Dict[str, Any]:
        row = self.store.revision_row(rev["revision_id"])
        stats: Dict[str, Any] = {}
        if not row["applied_index"]:
            keys = _Keys(self.cache, rev)
            try:
                stats = self.store.project(rev, keys=keys)
            except Exception as exc:
                self.store.abandon_revision(rev["revision_id"], f"{type(exc).__name__}: {exc}")
                raise
            self.cache.ensure(keys.texts)
            self._sync_pin()
            result = dict(_uj(row["result_json"], {}) or {})
            result.update({k: v for k, v in stats.items() if k not in ("touched_entities", "touched_claims")})
            with self.store.lock:
                self.store.conn.execute("UPDATE revisions SET result_json=? WHERE revision_id=?",
                                        (_j(result), rev["revision_id"]))
        if _fail_after_index:
            raise RuntimeError("Simulated stop between the index and the AbstractMemory commits.")
        if rev.get("action") == REINTERPRET_ACTION and self.store.marking:
            self._propagate(rev["revision_id"])
        elif rev.get("note_id") and not rev.get("action") and self.store.marking:
            self._gate(rev["revision_id"])
        row = self.store.revision_row(rev["revision_id"])
        if not row["applied_kg"]:
            # W31: an op the index refused (a supersession invariant) is not written to AbstractMemory either, so
            # the two stores agree; AbstractMemory checks the same invariants on its own state (``kg_refused``)
            result = dict(_uj(row["result_json"], {}) or {})
            refused = {int(x["index"]) for x in result.get("ops_refused") or [] if isinstance(x, dict)}
            kg_rev = {**rev, "ops": [op for i, op in enumerate(rev.get("ops") or []) if i not in refused]} \
                if refused else rev
            with self.store.lock:
                kinds = kinds_from_index(self.store.conn, kg_rev)
            projected = self.kg.project(kg_rev, kinds)
            if projected.get("refused"):
                result["kg_refused"] = projected["refused"]
                with self.store.lock:
                    self.store.conn.execute("UPDATE revisions SET result_json=? WHERE revision_id=?",
                                            (_j(result), rev["revision_id"]))
            self.store.set_applied_kg(rev["revision_id"])
        return self._result(self.store.revision_row(rev["revision_id"]))

    def _propagate(self, revision_id: str) -> None:
        """ADR 0008 §4a: measure how deeply an applied re-understanding changed each idea it touched and spread its
        deep changes to the later notes that relied on them (``recall.propagation.propagate``: the change records and
        the marks in one transaction). Done once per re-understanding (``result.propagation``), redone by
        :meth:`recover` when a stop came between the apply and the spread, never during a rebuild (the records are
        truth, copied). Never without the notebook's embedder: it fails loudly."""
        self.require_embedder()
        row = self.store.revision_row(revision_id)
        result = dict(_uj(row["result_json"], {}) or {})
        if "propagation" in result:
            return
        from ..recall.propagation import propagate

        out = propagate(self, revision_id)
        if out.get("reinterpret_marked") is not None:  # the gate decided the direct candidates (ADR 0008 §4 amended)
            result["reinterpret_marked"] = out.pop("reinterpret_marked")
        result["propagation"] = out
        with self.store.lock:
            self.store.conn.execute("UPDATE revisions SET result_json=? WHERE revision_id=?",
                                    (_j(result), revision_id))

    def _gate(self, revision_id: str) -> None:
        """AbstractMemory ADR 0001 rule 7 (ADR 0008 §4 amended): decide the candidates of an applied first reading
        (``recall.activation.gate_first_reading``: what it measurably changed about each entity a later note used,
        divided by the entity's mass, weighted by how much the later note relies on it, accumulated, through the
        logistic gate). Done once (``result.gate``), completed by :meth:`recover` after a stop, never during a
        rebuild (the decisions and the marks are truth, copied)."""
        row = self.store.revision_row(revision_id)
        result = dict(_uj(row["result_json"], {}) or {})
        if "gate" in result:
            return
        from ..recall.activation import gate_first_reading

        out = gate_first_reading(self, revision_id)
        result["gate"] = {"candidates": out["candidates"], "marked": out["marked"]}
        if out.get("reinterpret_marked") is not None:
            result["reinterpret_marked"] = out["reinterpret_marked"]
        with self.store.lock:
            self.store.conn.execute("UPDATE revisions SET result_json=? WHERE revision_id=?",
                                    (_j(result), revision_id))

    def _result(self, row: sqlite3.Row, *, already_applied: bool = False) -> Dict[str, Any]:
        result = dict(_uj(row["result_json"], {}) or {})
        out = {
            "revision_id": row["revision_id"], "summary": row["summary"],
            "entities_created": result.get("entities_created", []),
            "entities_merged": result.get("entities_merged", []),
            "claims_added": result.get("claims_added", []),
            "claims_reinforced": result.get("claims_reinforced", []),
            "claims_superseded": result.get("claims_superseded", []),
            "claims_retracted": result.get("claims_retracted", []),
            "tasks_opened": result.get("tasks_opened", []),
            "tasks_closed": result.get("tasks_closed", []),
            "claims_inserted_before": result.get("claims_inserted_before", []),
            "retrospective": result.get("retro_entities", []),
            "warnings": list(_uj(row["warnings_json"], []) or []),
            # ADR 0008: how many later notes this revision marked for re-understanding, and why (review 3.2)
            "marked": int((result.get("reinterpret_marked") or {}).get("count") or 0),
            "reinterpret_marked": result.get("reinterpret_marked") or {"count": 0, "notes": [], "by_entity": []},
        }
        for k in ("claims_reinterpreted", "claims_revised", "claims_reopened", "claims_start_restored",
                  "reinforcements_dropped", "ops_refused", "kg_refused",
                  "tasks_reopened", "tasks_retired", "entities_retired", "stale_context", "reinterpreted",
                  "propagation", "gate"):
            if result.get(k):
                out[k] = result[k]
        if already_applied:
            out["already_applied"] = True
        return out

    def recover(self) -> List[str]:
        """Finish every revision whose projections are not both applied. Returns their ids."""
        done = []
        with self._apply_lock:
            for row in self.store.pending_revisions():
                self._finish(self.store.revision_dict(row))
                done.append(row["revision_id"])
        return done

    def recover_pending(self) -> List[str]:
        """Recovery before a write: :meth:`recover` when this notebook can embed (a notebook opened
        without its embedder cannot write, and the write itself says so). Read paths never call it."""
        if self.cache.embedder is None:
            return []
        return self.recover()

    def repair_names_if_pending(self) -> Optional[Dict[str, Any]]:
        """A notebook upgraded from before canonical names (schema 2 flags it) merges what shares one, once:
        :meth:`normalize_entities` with source client ``migration``. Called by processing (a batch step, never a
        capture waiting for an answer); None when there is nothing to repair or no embedder."""
        if self.cache.embedder is None or self._normalizing or \
                self.store.meta_get(NAME_KEY_REPAIR_META) != "pending":
            return None
        return self.normalize_entities(None, client="migration")

    def revision(self, revision_id: str) -> Optional[Dict[str, Any]]:
        row = self.store.revision_row(revision_id)
        return self.store.revision_dict(row) if row else None

    # ------------------------------------------------------------------ user actions (M5)

    def _user_revision(self, action: str, target: str, ops: List[Dict[str, Any]], account: Optional[str],
                       summary: str, *, client: str = "web", occurred_at: Optional[str] = None) -> Dict[str, Any]:
        at = ids.normalize_time(occurred_at) if occurred_at else ids.utc_now()
        rev = {
            "revision_id": ids.revision_id_for_action(action, target, at), "note_id": None, "action": action,
            "target": target, "at": at, "ops": ops, "summary": summary,
            "source": {"client": client, "account": account},
        }
        self.recover_pending()
        return self.apply_revision(rev)

    def close_task(self, task_id: str, account: Optional[str], note: Optional[str] = None, *,
                   client: str = "web", occurred_at: Optional[str] = None) -> Dict[str, Any]:
        row = self.store.q1("SELECT * FROM tasks WHERE task_id=?", (task_id,))
        if row is None:
            raise ValueError("That task is not in this notebook.")
        if row["status"] != "open":
            raise ValueError("That task is already closed.")
        summary = f"Closed the task “{row['text']}”." + (f" {note}" if note else "")
        return self._user_revision("task.close", task_id, [{"op": "task.close", "payload": {
            "task_id": task_id, "text": row["text"], "by_note_id": None, "note": note}}], account, summary,
            client=client, occurred_at=occurred_at)

    def redate_note(self, note_id: str, occurred_at: Any, account: Optional[str], reason: str = "", *,
                    client: str = "web") -> Dict[str, Any]:
        """Set when a note happened (``POST /note/notes/{id}/redate``, basis ``user``): see
        :func:`abstractnote.ingest.dates.redate_note` (an understood note keeps its statements and is understood
        again at its new date with the notes that depend on it; refused while that is not possible)."""
        from ..ingest.dates import redate_note

        return redate_note(self, note_id, occurred_at, account, reason, client=client)

    def _redate_op(self, note_id: str, occurred_at: Any, account: Optional[str], reason: str = "", *,
                   client: str = "web", keep_statements: bool = False) -> Dict[str, Any]:
        """The user revision of a re-date: one ``note.redate`` op (basis ``user``). The note, its mentions, its
        topics' ranges and its tasks move to the new date; so do the statements it wrote with the old date unless
        ``keep_statements`` (an understood note: its statements wait for its re-understanding)."""
        from ..ingest.dates import USER

        row = self.store.note_row(note_id)
        if row is None:
            raise ValueError("That note is not in this notebook.")
        if occurred_at in (None, ""):
            raise ValueError("Give the date the note was written.")
        new = ids.normalize_time(occurred_at, field="occurred_at")
        if new == row["occurred_at"]:
            raise ValueError("The note already has that date.")
        reason = str(reason or "").strip()
        day = new[:10]
        return self._user_revision("note.redate", note_id, [{"op": "note.redate", "payload": {
            "note_id": note_id, "from": row["occurred_at"], "to": new, "basis": USER,
            **({"keep_statements": True} if keep_statements else {}),
            **({"reason": reason} if reason else {})}}], account,
            f"Set the note's date to {day}." + (f" {reason}" if reason else ""), client=client)

    def after_retro_insert(self, touched_entities: Iterable[str]) -> Dict[str, Any]:
        """Record a retrospective insertion by hand (processing records it itself: a note older than what was
        known about an entity, a statement placed before a later one, a redate): the entities' summaries are
        marked stale, their then-versus-now cards dropped, ``graph_version`` bumped (:meth:`retro_state`)."""
        eids = [self.store.canonical(e) for e in touched_entities if self.store.entity_row(e) is not None]
        with self.store.tx() as c:
            self.store.mark_retro(c, eids, None, None, ids.utc_now())
        return self.retro_state()

    def retro_state(self, threshold: Optional[int] = None) -> Dict[str, Any]:
        """What retrospective insertions left to refresh (for the consolidation): ``{graph_version, insertions,
        stale_summaries: [{entity_id, marked_seq}], retro_notes_since_communities, communities_due, threshold}``.
        A summary stays stale until a summary recorded after the mark is the entity's current one;
        ``communities_due`` once ``threshold`` (default :data:`RETRO_COMMUNITY_NOTES`) retrospective notes arrived
        since the communities were last computed."""
        return self.store.retro_state(threshold=RETRO_COMMUNITY_NOTES if threshold is None else int(threshold))

    # ------------------------------------------------------------------ ADR 0008: re-understanding marks

    def reinterpret_state(self) -> Dict[str, Any]:
        """What waits to be understood again and why (``NoteStore.reinterpret_state``), readable before the
        consolidation's ``reinterpret`` stage runs; ``propagated`` adds ``may_follow`` and ``may_follow_from``: the
        upper bound of the notes ideas understood differently may add (``recall.propagation.may_follow``, ADR 0008
        §4a); ``candidates``, ``marked``, ``top_sources``, ``would_mark_at``, ``theta``, ``floor`` and ``threshold``
        come from the activation gate (``recall.activation.gate_state``)."""
        from ..recall.activation import gate_state
        from ..recall.propagation import may_follow

        out = self.store.reinterpret_state()
        bound = may_follow(self)
        out["propagated"].update({"may_follow": bound["may_follow"], "may_follow_from": bound["by_source"]})
        # AbstractMemory ADR 0001 rule 7: what the activation gate decided over the open candidates (and would decide
        # with θ scaled by 0.8 and 1.2), readable before the run so the thresholds are tuned from the record
        out.update(gate_state(self))
        return out

    def idea_changes(self, revision_id: str) -> List[Dict[str, Any]]:
        """How deeply a re-understanding changed each idea it touched, and where a deep change spread
        (``idea_changes``, ADR 0008 §4a)."""
        from ..recall.propagation import changes_of

        return changes_of(self.store, revision_id)

    def mark_reinterpret(self, note_ids: Sequence[str], reason: str = "") -> Dict[str, Any]:
        """Mark notes for re-understanding by hand (``note_mark_reinterpret``): each must have been understood
        already. Returns ``{marked: [note_id], already: [note_id]}`` (an untaken mark is never doubled)."""
        why = " ".join(str(reason or "").split()) or "Asked to be understood again."
        rows = []
        for nid in note_ids:
            row = self.store.note_row(str(nid))
            if row is None:
                raise ValueError(f"The note {nid} is not in this notebook.")
            if self.store.current_interpretation(row["note_id"]) is None:
                raise ValueError(f"The note {nid} has not been understood yet; it is understood when its turn comes.")
            rows.append({"note_id": row["note_id"], "reason": why, "from_at": row["occurred_at"]})
        with self.store.tx() as c:
            new = self.store.mark_reinterpret(c, rows, ids.utc_now())
        return {"marked": new, "already": [r["note_id"] for r in rows if r["note_id"] not in new]}

    def mark_forward(self, note_id: str, from_at: Optional[str] = None, reason: str = "", *,
                     include_self: bool = False) -> Dict[str, Any]:
        """Mark the notes on or after ``from_at`` (default: the note's date) whose current interpretation used what
        this note's interpretation used (ADR 0008 §4; the API a date change or an archive elsewhere calls, review
        3.4). ``include_self`` marks the note too. Returns ``{marked: [note_id]}``."""
        row = self.store.note_row(note_id)
        if row is None:
            raise ValueError("That note is not in this notebook.")
        frm = ids.normalize_time(from_at, field="from_at") if from_at else row["occurred_at"]
        why = " ".join(str(reason or "").split()) or f"A note it builds on ({row['occurred_at'][:10]}) changed."
        with self.store.tx() as c:
            used = self.store.used_by(c, note_id)
            deps = self.store.later_dependents(c, used["entity"], from_at=frm, exclude=[note_id])
            rows = [{"note_id": n, "reason": why, "by_note_id": note_id, "entities": d["entities"], "from_at": frm}
                    for n, d in deps.items()]
            if include_self and self.store.current_interpretation(note_id, c) is not None:
                rows.append({"note_id": note_id, "reason": why, "entities": used["entity"], "from_at": frm})
            new = self.store.mark_reinterpret(c, rows, ids.utc_now())
        return {"marked": new}

    def interpretations(self, note_id: str) -> List[Dict[str, Any]]:
        """The note's interpretation history (ADR 0008, review 4.4): ``[{revision_id, at, recorded_at, action,
        summary, context: {as_of, entities, claims, summaries} | null, context_unknown, superseded_by}]``, oldest
        first (``action`` null for the first understanding, ``note.reinterpret`` for each re-understanding)."""
        sup = {r[0]: r[1] for r in self.store.q("SELECT revision_id, superseded_by FROM revision_supersessions WHERE"
                                                " note_id=?", (note_id,))}
        out = []
        for r in self.store.q("SELECT * FROM revisions WHERE note_id=? AND applied_index=1 AND abandoned=0 AND (action"
                              " IS NULL OR action=?) ORDER BY seq", (note_id, REINTERPRET_ACTION)):
            ctx = _uj(r["context_json"])
            out.append({"revision_id": r["revision_id"], "at": r["at"], "recorded_at": r["recorded_at"],
                        "action": r["action"], "summary": r["summary"],
                        "context": ({k: ctx.get(k) for k in ("as_of", "entities", "claims", "summaries")}
                                    if isinstance(ctx, dict) else None),
                        "context_unknown": ctx is None, "superseded_by": sup.get(r["revision_id"]),
                        # ADR 0008 §4a: how deeply this re-understanding changed each idea, and how far it spread
                        "changes": [{"entity_id": x["entity_id"], "name": self.store._name(x["entity_id"]),
                                     "magnitude": x["magnitude"], "deep": x["deep"],
                                     "notes_marked": sum(len(f.get("notes") or [])
                                                         for f in (x["spread_json"] or {}).get("reached") or [])}
                                    for x in self.idea_changes(r["revision_id"])]
                        if r["action"] == REINTERPRET_ACTION else []})
        return out

    def retract_claim(self, claim_id: str, account: Optional[str], reason: str, *, client: str = "web",
                      occurred_at: Optional[str] = None) -> Dict[str, Any]:
        row = self.store.claim_row(claim_id)
        if row is None:
            raise ValueError("That statement is not in this notebook.")
        if row["status"] == "retracted":
            raise ValueError("That statement was already retracted.")
        if not str(reason or "").strip():
            raise ValueError("Say why the statement is wrong.")
        # The person says it was never true: the window closes where it opens (an empty window),
        # never at the moment they said so, which can be before the claim's start.
        never = row["valid_from"] or row["observed_at"]
        return self._user_revision("claim.retract", claim_id, [{"op": "claim.retract", "payload": {
            "claim_id": claim_id, "reason": reason, "valid_until": never}}], account,
            f"Retracted a statement: {reason}", client=client, occurred_at=occurred_at)

    def unmerge_entity(self, entity_id: str, from_id: str, account: Optional[str], reason: str, *,
                       client: str = "web", occurred_at: Optional[str] = None) -> Dict[str, Any]:
        row = self.store.entity_row(from_id)
        if row is None or self.store.entity_row(entity_id) is None:
            raise ValueError("That entity is not in this notebook.")
        if row["merged_into"] != entity_id:
            raise ValueError("Those two entities are not merged.")
        return self._user_revision("entity.unmerge", f"{entity_id}|{from_id}", [{"op": "entity.unmerge", "payload": {
            "entity_id": entity_id, "from_id": from_id, "reason": reason or "undone by the user"}}], account,
            f"Separated {row['name']} again.", client=client, occurred_at=occurred_at)

    def normalize_entities(self, account: Optional[str], *, client: str = "cli", dry_run: bool = False
                           ) -> Dict[str, Any]:
        """Merge every group of live entities that share a canonical name into the earliest one, with the entries
        that are such a name with its kind echoed in parentheses (``abstractnote normalize``,
        ``POST /note/entities/normalize``; :func:`abstractnote.store.names.merge_plan`).

        One ``entity.merge`` revision per merged entry, source ``{client, account}``, each reversible with
        :meth:`unmerge_entity`; the merged entry's names stay as aliases, and reads follow the merge for its
        statements, relations, notes and tasks. ``dry_run`` only lists the groups. Returns ``{dry_run, groups:
        [{into_id, from_ids, name, names, kinds, kind_suffix}], merges: [{from_id, into_id, revision_id}]}``.
        """
        plan = merge_plan(self.store)
        out: Dict[str, Any] = {"dry_run": dry_run, "groups": plan, "merges": []}
        if dry_run:
            return out
        self.require_embedder()
        self._normalizing = True
        try:
            self.recover_pending()
            for group in plan:
                for from_id in group["from_ids"]:
                    ops, summary = merge_ops(self.store, group["into_id"], from_id,
                                             kind_suffix=from_id in group.get("kind_suffix", ()))
                    res = self._user_revision("entity.merge", f"{group['into_id']}|{from_id}|normalize", ops,
                                              account, summary, client=client)
                    out["merges"].append({"from_id": from_id, "into_id": group["into_id"],
                                          "revision_id": res["revision_id"]})
            with self.store.tx() as c:
                c.execute("DELETE FROM then_now_cache")
                c.execute("DELETE FROM meta WHERE key=?", (NAME_KEY_REPAIR_META,))
        finally:
            self._normalizing = False
        return out

    def normalize_topics(self, account: Optional[str] = None, *, client: str = "cli", dry_run: bool = False
                         ) -> Dict[str, Any]:
        """Give every topic one spelling (its canonical name: case, -, _ and whitespace only), in one revision
        (``abstractnote normalize-topics``; :mod:`abstractnote.store.topics`)."""
        from .topics import normalize_topics

        return normalize_topics(self, account, client=client, dry_run=dry_run)

    def dedupe_sources(self, account: Optional[str] = None, *, client: str = "cli", dry_run: bool = False
                       ) -> Dict[str, Any]:
        """Archive the later copy of each memory file imported by two importers, in one revision
        (``abstractnote dedupe-sources``; :mod:`abstractnote.importers.dedupe`)."""
        from ..importers.dedupe import dedupe_sources

        return dedupe_sources(self, account, client=client, dry_run=dry_run)

    # ------------------------------------------------------------------ capture and filing (0002)

    def capture(self, text: str = "", **kwargs: Any) -> Any:
        """Store a note now; see :func:`abstractnote.ingest.capture.capture`. Returns a ``CaptureResult``."""
        from ..ingest.capture import capture

        kwargs.setdefault("vision", self.extractors.get("vision"))
        kwargs.setdefault("stt", self.extractors.get("stt"))
        self.recover_pending()
        return capture(self, text, **kwargs)

    def file_note(self, note_id: str, **kwargs: Any) -> Dict[str, Any]:
        from ..ingest.capture import file_note

        self.recover_pending()
        return file_note(self, note_id, **kwargs)

    def note(self, note_id: str) -> Optional[Dict[str, Any]]:
        """A note in the pinned shape of ``GET /note/notes/{id}``: ``{note_id, title, text, kind, occurred_at,
        date_basis, date_approximate, received_at, status, topics, summary, source, location, attachments, revision,
        entities, claims, revisits, part_of, children, insight}``.

        ``part_of`` is the container (a ``NoteRef``) of a dated part, null otherwise; ``children`` a container's dated
        parts in history order, [] otherwise (W30).

        ``date_basis`` says how ``occurred_at`` was found (:func:`abstractnote.ingest.dates.date_basis`) and
        ``date_approximate`` whether it is a stand-in a person may correct ("Set the date").

        ``revision`` is the note's applied revision (or null); ``revisits`` lists the notes linked to it by
        ``ex:revisits`` in either direction as ``NoteRef``; ``insight`` is ``{kind, evidence_note_ids, entity_ids,
        acked_at}`` for an insight note (``kind`` is its ``insight_kind``) and null for every other note.
        """
        from ..recall import shapes

        row = self.store.note_row(note_id)
        if row is None:
            return None
        atts = self.store.attachments_of(note_id)
        for a in atts:
            path = self.root / "extracted" / f"{a['sha256']}.txt"
            a["extracted_text"] = path.read_text(encoding="utf-8") if a["extracted_chars"] and path.exists() else None
        rev = self.store.q1("SELECT * FROM revisions WHERE note_id=? AND applied_index=1 ORDER BY seq DESC LIMIT 1",
                            (note_id,))
        revisits = self.store.q(
            "SELECT n.* FROM note_links l JOIN notes n ON n.note_id=l.dst_note_id WHERE l.src_note_id=?"
            " AND l.predicate='ex:revisits' UNION SELECT n.* FROM note_links l JOIN notes n"
            " ON n.note_id=l.src_note_id WHERE l.dst_note_id=? AND l.predicate='ex:revisits'"
            " ORDER BY occurred_at, note_id", (note_id, note_id))
        from ..ingest.dates import date_basis, is_approximate

        basis = date_basis(row)
        return {
            "note_id": row["note_id"], "title": row["title"], "text": row["text"], "kind": row["kind"],
            "occurred_at": row["occurred_at"], "date_basis": basis, "date_approximate": is_approximate(basis),
            "received_at": row["received_at"], "status": row["status"],
            "topics": self.store.note_topics(note_id), "summary": row["summary"] or row["one_line"],
            "source": _uj(row["source_json"], {}), "location": _uj(row["location_json"]),
            "attachments": atts,
            "revision": self.store.revision_dict(rev) if rev else None,
            "entities": [b for b in (self.store.entity_brief(r[0]) for r in self.store.q(
                "SELECT entity_id FROM entity_notes WHERE note_id=? ORDER BY entity_id", (note_id,))) if b],
            "claims": [self.store.claim_dict(r) for r in self.store.q(
                "SELECT * FROM claims WHERE note_id=? ORDER BY seq", (note_id,))],
            "revisits": [shapes.note_ref(r) for r in revisits],
            # W30: the container this dated part belongs to, and a container's parts in history order
            "part_of": (shapes.note_ref(self.store.note_row(row["part_of"])) if row["part_of"]
                        and self.store.note_row(row["part_of"]) is not None else None),
            "children": [shapes.note_ref(r) for r in self.store.parts_of(note_id)],
            "insight": shapes.insight_facts(row),
            "interpretations": self.interpretations(note_id),
            # ADR 0008: why the note waits to be understood again (the reasons of its pending marks), [] when it does not
            "reinterpret_pending": next((m["reasons"] for m in self.store.pending_marks() if m["note_id"] == note_id), []),
            # W31 (F6): why the last pass could not read it again (held for the day, held by another reading, the
            # error of a failed reading), the sentence its pending mark keeps; null when none
            "reinterpret_error": next((m["error"] for m in self.store.pending_marks() if m["note_id"] == note_id), None),
        }

    def note_links(self, note_id: str) -> Dict[str, Any]:
        """Every link of a note (revisits both ways, same bytes, references) with how it matched."""
        out = {"outgoing": [], "incoming": []}
        for r in self.store.q("SELECT * FROM note_links WHERE src_note_id=? ORDER BY created_at", (note_id,)):
            out["outgoing"].append({"note_id": r["dst_note_id"], "predicate": r["predicate"],
                                    "matched_by": r["matched_by"], "score": r["score"],
                                    "detail": _uj(r["detail_json"])})
        for r in self.store.q("SELECT * FROM note_links WHERE dst_note_id=? ORDER BY created_at", (note_id,)):
            out["incoming"].append({"note_id": r["src_note_id"], "predicate": r["predicate"],
                                    "matched_by": r["matched_by"], "score": r["score"],
                                    "detail": _uj(r["detail_json"])})
        return out

    def attachment_bytes(self, note_id: str, attachment_id: str) -> Dict[str, Any]:
        """``{data, mime, filename, size}`` of one attachment of a note."""
        row = self.store.q1("SELECT * FROM attachments WHERE note_id=? AND attachment_id=?", (note_id, attachment_id))
        if row is None:
            raise ValueError("That attachment is not part of this note.")
        from ..ingest.blobs import BlobStore

        data = BlobStore(self.root / "blobs").read(row["sha256"])
        return {"data": data, "mime": row["mime"], "filename": row["filename"], "size": row["size"]}

    def archive_note(self, note_id: str, account: Optional[str] = None, reason: str = "", *, client: str = "web"
                     ) -> Dict[str, Any]:
        """Archive a note (never deleted; ``POST /note/notes/{id}/archive``): it leaves lists and search, and its
        reading is withdrawn with it (W28, :mod:`abstractnote.process.withdraw`): its statements close as a
        retraction ("note archived"), what it had closed re-opens, entities and tasks only it made retire, its
        mentions, topics and summaries leave the views, and the later notes that built on it are marked for
        re-understanding. One user revision ``note.archive``; :meth:`unarchive_note` undoes it. A note archived before
        its reading was withdrawn with it (an older archive) is withdrawn now; archiving it again otherwise changes
        nothing. Returns ``{note_id, status, archived_at, revision_id, withdrawn: {...}, marked}``."""
        from ..process.withdraw import is_withdrawn

        row = self.store.note_row(note_id)
        if row is None:
            raise ValueError("That note is not in this notebook.")
        if row["archived_at"] and is_withdrawn(self.store, note_id):
            return {"note_id": note_id, "status": "archived", "archived_at": row["archived_at"], "revision_id": None,
                    "withdrawn": None, "marked": 0, "already_archived": True}
        title = row["title"] or " ".join(str(row["text"] or "").split())[:60] or "a note"
        out = self.archive_notes([note_id], account, reason, client=client, target=note_id,
                                 summary=f"Archived the note “{title}”." + (f" {reason}" if reason else ""))
        out.update(note_id=note_id, status="archived", archived_at=self.store.note_row(note_id)["archived_at"])
        return out

    def archive_notes(self, note_ids: Sequence[str], account: Optional[str], reason: str = "", *,
                      client: str = "web", target: Optional[str] = None, summary: Optional[str] = None,
                      action: str = "note.archive", duplicate_of: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Archive several notes in ONE user revision, each reading withdrawn (:meth:`archive_note`). ``target`` names
        the revision (default: the notes); ``action`` is ``note.archive`` (``note.dedupe`` for duplicate copies,
        ``duplicate_of`` naming the copy kept). Returns the apply result plus ``withdrawn`` (counts per kind)."""
        from ..process.withdraw import plan_archive

        nids = list(dict.fromkeys(str(n) for n in note_ids))
        if not nids:
            raise ValueError("Say which notes to archive.")
        self.require_embedder()
        self.recover_pending()
        tgt = target or ("notes:" + ids.sha256_text("|".join(sorted(nids)))[:16])
        at, rid = self._free_action_id(action, tgt)
        plan = plan_archive(self, nids, rid, reason=reason, duplicate_of=duplicate_of)
        n = len(nids)
        res = self._user_revision(action, tgt, plan["ops"], account,
                                  summary or f"Archived {n} note{'s' if n != 1 else ''}.", client=client,
                                  occurred_at=at)
        if res["revision_id"] != rid:  # the plan's copies are named after this revision
            raise RuntimeError("The archive's revision id changed while it was planned.")
        res["withdrawn"] = plan["diff"]
        return res

    def _free_action_id(self, action: str, target: str) -> "tuple[str, str]":
        """``(at, revision_id)`` of a new user action on ``target``: the id is deterministic from (action, target,
        time), so an archive, an unarchive and an archive again within one second take the next free second."""
        from datetime import timedelta

        t = ids.parse_time(ids.utc_now())
        while True:
            at = t.strftime("%Y-%m-%dT%H:%M:%SZ")
            rid = ids.revision_id_for_action(action, target, at)
            if self.store.revision_row(rid) is None:
                return at, rid
            t += timedelta(seconds=1)

    def unarchive_note(self, note_id: str, account: Optional[str] = None, *, client: str = "web") -> Dict[str, Any]:
        """Bring an archived note back (``POST /note/notes/{id}/unarchive``, W28): the inverse of its archive, op by
        op (its statements re-open as they were, what it had closed closes again, its tasks, entities, mentions,
        topics and summaries come back), while what the archive set still stands; the later notes that build on it
        are marked again. Returns ``{note_id, status, archived_at: null, revision_id, restored: {...}, warnings,
        marked}``."""
        row = self.store.note_row(note_id)
        if row is None:
            raise ValueError("That note is not in this notebook.")
        title = row["title"] or " ".join(str(row["text"] or "").split())[:60] or "a note"
        out = self.unarchive_notes([note_id], account, client=client, target=note_id,
                                   summary=f"Brought the note “{title}” back from the archive.")
        out.update(note_id=note_id, status=self.store.note_row(note_id)["status"], archived_at=None)
        return out

    def unarchive_notes(self, note_ids: Sequence[str], account: Optional[str], *, client: str = "web",
                        target: Optional[str] = None, summary: Optional[str] = None) -> Dict[str, Any]:
        """Bring several archived notes back in ONE user revision ``note.unarchive`` (:meth:`unarchive_note`)."""
        from ..process.withdraw import UNARCHIVE_ACTION, plan_unarchive

        nids = list(dict.fromkeys(str(n) for n in note_ids))
        if not nids:
            raise ValueError("Say which notes to bring back.")
        self.require_embedder()
        self.recover_pending()
        tgt = target or ("notes:" + ids.sha256_text("|".join(sorted(nids)))[:16])
        at, rid = self._free_action_id(UNARCHIVE_ACTION, tgt)
        plan = plan_unarchive(self, nids, rid)
        n = len(nids)
        res = self._user_revision(UNARCHIVE_ACTION, tgt, plan["ops"], account,
                                  summary or f"Brought {n} note{'s' if n != 1 else ''} back from the archive.",
                                  client=client, occurred_at=at)
        if res["revision_id"] != rid:
            raise RuntimeError("The revision id changed while it was planned.")
        res["restored"] = plan["diff"]
        res["warnings"] = list(res.get("warnings") or []) + plan["warnings"]
        return res

    def unarchive_revision(self, revision_id: str, account: Optional[str] = None, *, client: str = "cli"
                           ) -> Dict[str, Any]:
        """Bring back, in one revision, every note an archive revision archived that is still archived (the undo of
        ``archive-insights --repeated`` or of any batch archive). ``400`` (ValueError) when it archived none."""
        row = self.store.revision_row(revision_id)
        if row is None or row["action"] not in ("note.archive", "note.dedupe"):
            raise ValueError("That is not an archive of notes in this notebook.")
        nids = [o["payload"]["note_id"] for o in _uj(row["ops_json"], []) or [] if o.get("op") == "note.archive"]
        live = [n for n in nids if (r := self.store.note_row(n)) is not None and r["archived_at"] is not None]
        if not live:
            raise ValueError("Every note that archive put away is back already.")
        out = self.unarchive_notes(live, account, client=client, target=f"undo:{revision_id}",
                                   summary=f"Brought back {len(live)} archived note{'s' if len(live) != 1 else ''}.")
        out["notes"] = live
        return out

    def archive_repeated_insights(self, *, dry_run: bool = False, account: Optional[str] = None,
                                  client: str = "cli") -> Dict[str, Any]:
        """Archive repeated insights, keeping the oldest unacknowledged one of each (``abstractnote archive-insights
        --repeated``, ``POST /note/insights/archive-repeated``; :mod:`abstractnote.recall.repeated_insights`)."""
        from ..recall.repeated_insights import archive_repeated

        return archive_repeated(self, dry_run=dry_run, account=account, client=client)

    def repeated_insights_cleanup(self) -> Optional[Dict[str, Any]]:
        """The latest repeated-insights cleanup row (``GET /note/insights/archive-repeated``), or None."""
        from ..recall.repeated_insights import latest

        return latest(self)

    def ack_insight(self, note_id: str) -> Dict[str, Any]:
        row = self.store.note_row(note_id)
        if row is None or not row["insight_kind"]:
            raise ValueError("That insight is not in this notebook.")
        now = ids.utc_now()
        with self.store.tx() as c:
            c.execute("UPDATE notes SET acked_at=COALESCE(acked_at, ?) WHERE note_id=?", (now, note_id))
        return {"note_id": note_id, "acked_at": self.store.note_row(note_id)["acked_at"]}

    # ------------------------------------------------------------------ reads (store-level)

    def beliefs_at(self, at: str, *, entity_id: Optional[str] = None, subject_id: Optional[str] = None
                   ) -> List[Dict[str, Any]]:
        return self.store.beliefs_at(at, entity_id=entity_id, subject_id=subject_id)

    def graph(self, *, focus: Optional[Sequence[str]] = None, at: Optional[str] = None, depth: int = 1,
              since: Optional[str] = None, until: Optional[str] = None, kinds: Optional[Sequence[str]] = None,
              min_count: int = 1, limit: int = 200, as_of: Optional[str] = None,
              as_received: Optional[str] = None) -> Dict[str, Any]:
        """Nodes and edges as of ``at``; ``as_received`` keeps what the archive held on that date
        (docs/data-model.md "Time"). Each node carries ``community_id`` (its community as last computed, null for a
        loose end or before the first computation)."""
        from ..recall.communities import community_ids

        out = self.store.graph(focus=focus, at=at or as_of, depth=depth, since=since, until=until, kinds=kinds,
                               min_count=min_count, limit=limit, as_received=as_received)
        groups = community_ids(self)
        for node in out["nodes"]:
            node["community_id"] = groups.get(node["id"])
        return out

    def day(self, day: str, tz: Optional[str] = None, as_received: Optional[str] = None) -> Dict[str, Any]:
        return self.store.day(day, tz, as_received)

    def timeline(self, since: Optional[str] = None, until: Optional[str] = None, granularity: str = "day"
                 ) -> Dict[str, Any]:
        return self.store.timeline(since=since, until=until, granularity=granularity)

    def notes(self, **kwargs: Any) -> Dict[str, Any]:
        return self.store.list_notes(**kwargs)

    def entities(self, **kwargs: Any) -> Dict[str, Any]:
        return self.store.list_entities(**kwargs)

    def revisions(self, **kwargs: Any) -> Dict[str, Any]:
        return self.store.revisions_feed(**kwargs)

    def topics(self) -> Dict[str, Any]:
        return {"items": self.store.topics()}

    def tasks(self, status: Optional[str] = "open", entity: Optional[str] = None, limit: int = 100, *,
              now: Optional[str] = None) -> Dict[str, Any]:
        """``{items: [{task_id, note_id, text, status, due, closed_at, age_days, stale}]}`` (``status=None`` for
        all). ``age_days`` counts whole days since the task opened (until it closed); ``stale`` marks an open task
        older than :data:`abstractnote.recall.reflect.STALE_DAYS` whose date is not an approximation (a commit or a
        file date never makes a task stale)."""
        from ..recall.reflect import loop_age, note_date_is_approximate

        now_n = ids.normalize_time(now) if now else ids.utc_now()
        keys = ("task_id", "note_id", "text", "status", "due", "closed_at")
        return {"items": [{**{k: t[k] for k in keys},
                           **loop_age(t["opened_at"], now_n, t["closed_at"],
                                      approximate=note_date_is_approximate(self, t["note_id"]))}
                          for t in self.store.tasks(status=status, entity=entity, limit=limit)]}

    def insights(self, kind: Optional[str] = None, acked: Optional[bool] = None, limit: int = 50
                 ) -> Dict[str, Any]:
        """``{items: [Insight]}``, newest first."""
        from ..recall import shapes
        from .notes import clamp_limit

        sql = "SELECT * FROM notes WHERE insight_kind IS NOT NULL AND archived_at IS NULL"
        params: List[Any] = []
        if kind:
            sql += " AND insight_kind=?"
            params.append(kind)
        if acked is True:
            sql += " AND acked_at IS NOT NULL"
        elif acked is False:
            sql += " AND acked_at IS NULL"
        sql += " ORDER BY received_at DESC, note_id DESC LIMIT ?"
        params.append(clamp_limit(limit))
        return {"items": [shapes.insight(r) for r in self.store.q(sql, params)]}

    def changes(self, since: Optional[str] = None, limit: int = 50, as_received: Optional[str] = None
                ) -> Dict[str, Any]:
        """Since you last looked; see :func:`abstractnote.recall.changes.changes`."""
        from ..recall.changes import changes

        return changes(self, since, limit, as_received)

    def overview(self, days: int = 30, *, now: Optional[str] = None) -> Dict[str, Any]:
        from ..recall.changes import overview

        return overview(self, days, now=now)

    def cues(self, limit: int = 3) -> Dict[str, Any]:
        from ..recall.changes import cues

        return cues(self, limit)

    def review(self, limit: int = 50) -> Dict[str, Any]:
        """Proposed matches and disagreements for a person (``GET /note/review``); see :mod:`abstractnote.recall.review`."""
        from ..recall.review import review

        return review(self, limit)

    def review_item(self, review_id: str) -> Optional[Dict[str, Any]]:
        """One item of :meth:`review` by id (None when it is not waiting for review)."""
        from ..recall.review import _public, review_item

        item = review_item(self, review_id)
        return _public(item) if item else None

    def confirm_match(self, review_id: str, account: Optional[str], *, client: str = "web",
                      occurred_at: Optional[str] = None) -> Dict[str, Any]:
        """Confirm a proposed match: a user-action revision ``match.confirm`` (see :mod:`abstractnote.recall.review`)."""
        from ..recall.review import confirm

        return confirm(self, review_id, account, client=client, occurred_at=occurred_at)

    # ------------------------------------------------------------------ recall (0006)

    def search(self, query: str = "", **kwargs: Any) -> Dict[str, Any]:
        """Quick recall: full-text plus vector over notes, entities and claims, with facets."""
        from ..recall.search import search

        return search(self, query, **kwargs)

    def entity(self, entity_id: str, at: Optional[str] = None, as_received: Optional[str] = None) -> Dict[str, Any]:
        """The entity card as of ``at`` (merged ids resolve to the surviving entity); ``as_received`` builds it from
        what the archive held on that date."""
        from ..recall.communities import community_of
        from ..recall.expand import entity_card

        card = entity_card(self, entity_id, at, as_received)
        card["community"] = community_of(self, card["entity_id"])
        return card

    def entity_record(self, entity_id: str) -> Dict[str, Any]:
        """The entry's own record, not followed to the surviving entity of a merge (``GET /note/entities/{id}?
        as_merged=true``): ``{entity_id, kind, name, aliases, summary, first_seen, last_seen, note_count, merged_into,
        merged_at, merge, notes}``. ``merge`` is the revision that joined it into ``merged_into`` (``{revision_id, at,
        recorded_at, reason, summary, note_id}``) or null when it is not merged; ``notes`` are its own notes."""
        from ..recall import shapes

        brief = self.store.entity_brief(entity_id)
        if brief is None:
            raise ValueError("That entity is not in this notebook.")
        row = self.store.entity_row(entity_id)
        merge = None
        if row["merged_into"]:
            m = self.store.q1("SELECT m.*, r.summary, r.note_id, r.recorded_at FROM merges m JOIN revisions r ON"
                              " r.revision_id=m.revision_id WHERE m.from_id=? AND m.into_id=? AND m.undone_at IS NULL"
                              " ORDER BY m.at DESC LIMIT 1", (entity_id, row["merged_into"]))
            if m is not None:
                merge = {"revision_id": m["revision_id"], "at": m["at"], "recorded_at": m["recorded_at"],
                         "reason": m["reason"], "summary": m["summary"], "note_id": m["note_id"]}
        notes = self.store.q("SELECT n.* FROM entity_notes en JOIN notes n ON n.note_id=en.note_id WHERE"
                             " en.entity_id=? AND n.archived_at IS NULL ORDER BY n.occurred_at DESC LIMIT 10",
                             (entity_id,))
        return {**brief, "merged_at": row["merged_at"], "merge": merge, "notes": [shapes.note_ref(r) for r in notes]}

    def entity_timeline(self, entity_id: str, since: Optional[str] = None, until: Optional[str] = None
                        ) -> Dict[str, Any]:
        from ..recall.expand import entity_timeline

        return entity_timeline(self, entity_id, since, until)

    def then_now(self, entity_id: str) -> Dict[str, Any]:
        from ..recall.then_now import then_now

        return then_now(self, entity_id)

    def relation(self, a: str, b: str, since: Optional[str] = None, until: Optional[str] = None) -> Dict[str, Any]:
        """The relationship between two entities through time (M6)."""
        from ..recall.expand import relation

        return relation(self, a, b, since, until)

    def claim(self, claim_id: str) -> Dict[str, Any]:
        """Why do I believe this: the evidence chain of a claim."""
        from ..recall.expand import claim_chain

        return claim_chain(self, claim_id)

    def expand(self, note_ids: Optional[Sequence[str]] = None, entity_ids: Optional[Sequence[str]] = None,
               as_of: Optional[str] = None, *, query: Optional[str] = None, budget_chars: Optional[int] = None
               ) -> Dict[str, Any]:
        """The standard recall packet within a character budget (:func:`abstractnote.recall.expand.expand`)."""
        from ..recall.expand import expand

        return expand(self, note_ids or [], entity_ids or [], as_of, query=query, budget_chars=budget_chars)

    def write_insight(self, kind: str, title: str, body: str, evidence_note_ids: Optional[Sequence[str]] = None,
                      entity_ids: Optional[Sequence[str]] = None, run_id: Optional[str] = None) -> Dict[str, Any]:
        from ..recall.insights import write_insight

        return write_insight(self, kind, title, body, evidence_note_ids, entity_ids, run_id)

    def open_loops(self, limit: int = 20, since: Optional[str] = None, *, now: Optional[str] = None) -> Dict[str, Any]:
        from ..recall.reflect import open_loops

        return open_loops(self, limit, since, now=now)

    def contradictions(self, limit: int = 20, since: Optional[str] = None) -> Dict[str, Any]:
        from ..recall.reflect import contradictions

        return contradictions(self, limit, since)

    def resurface(self, limit: int = 20, since: Optional[str] = None, *, now: Optional[str] = None) -> Dict[str, Any]:
        from ..recall.reflect import resurface

        return resurface(self, limit, since, now=now)

    def anniversaries(self, limit: int = 20, since: Optional[str] = None, *, now: Optional[str] = None
                      ) -> Dict[str, Any]:
        from ..recall.reflect import anniversaries

        return anniversaries(self, limit, since, now=now)

    # ------------------------------------------------------------------ communities and statistics (0030)

    def communities(self, recompute: bool = False, resolution: Optional[float] = None) -> Dict[str, Any]:
        """The communities of the entity graph, largest first (:func:`abstractnote.recall.communities.communities`);
        ``recompute=True`` runs detection first (a write: refused on a read-only notebook)."""
        from ..recall import communities as cm

        if recompute:
            cm.recompute(self, resolution)
        return cm.communities(self)

    def communities_pending(self, limit: int = 5) -> Dict[str, Any]:
        """Communities waiting for a label (none yet, or stale), with what the model needs to name them."""
        from ..recall import communities as cm

        return cm.pending(self, limit)

    def label_communities(self, items: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        """Store labels ``[{community_id, label, summary, kind}]`` -> ``{labelled, warnings}``."""
        from ..recall import communities as cm

        return cm.label(self, items)

    def stats(self, *, now: Optional[str] = None) -> Dict[str, Any]:
        """What the notebook has accumulated (``GET /note/stats``; :func:`abstractnote.recall.stats.stats`)."""
        from ..recall.stats import stats

        return stats(self, now=now)

    def recompute_communities(self, resolution: Optional[float] = None) -> Dict[str, Any]:
        """Run community detection over the entity graph and store the result (no model); returns the counts."""
        from ..recall import communities as cm

        return cm.recompute(self, resolution)

    # ------------------------------------------------------------------ settings and status

    def settings(self) -> Dict[str, Any]:
        stored = self.store.meta_get("settings", {}) or {}
        out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULT_SETTINGS.items()}
        for k, v in stored.items():
            if k in out:
                out[k] = v
        return out

    def update_settings(self, changes: Dict[str, Any]) -> Dict[str, Any]:
        unknown = sorted(set(changes) - set(DEFAULT_SETTINGS))
        if unknown:
            raise ValueError(f"Unknown setting: {', '.join(unknown)}.")
        if "processor_schedule" in changes:
            changes = {**changes, "processor_schedule": _check_processor_schedule(changes["processor_schedule"])}
        if "consolidate_schedule" in changes:
            changes = {**changes, "consolidate_schedule": _check_consolidate_schedule(changes["consolidate_schedule"])}
        current = self.settings()
        current.update(changes)
        self.store.meta_set("settings", current)
        return current

    def processor_throughput(self) -> Dict[str, Any]:
        """``{max_items, interval}`` of the processor: what its automation was created with (recorded by
        ``POST /note/processor/ensure`` under ``processor_schedule_applied``) while it exists, else the stored
        ``processor_schedule`` setting it will be created with."""
        applied = self.store.meta_get("processor_schedule_applied") if self.store.meta_get(
            "processor_automation_id") else None
        sched = applied if isinstance(applied, dict) else self.settings()["processor_schedule"]
        return {"max_items": int(sched["max_items"]), "interval": str(sched["every"])}

    def processing(self, *, plane: Any = None, automations: Optional[Dict[str, Any]] = None,
                   now: Optional[str] = None) -> Dict[str, Any]:
        """What the processing is doing (``GET /note/processing``; :func:`abstractnote.recall.processing.processing`).
        Headless (no ``plane``), the active runs are empty and the run-dependent fields null."""
        from ..recall.processing import processing

        return processing(self, plane=plane, automations=automations, now=now)

    def set_priority(self, note_id: str, priority: Any) -> Dict[str, Any]:
        """Move a waiting note in the line (``POST /note/queue/{note_id}/priority``; 3 = front of the line).
        Bookkeeping only: the queue row and the note's priority change (kept by later filing), no revision."""
        from .queue import PRIORITY_BULK, PRIORITY_URGENT

        if isinstance(priority, bool) or not isinstance(priority, int) or not (
                PRIORITY_BULK <= priority <= PRIORITY_URGENT):
            raise ValueError("The priority must be a whole number from 0 to 3.")
        with self.store.tx() as c:
            row = c.execute("SELECT state FROM queue WHERE note_id=?", (note_id,)).fetchone()
            if row is None or row["state"] != "queued":
                raise ValueError("Only a note that is waiting in the queue can be moved.")
            self.queue.set_priority(c, note_id, priority)
            c.execute("UPDATE notes SET priority=?, priority_explicit=1 WHERE note_id=?", (priority, note_id))
        return {"note_id": note_id, "priority": priority}

    def meta(self, key: str, default: Any = None) -> Any:
        return self.store.meta_get(key, default)

    def set_meta(self, key: str, value: Any) -> None:
        if key in ("schema_version", "embedding_pin", "settings"):
            raise ValueError(f"The {key} entry is managed by the notebook.")
        self.store.meta_set(key, value)

    def status(self) -> Dict[str, Any]:
        from ..versions import module_version

        # W28: the version of the AbstractMemory that is imported (a checkout on the path can run 0.3.2 while the
        # installed metadata still says 0.3.1)
        am_version = module_version("abstractmemory")
        pending = len(self.store.pending_revisions())
        return {
            "notebook": str(self.root),
            "counts": self.store.counts(),
            "queue": self.queue.stats(),
            "embedding_pin": self.store.meta_get("embedding_pin"),
            "kg_embedding_pin": self.kg.embedding_pin(),
            "kg_assertions": self.kg.count(),
            "pending_revisions": pending,
            "read_only": self.read_only,
            "settings": self.settings(),
            "automations": {"processor_id": self.store.meta_get("processor_automation_id"),
                            "reflect_id": self.store.meta_get("reflect_automation_id")},
            "versions": {"abstractnote": __version__, "schema": int(self.store.meta_get("schema_version", 0)),
                         "abstractmemory": am_version},
        }

    # ------------------------------------------------------------------ rebuild

    def rebuild(self, *, reembed: bool = False, embedder: Optional[TextEmbedder] = None) -> Dict[str, Any]:
        """Replay notes and revisions into fresh index projections and a fresh triple store.

        Without ``reembed`` stored vectors are reused (no embedder calls for known
        texts). With ``reembed`` every vector is recomputed with ``embedder`` (or the
        current one) and the notebook is pinned to that model.
        """
        with self._apply_lock:
            before = self.store.counts()
            new_embedder = embedder or self.cache.embedder
            if new_embedder is None:
                raise ValueError(self.cache.missing_embedder)
            self.kg.close()
            with self.store.lock:
                self.store.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self.store.close()
            note_path, kg_path = self.root / NOTE_DB, self.root / KG_DB
            old_note = self.root / (NOTE_DB + ".pre-rebuild")
            old_kg = self.root / (KG_DB + ".pre-rebuild")
            for p in (old_note, old_kg):
                _remove_db(p)
            os.replace(note_path, old_note)
            _remove_db_sidecars(note_path)
            if kg_path.exists():
                os.replace(kg_path, old_kg)
            _remove_db_sidecars(kg_path)
            try:
                store = NoteStore(str(note_path))
                self._copy_truth(store, old_note, reembed=reembed)
                cache = self._make_cache(store, new_embedder, force_new_pin=reembed)
                self.store, self.cache = store, cache
                self.queue.store = store
                self.note_vectors.store = store
                self.note_vectors.reset()
                self._reindex_notes(reembed=reembed)
                self.kg = ClaimStore(kg_path, cache)
                replayed = 0
                self.store.marking = False  # ADR 0008: the marks table is truth; a replay marks nothing again
                for row in self.store.q("SELECT * FROM revisions WHERE abandoned=0 ORDER BY seq"):
                    self._finish(self.store.revision_dict(row))
                    replayed += 1
                self.store.marking = True
                self._sync_pin()
            except BaseException:
                try:
                    self.kg.close()
                except Exception:
                    pass
                try:
                    self.store.close()
                except Exception:
                    pass
                _remove_db(note_path)
                _remove_db(kg_path)
                os.replace(old_note, note_path)
                if old_kg.exists():
                    os.replace(old_kg, kg_path)
                self.store = NoteStore(str(note_path))
                self.cache = self._make_cache(self.store, new_embedder if not reembed else None)
                self.queue.store = self.store
                self.note_vectors.store = self.store
                self.note_vectors.reset()
                self.kg = ClaimStore(kg_path, self.cache)
                raise
            _remove_db(old_note)
            _remove_db(old_kg)
            return {"replayed_revisions": replayed, "before": before, "after": self.store.counts(),
                    "reembedded": reembed, "embedding_pin": self.store.meta_get("embedding_pin")}

    def _copy_truth(self, store: NoteStore, old_path: Path, *, reembed: bool) -> None:
        with store.lock:
            c = store.conn
            c.execute("ATTACH DATABASE ? AS old", (str(old_path),))
            try:
                c.execute("BEGIN IMMEDIATE")
                c.execute("DELETE FROM meta")
                c.execute("INSERT INTO meta SELECT * FROM old.meta" + (" WHERE key!='embedding_pin'" if reembed else ""))
                tables = ["notes", "attachments", "note_links", "queue", "queue_errors", "context_packets", "revisions",
                          "reinterpret_marks", "idea_changes", "reading_confirmations", "gate_decisions",
                          "edge_conductance", *KEPT_TABLES]
                if not reembed:
                    tables.append("vectors")
                for table in tables:
                    new_cols = [r[1] for r in c.execute(f"PRAGMA main.table_info({table})")]
                    old_cols = {r[1] for r in c.execute(f"PRAGMA old.table_info({table})")}
                    collist = ",".join(col for col in new_cols if col in old_cols)
                    if not collist:  # a table the old notebook did not have yet
                        continue
                    c.execute(f"INSERT INTO main.{table}({collist}) SELECT {collist} FROM old.{table}")
                c.execute("UPDATE revisions SET applied_index=0, applied_kg=0, result_json=NULL WHERE abandoned=0")
                c.execute("UPDATE notes SET summary=NULL")
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise
            finally:
                c.execute("DETACH DATABASE old")

    def _reindex_notes(self, *, reembed: bool = False) -> None:
        """Note FTS rows, filing topics and (when missing or re-embedding) note vectors."""
        rows = self.store.q("SELECT n.note_id, n.title, n.text, n.filing_topics_json, n.occurred_at, n.vector_key,"
                            " v.vector_key AS have FROM notes n LEFT JOIN vectors v ON v.vector_key=n.vector_key"
                            " ORDER BY n.seq")
        texts = []
        with self.store.tx() as c:
            for r in rows:
                extracted = self.extracted_texts(r["note_id"])
                self.store.refresh_note_fts(c, r["note_id"], extracted)
                for topic in _uj(r["filing_topics_json"], []) or []:
                    self.store.assign_topic(c, r["note_id"], topic, by="filing", revision_id=None, at=r["occurred_at"])
                if reembed or (r["vector_key"] and not r["have"]):
                    text = note_vector_text(r["title"], r["text"], extracted, NOTE_EMBED_BUDGET)
                    if text.strip():
                        texts.append((r["note_id"], text))
        if texts:
            keys = self.cache.ensure([t for _, t in texts])
            with self.store.tx() as c:
                c.executemany("UPDATE notes SET vector_key=? WHERE note_id=?",
                              [(k, nid) for (nid, _), k in zip(texts, keys)])

    def extracted_texts(self, note_id: str) -> List[str]:
        out = []
        for a in self.store.attachments_of(note_id):
            path = self.root / "extracted" / f"{a['sha256']}.txt"
            if a["extracted_chars"] and path.exists():
                out.append(path.read_text(encoding="utf-8"))
        return out


def _remove_db_sidecars(path: Path) -> None:
    for suffix in ("-wal", "-shm"):
        p = Path(str(path) + suffix)
        if p.exists():
            p.unlink()


def _remove_db(path: Path) -> None:
    if path.exists():
        path.unlink()
    _remove_db_sidecars(path)
