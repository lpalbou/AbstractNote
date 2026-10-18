"""Embedders and the notebook's embedding cache.

One notebook = one embedding space. The pin ``{model_id, dimension}`` is kept
in the note index ``meta`` table and given to the AbstractMemory store at
creation. Every vector is computed once through :class:`VectorCache` (keyed by
the sha256 of the embedded text and the model id), stored in the note index,
and handed to the triple store, so a rebuild never re-embeds unless asked.

Adapters:

- :class:`HashingEmbedder` — deterministic feature hashing, offline; the
  default for headless notebooks and the test suite.
- :func:`openai_compat_embedder` — any OpenAI-compatible ``/v1/embeddings``.
- :func:`gateway_embedder` — the gateway embedding route via
  ``abstractmemory.AbstractGatewayTextEmbedder``.
"""

from __future__ import annotations

import hashlib
import math
import re
import sqlite3
import struct
import threading
from typing import Any, Dict, Iterable, List, Optional, Protocol, Sequence, Tuple

try:  # numpy is optional: faster cosine scans when present
    import numpy as _np
except Exception:  # pragma: no cover
    _np = None

HASHING_MODEL_PREFIX = "abstractnote-hashing-v1"


class TextEmbedder(Protocol):
    def embed_texts(self, texts: Sequence[str]) -> List[List[float]]: ...


_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


class HashingEmbedder:
    """Deterministic, offline embedder: signed feature hashing of words and
    character trigrams, L2-normalised. Good enough for exact and near-exact
    similarity (tests, headless use); not a semantic model."""

    def __init__(self, dimension: int = 256) -> None:
        if dimension < 8:
            raise ValueError("The hashing embedder needs at least 8 dimensions.")
        self.dimension = int(dimension)
        self.model_id = f"{HASHING_MODEL_PREFIX}-{self.dimension}"

    def _features(self, text: str) -> Iterable[str]:
        words = _TOKEN_RE.findall(str(text or "").casefold())
        for w in words:
            yield "w:" + w
            padded = f"#{w}#"
            for i in range(len(padded) - 2):
                yield "c:" + padded[i : i + 3]

    def embed_texts(self, texts: Sequence[str]) -> List[List[float]]:
        out: List[List[float]] = []
        for text in texts:
            vec = [0.0] * self.dimension
            for feat in self._features(text):
                h = hashlib.blake2b(feat.encode("utf-8"), digest_size=8).digest()
                idx = int.from_bytes(h[:4], "little") % self.dimension
                sign = 1.0 if h[4] & 1 else -1.0
                vec[idx] += sign
            norm = math.sqrt(sum(v * v for v in vec))
            out.append([v / norm for v in vec] if norm > 0 else vec)
        return out


def openai_compat_embedder(base_url: str, model: str, **kwargs: Any) -> TextEmbedder:
    from abstractmemory import OpenAICompatTextEmbedder

    return OpenAICompatTextEmbedder(base_url, model, **kwargs)


class _PinnedGatewayEmbedder:
    """Gateway embedder with an explicit model id (the gateway route does not report one)."""

    def __init__(self, inner: Any, model_id: Optional[str]) -> None:
        self._inner = inner
        if model_id:
            self.model_id = model_id

    def embed_texts(self, texts: Sequence[str]) -> List[List[float]]:
        return self._inner.embed_texts(texts)


def gateway_embedder(base_url: str, auth_token: Optional[str] = None, *, model_id: Optional[str] = None,
                     **kwargs: Any) -> TextEmbedder:
    from abstractmemory import AbstractGatewayTextEmbedder

    return _PinnedGatewayEmbedder(AbstractGatewayTextEmbedder(base_url=base_url, auth_token=auth_token, **kwargs),
                                  model_id)


def embedder_model_id(embedder: Any) -> Optional[str]:
    for attr in ("model_id", "model"):
        v = getattr(embedder, attr, None)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def embedder_for_pin(pin: Optional[Dict[str, Any]]) -> Optional[TextEmbedder]:
    """The embedder a pin can be reopened with without configuration (hashing only)."""
    if not pin:
        return None
    mid = str(pin.get("model_id") or "")
    if mid.startswith(HASHING_MODEL_PREFIX + "-"):
        try:
            return HashingEmbedder(int(mid.rsplit("-", 1)[1]))
        except ValueError:
            return None
    return None


# ----------------------------------------------------------------------------- vectors

def pack(vector: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *[float(x) for x in vector])


def unpack(blob: bytes) -> List[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na > 0 and nb > 0 else 0.0


def top_k(query: Sequence[float], items: Sequence[Tuple[str, bytes]], k: int,
          min_score: Optional[float] = None) -> List[Tuple[str, float]]:
    """Cosine top-k of (key, packed-vector) items, best first."""
    if not items or k <= 0:
        return []
    if _np is not None:
        q = _np.asarray(query, dtype=_np.float32)
        qn = float(_np.linalg.norm(q))
        if qn == 0:
            return []
        keys = [key for key, _ in items]
        mat = _np.frombuffer(b"".join(blob for _, blob in items), dtype="<f4").reshape(len(items), -1)
        if mat.shape[1] != q.shape[0]:
            raise ValueError("A stored vector has a different dimension from the query; rebuild with --reembed.")
        norms = _np.linalg.norm(mat, axis=1)
        norms[norms == 0] = 1.0
        scores = (mat @ q) / (norms * qn)
        order = _np.argsort(-scores)
        out: List[Tuple[str, float]] = []
        for idx in order[: max(k, 0) if min_score is None else len(order)]:
            s = float(scores[idx])
            if min_score is not None and s < min_score:
                break
            out.append((keys[idx], s))
            if len(out) >= k:
                break
        return out
    scored = [(key, cosine(query, unpack(blob))) for key, blob in items]
    scored.sort(key=lambda t: -t[1])
    if min_score is not None:
        scored = [t for t in scored if t[1] >= min_score]
    return scored[:k]


def vector_key(text: str, model_id: str) -> str:
    return hashlib.sha256(f"{model_id}\x1f{text}".encode("utf-8")).hexdigest()


class VectorCache:
    """Embeds through the notebook's embedder once per distinct (model, text).

    Vectors live in the note index ``vectors`` table; only misses reach the embedder."""

    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock, embedder: Optional[TextEmbedder],
                 model_id: Optional[str], dimension: Optional[int]) -> None:
        self._conn = conn
        self._lock = lock
        self.embedder = embedder
        self.model_id = model_id or (embedder_model_id(embedder) if embedder is not None else None) or "unknown"
        self.dimension = dimension

    def keys_for(self, texts: Sequence[str]) -> List[str]:
        return [vector_key(t, self.model_id) for t in texts]

    def ensure(self, texts: Sequence[str]) -> List[str]:
        """Make sure every text has a stored vector; return their keys."""
        keys = self.keys_for(texts)
        with self._lock:
            have = set()
            for i in range(0, len(keys), 500):
                chunk = keys[i : i + 500]
                q = ",".join("?" for _ in chunk)
                have.update(r[0] for r in self._conn.execute(
                    f"SELECT vector_key FROM vectors WHERE vector_key IN ({q})", chunk))
        missing: Dict[str, str] = {}
        for key, text in zip(keys, texts):
            if key not in have and key not in missing:
                missing[key] = text
        if missing:
            if self.embedder is None:
                raise RuntimeError("This notebook has no embedder configured, so new text cannot be embedded.")
            texts_missing = list(missing.values())
            vectors = self.embedder.embed_texts(texts_missing)
            if len(vectors) != len(texts_missing):
                raise RuntimeError("The embedder returned a different number of vectors than texts.")
            dims = {len(v) for v in vectors}
            if len(dims) != 1:
                raise RuntimeError("The embedder returned vectors of different dimensions.")
            dim = dims.pop()
            if self.dimension is not None and dim != self.dimension:
                raise RuntimeError(
                    f"The embedder returned {dim}-dimension vectors but this notebook is pinned to "
                    f"{self.dimension}; rebuild with --reembed after changing the embedding model."
                )
            self.dimension = dim
            with self._lock:
                self._conn.executemany(
                    "INSERT OR IGNORE INTO vectors(vector_key, model_id, dimension, vector) VALUES (?,?,?,?)",
                    [(k, self.model_id, dim, pack(v)) for k, v in zip(missing.keys(), vectors)],
                )
        return keys

    def get(self, key: Optional[str]) -> Optional[List[float]]:
        if not key:
            return None
        with self._lock:
            row = self._conn.execute("SELECT vector FROM vectors WHERE vector_key=?", (key,)).fetchone()
        return unpack(row[0]) if row else None

    def embed_query(self, text: str) -> Optional[List[float]]:
        """Vector for a query text: a stored vector when the text was embedded
        before, else one embedder call (queries are not stored). None when the
        notebook has no embedder."""
        cached = self.get(vector_key(text, self.model_id))
        if cached is not None:
            return cached
        if self.embedder is None:
            return None
        [vec] = self.embedder.embed_texts([text])
        vec = [float(x) for x in vec]
        if self.dimension is not None and len(vec) != self.dimension:
            raise RuntimeError(
                f"The embedder returned a {len(vec)}-dimension vector but this notebook is pinned to "
                f"{self.dimension}; rebuild with --reembed after changing the embedding model."
            )
        return vec
