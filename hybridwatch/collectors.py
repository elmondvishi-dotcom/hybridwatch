"""Collectors turn sources into post dicts.

Post dict fields: ext_id, source, source_type, credibility, author, author_created (epoch),
followers, ts (epoch), text, url, optional translation / lang / gt_* (ground truth, demo only).

* ReplayCollector - replays a recorded dataset on a simulated clock (demo + backtesting).
  speed=0 loads everything immediately (backlog mode).
* RSSCollector   - news sites and fact-checkers that publish RSS/Atom.
Telegram / X / Facebook collectors plug in the same way (poll() -> list of post dicts);
they need platform API credentials, so they are left as an extension point.
"""
from __future__ import annotations

import json
import time
from calendar import timegm

from .config import resolve


class ReplayCollector:
    def __init__(self, cfg: dict, source: dict, state: dict):
        self.source = source
        path = resolve(cfg, source.get("url") or cfg["replay"]["file"])
        with open(path, encoding="utf-8") as f:
            self.posts = sorted((json.loads(line) for line in f if line.strip()), key=lambda p: p["ts"])
        self.speed = float(cfg["replay"].get("speed", 400))
        self.batch = int(cfg["worker"].get("batch_size", 500))
        # state survives restarts (stored in DB settings by the worker)
        self.state = state
        self.state.setdefault("offset", 0)
        self.state.setdefault("wall_start", time.time())
        self.state.setdefault("sim_start", self.posts[0]["ts"] if self.posts else 0)

    @property
    def done(self) -> bool:
        return self.state["offset"] >= len(self.posts)

    def sim_clock(self) -> float:
        if self.speed <= 0:
            return float("inf")
        return self.state["sim_start"] + (time.time() - self.state["wall_start"]) * self.speed

    def poll(self) -> list[dict]:
        clock = self.sim_clock()
        out = []
        i = self.state["offset"]
        while i < len(self.posts) and self.posts[i]["ts"] <= clock and len(out) < self.batch:
            p = dict(self.posts[i])
            p.setdefault("source_type", self.source.get("type", "replay"))
            out.append(p)
            i += 1
        self.state["offset"] = i
        return out


class RSSCollector:
    def __init__(self, cfg: dict, source: dict, state: dict):
        self.source = source
        self.state = state
        self.interval = 120

    @property
    def done(self) -> bool:
        return False

    def poll(self) -> list[dict]:
        if time.time() - self.state.get("last", 0) < self.interval or not self.source.get("url"):
            return []
        self.state["last"] = time.time()
        import feedparser
        feed = feedparser.parse(self.source["url"])
        out = []
        for e in feed.entries:
            ts = timegm(e.published_parsed) if e.get("published_parsed") else time.time()
            text = (e.get("title", "") + ". " + e.get("summary", "")).strip()
            out.append({"ext_id": e.get("link") or e.get("id") or text[:100], "source": self.source["name"],
                        "source_type": "news", "credibility": self.source.get("credibility", "unknown"),
                        "author": self.source["name"], "author_created": None, "followers": 0,
                        "ts": ts, "text": text[:2000], "url": e.get("link", "")})
        return out


def make_collector(cfg: dict, source: dict, state: dict):
    kind = source.get("type")
    if kind == "replay":
        return ReplayCollector(cfg, source, state)
    if kind == "rss":
        return RSSCollector(cfg, source, state)
    return None
