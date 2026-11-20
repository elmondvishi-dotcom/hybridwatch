"""Incremental (online) narrative clustering.

Each narrative is a centroid. A new post joins the most similar narrative if the
cosine similarity is above a threshold, otherwise it starts a new narrative.
No re-clustering of old data is needed, so cost per post stays constant -
which is what makes real-time processing possible.
"""
from __future__ import annotations

from typing import Callable

import numpy as np


class OnlineClusterer:
    def __init__(self, dim: int, threshold: float):
        self.dim = dim
        self.threshold = threshold
        self.ids: list[int] = []
        self.sums = np.zeros((0, dim), dtype=np.float32)   # un-normalised sum of member vectors
        self.counts = np.zeros(0, dtype=np.int64)
        self._centroids: np.ndarray | None = None

    # ---- state ----
    def load(self, items: list[tuple[int, np.ndarray, int]]) -> None:
        """items: (narrative_id, centroid_unit_vector, size)"""
        if not items:
            return
        self.ids = [i for i, _, _ in items]
        self.sums = np.vstack([c * n for _, c, n in items]).astype(np.float32)
        self.counts = np.array([n for _, _, n in items], dtype=np.int64)
        self._centroids = None

    def centroids(self) -> np.ndarray:
        if self._centroids is None:
            n = np.linalg.norm(self.sums, axis=1, keepdims=True)
            n[n == 0] = 1.0
            self._centroids = self.sums / n
        return self._centroids

    def centroid(self, nid: int) -> np.ndarray:
        return self.centroids()[self.ids.index(nid)]

    # ---- assignment ----
    def assign(self, vecs: np.ndarray, new_id: Callable[[], int]) -> list[tuple[int, float]]:
        """Returns (narrative_id, similarity) for each vector, creating narratives as needed."""
        out: list[tuple[int, float] | None] = [None] * len(vecs)
        pending = list(range(len(vecs)))

        if self.ids:
            sims = vecs @ self.centroids().T
            best = sims.argmax(axis=1)
            best_sim = sims[np.arange(len(vecs)), best]
            pending = []
            for i in range(len(vecs)):
                if best_sim[i] >= self.threshold:
                    k = int(best[i])
                    self.sums[k] += vecs[i]
                    self.counts[k] += 1
                    out[i] = (self.ids[k], float(best_sim[i]))
                else:
                    pending.append(i)
            self._centroids = None

        # Posts that matched nothing: assign sequentially so posts in the same batch
        # can found and join a new narrative together.
        for i in pending:
            v = vecs[i]
            if self.ids:
                sims = self.centroids() @ v
                k = int(sims.argmax())
                if sims[k] >= self.threshold:
                    self.sums[k] += v
                    self.counts[k] += 1
                    self._centroids = None
                    out[i] = (self.ids[k], float(sims[k]))
                    continue
            nid = new_id()
            self.ids.append(nid)
            self.sums = np.vstack([self.sums, v[None, :]])
            self.counts = np.append(self.counts, 1)
            self._centroids = None
            out[i] = (nid, 1.0)
        return out  # type: ignore[return-value]

    # ---- merging ----
    def merge_candidates(self, threshold: float, min_size: int = 3) -> list[tuple[int, int, float]]:
        """Pairs (keep_id, drop_id, sim) of narratives whose centroids drifted together."""
        idx = [k for k in range(len(self.ids)) if self.counts[k] >= min_size]
        if len(idx) < 2:
            return []
        c = self.centroids()[idx]
        sims = c @ c.T
        np.fill_diagonal(sims, 0)
        pairs = []
        used: set[int] = set()
        for a, b in zip(*np.where(np.triu(sims) >= threshold)):
            ka, kb = idx[a], idx[b]
            if ka in used or kb in used:
                continue
            keep, drop = (ka, kb) if self.counts[ka] >= self.counts[kb] else (kb, ka)
            pairs.append((self.ids[keep], self.ids[drop], float(sims[a, b])))
            used.update((ka, kb))
        return pairs

    def merge(self, keep_id: int, drop_id: int) -> None:
        k, d = self.ids.index(keep_id), self.ids.index(drop_id)
        self.sums[k] += self.sums[d]
        self.counts[k] += self.counts[d]
        self.sums = np.delete(self.sums, d, axis=0)
        self.counts = np.delete(self.counts, d)
        self.ids.pop(d)
        self._centroids = None

    def size(self, nid: int) -> int:
        return int(self.counts[self.ids.index(nid)])
