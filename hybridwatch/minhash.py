"""MinHash + LSH for near-duplicate detection (copy-paste campaigns).

Finds posts whose wording is almost identical (small edits, extra emoji,
different hashtag) in roughly constant time per post, instead of comparing
every post with every other post.
"""
from __future__ import annotations

import zlib
from collections import defaultdict

import numpy as np

_MERSENNE = np.uint64((1 << 61) - 1)
_MAX32 = np.uint64(0xFFFFFFFF)


class MinHasher:
    def __init__(self, num_perm: int = 64, shingle: int = 3, seed: int = 7):
        rng = np.random.default_rng(seed)
        self.num_perm = num_perm
        self.shingle = shingle
        self.a = rng.integers(1, (1 << 31) - 1, size=num_perm, dtype=np.uint64)
        self.b = rng.integers(0, (1 << 31) - 1, size=num_perm, dtype=np.uint64)

    def _shingles(self, normalized: str) -> np.ndarray:
        toks = normalized.split()
        if len(toks) < self.shingle:
            grams = [" ".join(toks)] if toks else [""]
        else:
            grams = [" ".join(toks[i:i + self.shingle]) for i in range(len(toks) - self.shingle + 1)]
        # crc32 is stable across runs (Python's hash() is not)
        return np.array([zlib.crc32(g.encode()) for g in set(grams)], dtype=np.uint64)

    def signature(self, normalized: str) -> np.ndarray:
        sh = self._shingles(normalized)
        h = (np.outer(sh, self.a) + self.b) % _MERSENNE & _MAX32
        return h.min(axis=0).astype(np.uint32)

    @staticmethod
    def similarity(s1: np.ndarray, s2: np.ndarray) -> float:
        return float(np.mean(s1 == s2))


class LSHIndex:
    """Banded LSH over MinHash signatures, with a time window so memory stays flat."""

    def __init__(self, num_perm: int = 64, bands: int = 16):
        assert num_perm % bands == 0
        self.bands = bands
        self.rows = num_perm // bands
        self.buckets: list[dict[bytes, list[int]]] = [defaultdict(list) for _ in range(bands)]
        self.sigs: dict[int, np.ndarray] = {}
        self.times: dict[int, float] = {}

    def query(self, sig: np.ndarray) -> set[int]:
        out: set[int] = set()
        for b in range(self.bands):
            key = sig[b * self.rows:(b + 1) * self.rows].tobytes()
            out.update(self.buckets[b].get(key, ()))
        return out

    def add(self, item_id: int, sig: np.ndarray, t: float) -> None:
        self.sigs[item_id] = sig
        self.times[item_id] = t
        for b in range(self.bands):
            key = sig[b * self.rows:(b + 1) * self.rows].tobytes()
            self.buckets[b][key].append(item_id)

    def prune(self, older_than: float) -> None:
        old = {i for i, t in self.times.items() if t < older_than}
        if not old:
            return
        for i in old:
            self.sigs.pop(i, None)
            self.times.pop(i, None)
        for b in range(self.bands):
            bucket = self.buckets[b]
            for key in list(bucket.keys()):
                kept = [i for i in bucket[key] if i not in old]
                if kept:
                    bucket[key] = kept
                else:
                    del bucket[key]
