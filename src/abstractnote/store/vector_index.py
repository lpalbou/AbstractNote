"""In-memory cosine index over note vectors, refreshed incrementally from the note index.

Captures, quick search and the context packet all need "nearest notes"; reading
every vector from SQLite for each call made capture time grow with the
notebook. The index loads rows above the last ``notes.seq`` it saw (note
vectors never change except through a rebuild, which resets the index), so
other processes writing to the same notebook are picked up on the next call.
"""

from __future__ import annotations

import threading
from typing import Any, Iterable, List, Optional, Sequence, Set, Tuple

from .embeddings import _np, cosine, unpack


class NoteVectorIndex:
    def __init__(self, store: Any) -> None:
        self.store = store
        self._lock = threading.RLock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._last_seq = 0
            self._ids: List[str] = []
            self._insight: List[bool] = []
            self._rows: List[bytes] = []
            self._mat = None
            self._dirty = False

    def refresh(self) -> None:
        rows = self.store.q(
            "SELECT n.seq, n.note_id, n.insight_kind, v.vector FROM notes n JOIN vectors v ON v.vector_key=n.vector_key"
            " WHERE n.seq>? ORDER BY n.seq", (self._last_seq,))
        if not rows:
            return
        with self._lock:
            for r in rows:
                self._ids.append(r["note_id"])
                self._insight.append(r["insight_kind"] is not None)
                self._rows.append(r["vector"])
                self._last_seq = max(self._last_seq, int(r["seq"]))
            self._dirty = True

    def nearest(self, query: Sequence[float], k: int, min_score: Optional[float] = None, *,
                allowed: Optional[Set[str]] = None, exclude: Iterable[str] = (), notes_only: bool = False
                ) -> List[Tuple[str, float]]:
        self.refresh()
        excluded = set(exclude)
        with self._lock:
            if not self._ids:
                return []
            if _np is not None:
                if self._dirty or self._mat is None:
                    have = 0 if self._mat is None else self._mat.shape[0]
                    new = _np.frombuffer(b"".join(self._rows[have:]), dtype="<f4").reshape(len(self._rows) - have, -1)
                    norms = _np.linalg.norm(new, axis=1)
                    norms[norms == 0] = 1.0
                    if self._mat is None:
                        self._mat, self._norms = new, norms
                    else:
                        self._mat = _np.vstack([self._mat, new])
                        self._norms = _np.concatenate([self._norms, norms])
                    self._dirty = False
                q = _np.asarray(query, dtype=_np.float32)
                if q.shape[0] != self._mat.shape[1]:
                    raise ValueError("A stored vector has a different dimension from the query; rebuild with --reembed.")
                qn = float(_np.linalg.norm(q)) or 1.0
                scores = (self._mat @ q) / (self._norms * qn)
                order = _np.argsort(-scores)
                pairs = ((int(i), float(scores[i])) for i in order)
            else:  # pragma: no cover - numpy ships with the framework
                scored = [(i, cosine(query, unpack(b))) for i, b in enumerate(self._rows)]
                scored.sort(key=lambda t: -t[1])
                pairs = iter(scored)
            out: List[Tuple[str, float]] = []
            for i, s in pairs:
                if min_score is not None and s < min_score:
                    break
                nid = self._ids[i]
                if nid in excluded or (notes_only and self._insight[i]) or (allowed is not None and nid not in allowed):
                    continue
                out.append((nid, s))
                if len(out) >= k:
                    break
            return out
