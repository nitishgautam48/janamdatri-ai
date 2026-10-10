"""FAISS inner-product index over normalised embeddings (cosine similarity).

Falls back to an exact NumPy search when faiss is not installed.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

log = logging.getLogger(__name__)

try:  # pragma: no cover - import guard
    import faiss  # type: ignore

    HAS_FAISS = True
except Exception:  # pragma: no cover
    faiss = None
    HAS_FAISS = False


@dataclass
class Hit:
    score: float
    doc: dict = field(default_factory=dict)


class VectorStore:
    def __init__(self, embedder) -> None:
        self.embedder = embedder
        self.docs: list[dict] = []
        self._matrix: np.ndarray | None = None
        self._index = None

    @property
    def backend(self) -> str:
        return "faiss" if self._index is not None else "numpy"

    def build(self, docs: list[dict], text_key: str = "text") -> None:
        self.docs = docs
        if hasattr(self.embedder, "fit"):
            self.embedder.fit([d[text_key] for d in docs])
        vectors = self.embedder.embed([d[text_key] for d in docs]) if docs else np.zeros((0, self.embedder.dim), "float32")
        self._matrix = vectors
        if HAS_FAISS and len(docs):
            index = faiss.IndexFlatIP(vectors.shape[1])
            index.add(vectors)
            self._index = index

    def search(self, query: str, k: int = 4, where: dict | None = None) -> list[Hit]:
        if not self.docs:
            return []
        q = getattr(self.embedder, "embed_query", self.embedder.embed)([query])
        # Over-fetch when filtering so the filtered set still has k items.
        fetch = len(self.docs) if where else min(k, len(self.docs))
        if self._index is not None:
            scores, idx = self._index.search(q, fetch)
            pairs = list(zip(scores[0].tolist(), idx[0].tolist()))
        else:
            sims = (self._matrix @ q[0]).tolist()
            order = sorted(range(len(sims)), key=lambda i: -sims[i])[:fetch]
            pairs = [(sims[i], i) for i in order]
        hits: list[Hit] = []
        for score, i in pairs:
            if i < 0:
                continue
            doc = self.docs[i]
            if where and any(doc.get(key) not in (val if isinstance(val, (list, tuple, set)) else [val])
                             for key, val in where.items()):
                continue
            hits.append(Hit(score=float(score), doc=doc))
            if len(hits) >= k:
                break
        return hits
