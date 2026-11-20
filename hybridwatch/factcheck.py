"""Debunked-claims library and matching.

The tool never decides what is true. It recognises when a post repeats a claim
that a named fact-checker (or an analyst, after confirming a case) has already
rated false, and links to that source.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time

import numpy as np

from .config import resolve
from .text import normalize

# Words that signal a post is REFUTING the claim, not spreading it.
REFUTATION_MARKERS = [
    "fake news", "hoax", "debunk", "is false", "not true", "misinformation", "disinformation",
    "lajm i rreme", "lajme te rreme", "e rreme", "nuk eshte e vertete", "dezinformat", "genjeshtr",
    "lazna vest", "lazne vesti", "lazna", "nije istina", "netacno", "dezinformacij", "neistin",
]


class FactCheckLibrary:
    def __init__(self, conn: sqlite3.Connection, embedder, llm, cfg: dict):
        self.conn = conn
        self.embedder = embedder
        self.llm = llm
        self.cfg = cfg
        self.ids: list[int] = []
        self.claims: list[str] = []
        self.matrix = np.zeros((0, embedder.dim), dtype=np.float32)
        self.reload()

    def reload(self) -> None:
        rows = self.conn.execute("SELECT id, claim, embedding FROM factchecks").fetchall()
        self.ids = [r["id"] for r in rows]
        self.claims = [r["claim"] for r in rows]
        if rows:
            self.matrix = np.vstack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
        else:
            self.matrix = np.zeros((0, self.embedder.dim), dtype=np.float32)

    # ---- loading ----
    def add(self, items: list[dict], kind: str = "external", embeddings: np.ndarray | None = None) -> int:
        items = [i for i in items if i.get("claim")]
        if not items:
            return 0
        vecs = embeddings if embeddings is not None else self.embedder.embed([i["claim"] for i in items])
        n = 0
        for it, v in zip(items, vecs):
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO factchecks(ext_id, source, claim, verdict, url, date, kind, embedding) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (it.get("id") or it.get("url") or it["claim"][:80], it.get("source", ""), it["claim"],
                 it.get("verdict", ""), it.get("url", ""), it.get("date", ""), kind,
                 np.asarray(v, dtype=np.float32).tobytes()))
            n += cur.rowcount
        self.conn.commit()
        self.reload()
        return n

    def refresh_sources(self, factcheckers: list[dict]) -> None:
        for fc in factcheckers:
            if not fc.get("enabled"):
                continue
            try:
                items = self._fetch(fc)
                added = self.add(items)
                if added:
                    print(f"[factcheck] {fc['name']}: +{added} claims")
            except Exception as e:
                print(f"[factcheck] {fc['name']} failed: {e}")

    def _fetch(self, fc: dict) -> list[dict]:
        kind, url = fc.get("type"), fc.get("url", "")
        if kind == "file":
            with open(resolve(self.cfg, url), encoding="utf-8") as f:
                return json.load(f)
        if kind == "rss" and url:
            import feedparser
            feed = feedparser.parse(url)
            return [{"id": e.get("link"), "source": fc["name"], "claim": e.get("title", ""),
                     "verdict": "see article", "url": e.get("link", ""), "date": e.get("published", "")}
                    for e in feed.entries]
        if kind == "google_factcheck":
            import httpx
            key = os.environ.get("GOOGLE_FACTCHECK_KEY")
            if not key:
                return []
            r = httpx.get("https://factchecktools.googleapis.com/v1alpha1/claims:search",
                          params={"query": url or "Kosovo", "key": key, "pageSize": 100}, timeout=30)
            out = []
            for c in r.json().get("claims", []):
                rev = (c.get("claimReview") or [{}])[0]
                out.append({"id": rev.get("url"), "source": rev.get("publisher", {}).get("name", "Google Fact Check"),
                            "claim": c.get("text", ""), "verdict": rev.get("textualRating", ""),
                            "url": rev.get("url", ""), "date": rev.get("reviewDate", "")})
            return out
        return []

    # ---- matching ----
    def match(self, vecs: np.ndarray, texts: list[str], threshold: float,
              llm_budget: int = 10) -> list[tuple[int | None, float]]:
        if not len(self.ids) or not len(vecs):
            return [(None, 0.0)] * len(vecs)
        sims = vecs @ self.matrix.T
        best = sims.argmax(axis=1)
        out: list[tuple[int | None, float]] = []
        for i, k in enumerate(best):
            s = float(sims[i, k])
            if s < threshold:
                out.append((None, s))
                continue
            # Similar wording is not enough: a post may be quoting the claim to refute it.
            if is_refutation(texts[i]):
                out.append((None, s))
                continue
            if self.llm.enabled and llm_budget > 0 and s < threshold + 0.15:
                llm_budget -= 1
                verdict = self.llm.repeats_claim(texts[i], self.claims[k])
                if verdict is False:
                    out.append((None, s))
                    continue
            out.append((self.ids[k], s))
        return out


def is_refutation(text: str) -> bool:
    t = normalize(text)
    return any(m in t for m in REFUTATION_MARKERS)


def now() -> float:
    return time.time()
