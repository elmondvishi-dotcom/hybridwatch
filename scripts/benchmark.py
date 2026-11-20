"""Measure processing speed (posts per second) on this machine.

    py scripts/benchmark.py            # 20,000 posts
    py scripts/benchmark.py --n 100000

Reports the speed of each fast-path step on its own, and the full pipeline
(including database writes and alert updates) processing a large backlog.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hybridwatch.config import load_config, resolve  # noqa: E402
from hybridwatch.pipeline import Pipeline  # noqa: E402
from hybridwatch.text import normalize  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20000)
    ap.add_argument("--batch", type=int, default=2000)
    args = ap.parse_args()
    cfg = load_config()
    base = [json.loads(l) for l in open(resolve(cfg, cfg["replay"]["file"]), encoding="utf-8")]
    random.seed(1)
    posts = []
    t0 = base[0]["ts"]
    for i in range(args.n):
        p = dict(random.choice(base))
        p["ext_id"] = f"bench-{i}"
        p["ts"] = t0 + i * (48 * 3600 / args.n)
        posts.append(p)

    pipe = Pipeline(cfg, db_path=str(Path(tempfile.mkdtemp()) / "bench.db"), quiet=True)
    pipe.factchecks.refresh_sources([f for f in cfg["factcheckers"] if f.get("type") == "file"])
    texts = [p["text"] for p in posts]

    print(f"Benchmark on {args.n:,} posts\n\nFast path, step by step:")
    t = time.perf_counter(); norms = [normalize(x) for x in texts]; dt = time.perf_counter() - t
    print(f"  normalise (Cyrillic->Latin, diacritics)  {args.n / dt:>10,.0f} posts/s")
    t = time.perf_counter(); vecs = pipe.embedder.embed(texts, norms); dt = time.perf_counter() - t
    print(f"  embeddings ({type(pipe.embedder).__name__})        {args.n / dt:>10,.0f} posts/s")
    t = time.perf_counter(); sigs = [pipe.hasher.signature(n) for n in norms]; dt = time.perf_counter() - t
    print(f"  MinHash signatures                       {args.n / dt:>10,.0f} posts/s")
    t = time.perf_counter()
    for i, s in enumerate(sigs):
        pipe.lsh.query(s); pipe.lsh.add(-i - 1, s, 0)
    dt = time.perf_counter() - t
    print(f"  LSH near-duplicate lookup                {args.n / dt:>10,.0f} posts/s")
    pipe.lsh.prune(1)
    t = time.perf_counter(); pipe.factchecks.match(vecs, texts, 0.4, llm_budget=0); dt = time.perf_counter() - t
    print(f"  debunk similarity search                 {args.n / dt:>10,.0f} posts/s")

    pipe2 = Pipeline(cfg, db_path=str(Path(tempfile.mkdtemp()) / "bench2.db"), quiet=True)
    pipe2.factchecks.refresh_sources([f for f in cfg["factcheckers"] if f.get("type") == "file"])
    t = time.perf_counter()
    for i in range(0, len(posts), args.batch):
        pipe2.process(posts[i:i + args.batch])
    dt = time.perf_counter() - t
    print(f"\nFull pipeline (clustering + DB writes + alert updates), batches of {args.batch}:")
    print(f"  {args.n:,} posts in {dt:.1f}s  ->  {args.n / dt:,.0f} posts/s")
    print(f"  narratives: {pipe2.conn.execute('SELECT COUNT(*) FROM narratives').fetchone()[0]}, "
          f"alerts: {pipe2.conn.execute('SELECT COUNT(*) FROM alerts').fetchone()[0]}")


if __name__ == "__main__":
    main()
