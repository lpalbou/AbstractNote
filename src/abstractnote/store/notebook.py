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
:meth:`Notebook.recover` replays revisions whose flags are not both set.
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
from .notes import NoteStore, _j, _uj, entity_text, note_vector_text

NOTE_DB = "note.sqlite3"
KG_DB = "kg.sqlite3"
NOTE_EMBED_BUDGET = 8000  # characters of a note handed to the embedder (the stored note is never cut)

DEFAULT_SETTINGS: Dict[str, Any] = {
    "filing_model": {"provider": None, "model": None},
    "answering_model": {"provider": None, "model": None},
    "reflect_schedule": {"every": "7d"},
    "process_inline_default": False,
}


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
        # Injected processing helpers (bound by abstractnote.tools.bind or by callers)
        self.extractors: Dict[str, Callable[..., Any]] = {}

    # ------------------------------------------------------------------ open / close

    @classmethod
    def open(cls, root: "str | os.PathLike[str]", *, embedder: Optional[TextEmbedder] = None,
             recover: bool = True) -> "Notebook":
        """Open (and create when missing) a notebook directory.

        ``embedder`` selects the embedding space. A new notebook without one uses
        the deterministic :class:`HashingEmbedder`. A notebook pinned to another
        model must be opened with that model (or rebuilt with ``reembed=True``).
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
        if recover:
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
                    raise ValueError(
                        f"This notebook's vectors were made with the embedding model {pin.get('model_id')!r}; "
                        "open it with that embedder, or rebuild it with reembed to change models."
                    )
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

    # ------------------------------------------------------------------ revisions

    def apply_revision(self, revision: "Revision | Dict[str, Any]", *, _fail_after_index: bool = False
                       ) -> Dict[str, Any]:
        """Apply a revision to both stores. Re-applying the same ``revision_id`` is a no-op
        that returns the stored result. Raises on invalid ops (nothing is applied)."""
        rev = self._rev_dict(revision)
        with self._apply_lock:
            existing = self.store.revision_row(rev["revision_id"])
            if existing is not None:
                if existing["abandoned"]:
                    raise ValueError(f"The revision {rev['revision_id']} failed before and was set aside.")
                if existing["applied_index"] and existing["applied_kg"]:
                    return self._result(existing, already_applied=True)
                return self._finish(self.store.revision_dict(existing), _fail_after_index=_fail_after_index)
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
            o = op.model_dump() if isinstance(op, RevisionOp) else RevisionOp(**op).model_dump()
            ops.append(o)
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
        row = self.store.revision_row(rev["revision_id"])
        if not row["applied_kg"]:
            with self.store.lock:
                kinds = kinds_from_index(self.store.conn, rev)
            self.kg.project(rev, kinds)
            self.store.set_applied_kg(rev["revision_id"])
        return self._result(self.store.revision_row(rev["revision_id"]))

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
            "warnings": list(_uj(row["warnings_json"], []) or []),
        }
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

    def retract_claim(self, claim_id: str, account: Optional[str], reason: str, *, client: str = "web",
                      occurred_at: Optional[str] = None) -> Dict[str, Any]:
        row = self.store.claim_row(claim_id)
        if row is None:
            raise ValueError("That statement is not in this notebook.")
        if row["status"] == "retracted":
            raise ValueError("That statement was already retracted.")
        if not str(reason or "").strip():
            raise ValueError("Say why the statement is wrong.")
        return self._user_revision("claim.retract", claim_id, [{"op": "claim.retract", "payload": {
            "claim_id": claim_id, "reason": reason}}], account, f"Retracted a statement: {reason}",
            client=client, occurred_at=occurred_at)

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

    # ------------------------------------------------------------------ reads (store-level)

    def beliefs_at(self, at: str, *, entity_id: Optional[str] = None, subject_id: Optional[str] = None
                   ) -> List[Dict[str, Any]]:
        return self.store.beliefs_at(at, entity_id=entity_id, subject_id=subject_id)

    def graph(self, *, focus: Optional[Sequence[str]] = None, at: Optional[str] = None, depth: int = 1,
              since: Optional[str] = None, until: Optional[str] = None, kinds: Optional[Sequence[str]] = None,
              min_count: int = 1, limit: int = 200, as_of: Optional[str] = None) -> Dict[str, Any]:
        return self.store.graph(focus=focus, at=at or as_of, depth=depth, since=since, until=until, kinds=kinds,
                                min_count=min_count, limit=limit)

    def day(self, day: str, tz: Optional[str] = None) -> Dict[str, Any]:
        return self.store.day(day, tz)

    def timeline(self, since: Optional[str] = None, until: Optional[str] = None, granularity: str = "day"
                 ) -> Dict[str, Any]:
        return self.store.timeline(since=since, until=until, granularity=granularity)

    def notes(self, **kwargs: Any) -> Dict[str, Any]:
        return self.store.list_notes(**kwargs)

    def entities(self, **kwargs: Any) -> Dict[str, Any]:
        return self.store.list_entities(**kwargs)

    def revisions(self, **kwargs: Any) -> Dict[str, Any]:
        return self.store.revisions_feed(**kwargs)

    def topics(self) -> List[Dict[str, Any]]:
        return self.store.topics()

    def tasks(self, status: Optional[str] = "open", entity: Optional[str] = None, limit: int = 100
              ) -> List[Dict[str, Any]]:
        return self.store.tasks(status=status, entity=entity, limit=limit)

    def insights(self, kind: Optional[str] = None, acked: Optional[bool] = None, limit: int = 50
                 ) -> List[Dict[str, Any]]:
        return self.store.insights(kind=kind, acked=acked, limit=limit)

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
        current = self.settings()
        current.update(changes)
        self.store.meta_set("settings", current)
        return current

    def meta(self, key: str, default: Any = None) -> Any:
        return self.store.meta_get(key, default)

    def set_meta(self, key: str, value: Any) -> None:
        if key in ("schema_version", "embedding_pin", "settings"):
            raise ValueError(f"The {key} entry is managed by the notebook.")
        self.store.meta_set(key, value)

    def status(self) -> Dict[str, Any]:
        from importlib.metadata import PackageNotFoundError, version

        try:
            am_version: Optional[str] = version("abstractmemory")
        except PackageNotFoundError:  # pragma: no cover
            am_version = None
        pending = len(self.store.pending_revisions())
        return {
            "notebook": str(self.root),
            "counts": self.store.counts(),
            "queue": self.queue.stats(),
            "embedding_pin": self.store.meta_get("embedding_pin"),
            "kg_embedding_pin": self.kg.embedding_pin(),
            "kg_assertions": self.kg.count(),
            "pending_revisions": pending,
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
                raise ValueError("Rebuilding needs an embedder.")
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
                self._reindex_notes()
                self.kg = ClaimStore(kg_path, cache)
                replayed = 0
                for row in self.store.q("SELECT * FROM revisions WHERE abandoned=0 ORDER BY seq"):
                    self._finish(self.store.revision_dict(row))
                    replayed += 1
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
                for table in ("notes", "attachments", "note_links", "queue", "context_packets"):
                    c.execute(f"INSERT INTO {table} SELECT * FROM old.{table}")
                if not reembed:
                    c.execute("INSERT INTO vectors SELECT * FROM old.vectors")
                cols = [r[1] for r in c.execute("PRAGMA table_info(revisions)")]
                collist = ",".join(cols)
                c.execute(f"INSERT INTO revisions({collist}) SELECT {collist} FROM old.revisions")
                c.execute("UPDATE revisions SET applied_index=0, applied_kg=0, result_json=NULL WHERE abandoned=0")
                c.execute("UPDATE notes SET summary=NULL")
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise
            finally:
                c.execute("DETACH DATABASE old")

    def _reindex_notes(self) -> None:
        """Note FTS rows, filing topics and note vectors from the truth tables."""
        rows = self.store.q("SELECT note_id, title, text, filing_topics_json, occurred_at FROM notes ORDER BY seq")
        texts = []
        with self.store.tx() as c:
            for r in rows:
                extracted = self.extracted_texts(r["note_id"])
                self.store.refresh_note_fts(c, r["note_id"], extracted)
                for topic in _uj(r["filing_topics_json"], []) or []:
                    self.store.assign_topic(c, r["note_id"], topic, by="filing", revision_id=None, at=r["occurred_at"])
                text = note_vector_text(r["title"], r["text"], extracted, NOTE_EMBED_BUDGET)
                if text:
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
