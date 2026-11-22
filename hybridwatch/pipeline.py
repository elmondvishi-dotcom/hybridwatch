"""The processing pipeline: post batch in -> narratives, signals and alerts out.

FAST PATH (every post, vectorised):
  normalise -> language -> embedding -> MinHash/LSH near-duplicates
  -> online clustering into narratives -> debunk similarity search
SLOW PATH (only a few items per cycle):
  LLM labels / translations / debunk confirmation (if an LLM is configured)
"""
from __future__ import annotations

import json
import time
from collections import Counter

import numpy as np

from .alerts import AlertManager
from .threat import ThreatModel
from .clustering import OnlineClusterer
from .db import connect, get_setting, set_setting
from .config import resolve
from .embed import HashingEmbedder, make_embedder
from .factcheck import FactCheckLibrary
from .llm import LLM
from .minhash import LSHIndex, MinHasher
from .text import Glossary, detect_language, normalize


class Pipeline:
    def __init__(self, cfg: dict, db_path: str | None = None, quiet: bool = False):
        self.cfg = cfg
        self.th = dict(cfg["thresholds"])
        self.quiet = quiet
        self.conn = connect(db_path or resolve(cfg, cfg["db_path"]))
        self.glossary = Glossary(cfg.get("glossary", {}))
        self.embedder = make_embedder(cfg, self.glossary)
        self.neural = not isinstance(self.embedder, HashingEmbedder)
        if self.neural:   # XLM-RoBERTa similarities live on a different scale
            self.th.update(cfg.get("thresholds_neural", {}) or {})
        self.llm = LLM(cfg)
        # labels and translations run in a background thread (llm_worker.py), so a slow LLM never delays alerts
        self.llm_background = self.llm.enabled and (cfg.get("llm", {}) or {}).get("background", True)
        self._llm_inline = self.llm.enabled and not self.llm_background
        self.hasher = MinHasher()
        self.lsh = LSHIndex()
        self.dup_of: dict[int, int] = {}
        self.clusterer = OnlineClusterer(self.embedder.dim, self.th["cluster_similarity"])
        self.factchecks = FactCheckLibrary(self.conn, self.embedder, self.llm, cfg)
        self.threat = ThreatModel(cfg, self.llm, log=self.log)
        self.alerts = AlertManager(self.conn, cfg, self.glossary, self.threat)
        self.now = get_setting(self.conn, "sim_now", 0.0) or 0.0
        self.batches = 0
        self._restore()

    def log(self, *a):
        if not self.quiet:
            print(*a)

    # ------------------------------------------------------------------ state
    def _restore(self) -> None:
        rows = self.conn.execute("SELECT id, centroid, size FROM narratives WHERE centroid IS NOT NULL").fetchall()
        items = []
        for r in rows:
            c = np.frombuffer(r["centroid"], dtype=np.float32)
            if c.shape[0] == self.embedder.dim:
                items.append((r["id"], c, r["size"]))
        self.clusterer.load(items)
        since = self.now - self.th["lsh_window_hours"] * 3600
        for r in self.conn.execute("SELECT id, text, ts, dup_group FROM posts WHERE ts>=?", (since,)):
            if len(normalize(r["text"]).split()) < self.th.get("dup_min_tokens", 10):
                continue
            self.lsh.add(r["id"], self.hasher.signature(normalize(r["text"])), r["ts"])
            self.dup_of[r["id"]] = r["dup_group"]
        if items:
            self.log(f"[pipeline] restored {len(items)} narratives, {len(self.dup_of)} recent posts")

    # ------------------------------------------------------------------ main
    def process(self, posts: list[dict]) -> dict:
        t0 = time.perf_counter()
        if not posts:
            return {"posts": 0}
        # skip posts we already have
        ext = [p["ext_id"] for p in posts]
        have = set()
        for i in range(0, len(ext), 900):
            chunk = ext[i:i + 900]
            have |= {r[0] for r in self.conn.execute(
                f"SELECT ext_id FROM posts WHERE ext_id IN ({','.join('?' * len(chunk))})", chunk)}
        posts = sorted([p for p in posts if p["ext_id"] not in have], key=lambda p: p["ts"])
        if not posts:
            return {"posts": 0}

        texts = [p["text"] for p in posts]
        norms = [normalize(t) for t in texts]
        vecs = self.embedder.embed(texts, norms)
        sigs = [self.hasher.signature(n) for n in norms]
        langs = [p.get("lang") or detect_language(t) for p, t in zip(posts, texts)]
        collected = time.time()

        # insert posts to get ids
        ids = []
        for p, lang in zip(posts, langs):
            cur = self.conn.execute(
                "INSERT INTO posts(ext_id, source, source_type, credibility, author, author_created, followers, ts, "
                "text, translation, lang, url, collected_at, gt_narrative, gt_coordinated, gt_debunk) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (p["ext_id"], p.get("source"), p.get("source_type"), p.get("credibility", "unknown"),
                 p.get("author"), p.get("author_created"), p.get("followers", 0), p["ts"], p["text"],
                 p.get("translation"), lang, p.get("url"), collected,
                 p.get("gt_narrative"), int(bool(p.get("gt_coordinated"))) if "gt_coordinated" in p else None,
                 p.get("gt_debunk")))
            ids.append(cur.lastrowid)

        # near-duplicate groups (copy-paste detection)
        dups = []
        min_tokens = self.th.get("dup_min_tokens", 10)
        for pid, sig, p, n in zip(ids, sigs, posts, norms):
            group = pid
            if len(n.split()) < min_tokens:   # "great match!" repeats naturally - not evidence of coordination
                self.dup_of[pid] = group
                dups.append(group)
                continue
            best = self.th["dup_jaccard"]
            for cand in self.lsh.query(sig):
                s = MinHasher.similarity(sig, self.lsh.sigs[cand])
                if s >= best:
                    best, group = s, self.dup_of.get(cand, cand)
            self.lsh.add(pid, sig, p["ts"])
            self.dup_of[pid] = group
            dups.append(group)

        # narratives
        def new_narrative() -> int:
            return self.conn.execute("INSERT INTO narratives(size) VALUES(0)").lastrowid
        assigned = self.clusterer.assign(vecs, new_narrative)

        # debunked-claim matching
        matches = self.factchecks.match(vecs, texts, self.th["debunk_similarity"])

        self.conn.executemany(
            "UPDATE posts SET narrative_id=?, dup_group=?, debunk_id=?, debunk_score=? WHERE id=?",
            [(a[0], d, m[0], round(m[1], 3), pid) for a, d, m, pid in zip(assigned, dups, matches, ids)])

        touched = Counter(a[0] for a in assigned)
        tmin: dict[int, float] = {}
        tmax: dict[int, float] = {}
        for (nid, _), p in zip(assigned, posts):
            tmin[nid] = min(tmin.get(nid, p["ts"]), p["ts"])
            tmax[nid] = max(tmax.get(nid, p["ts"]), p["ts"])
        for nid in touched:
            self.conn.execute(
                "UPDATE narratives SET size=?, centroid=?, first_ts=COALESCE(first_ts, ?), last_ts=? WHERE id=?",
                (self.clusterer.size(nid), self.clusterer.centroid(nid).astype(np.float32).tobytes(),
                 tmin[nid], tmax[nid], nid))

        self.now = max(self.now, posts[-1]["ts"])
        self.lsh.prune(self.now - self.th["lsh_window_hours"] * 3600)
        for pid in [k for k in self.dup_of if k not in self.lsh.sigs]:
            del self.dup_of[pid]

        self.batches += 1
        if self.batches % 3 == 0:
            self._merge_narratives()
        self._merged_keep: set[int] = set()
        self._evidence_merges({d for d, pid in zip(dups, ids) if d != pid})
        touched = Counter({n: c for n, c in touched.items() if n in self.clusterer.ids})
        for nid in self._merged_keep:          # narratives that absorbed others need re-evaluation
            if nid in self.clusterer.ids:
                touched[nid] += 1

        # alerts for every narrative that received posts (plus open alerts, whose trend can cool down)
        results = Counter()
        self.threat.new_cycle()
        # a big backlog batch can span many hours: look for spikes across the whole batch
        span_h = (posts[-1]["ts"] - posts[0]["ts"]) / 3600 + 1
        self.alerts.th = {**self.th, "trend_lookback_hours": max(self.th.get("trend_lookback_hours", 6), span_h)}
        for nid in touched:
            r = self.alerts.update(nid, self.now)
            if r:
                results[r] += 1
        for nid in self._campaign_merges():
            self.alerts.update(nid, self.now)
        self.conn.commit()

        self._label_and_translate(touched)
        set_setting(self.conn, "sim_now", self.now)
        dt = time.perf_counter() - t0
        self.conn.execute("INSERT INTO metrics(wall_ts, sim_ts, n_posts, seconds, n_alerts) VALUES(?,?,?,?,?)",
                          (time.time(), self.now, len(posts), dt, sum(results.values())))
        self.conn.commit()
        return {"posts": len(posts), "seconds": dt, "narratives": len(touched), **results}

    # ------------------------------------------------------------------ merging
    def _merge(self, keep: int, drop: int, why: str) -> None:
        if keep == drop or drop not in self.clusterer.ids or keep not in self.clusterer.ids:
            return
        self.clusterer.merge(keep, drop)
        getattr(self, "_merged_keep", set()).add(keep)
        self.conn.execute("UPDATE posts SET narrative_id=? WHERE narrative_id=?", (keep, drop))
        self.conn.execute("UPDATE narratives SET size=?, centroid=?, labeled_at_size=0 WHERE id=?",
                          (self.clusterer.size(keep), self.clusterer.centroid(keep).tobytes(), keep))
        self.conn.execute("DELETE FROM narratives WHERE id=?", (drop,))
        a_keep = self.conn.execute("SELECT id, history FROM alerts WHERE narrative_id=?", (keep,)).fetchone()
        a_drop = self.conn.execute("SELECT id, status FROM alerts WHERE narrative_id=?", (drop,)).fetchone()
        if a_drop and not a_keep:
            self.conn.execute("UPDATE alerts SET narrative_id=? WHERE id=?", (keep, a_drop["id"]))
        elif a_drop:
            self.conn.execute("UPDATE alerts SET status='merged', narrative_id=NULL WHERE id=?", (a_drop["id"],))
            hist = json.loads(a_keep["history"] or "[]")
            hist.append({"t": time.time(), "sim": self.now, "event": f"Merged alert #{a_drop['id']} into this one ({why})"})
            self.conn.execute("UPDATE alerts SET history=? WHERE id=?", (json.dumps(hist), a_keep["id"]))
        self.log(f"[pipeline] merged narrative {drop} into {keep} ({why})")

    def _merge_narratives(self) -> None:
        """Similarity-based merge of narratives whose centroids drifted together."""
        for keep, drop, sim in self.clusterer.merge_candidates(self.th["merge_similarity"]):
            self._merge(keep, drop, f"similar content {sim:.2f}")

    def _evidence_merges(self, dup_groups: set[int]) -> None:
        """Merge narratives when there is hard evidence they are one story:
        * the same copy-pasted text landed in both (e.g. language versions of one template)
        * both mainly repeat the same debunked claim"""
        def biggest(nids):
            return max(nids, key=lambda n: self.clusterer.size(n) if n in self.clusterer.ids else -1)
        groups = [g for g in dup_groups if g is not None]
        for i in range(0, len(groups), 900):
            chunk = groups[i:i + 900]
            spans: dict[int, list[int]] = {}
            for r in self.conn.execute(
                    f"SELECT dup_group, narrative_id, COUNT(*) c FROM posts WHERE dup_group IN ({','.join('?' * len(chunk))}) "
                    "GROUP BY dup_group, narrative_id HAVING c >= 2", chunk):
                spans.setdefault(r["dup_group"], []).append(r["narrative_id"])
            for g, nids in spans.items():
                nids = [n for n in nids if n in self.clusterer.ids]
                if len(nids) > 1:
                    keep = biggest(nids)
                    for n in nids:
                        self._merge(keep, n, "same copy-pasted text")
        by_claim: dict[int, list[int]] = {}
        for r in self.conn.execute(
                "SELECT p.narrative_id, p.debunk_id, COUNT(*) c, n.size FROM posts p JOIN narratives n ON n.id=p.narrative_id "
                "WHERE p.debunk_id IS NOT NULL AND p.ts >= ? GROUP BY p.narrative_id, p.debunk_id",
                (self.now - 3 * 86400,)):
            if r["c"] >= self.th["debunk_min_posts"]:
                by_claim.setdefault(r["debunk_id"], []).append(r["narrative_id"])
        for claim, nids in by_claim.items():
            nids = [n for n in nids if n in self.clusterer.ids]
            if len(nids) > 1:
                keep = biggest(nids)
                for n in nids:
                    self._merge(keep, n, "repeat the same debunked claim")

    def _campaign_merges(self) -> set[int]:
        """Same network, same time: narratives whose copy-paste bursts start within the coordination
        window and share most of their accounts are one campaign pushed in several languages."""
        win = self.th["coord_window_minutes"] * 60
        items = []
        for a in self.conn.execute("SELECT narrative_id, signals FROM alerts WHERE status!='merged' AND narrative_id IS NOT NULL"):
            c = json.loads(a["signals"] or "{}").get("coordination")
            if not c or not c.get("groups"):
                continue
            g = c["groups"]
            authors = {r[0] for r in self.conn.execute(
                f"SELECT DISTINCT author FROM posts WHERE dup_group IN ({','.join('?' * len(g))})", g)}
            items.append((a["narrative_id"], c["start"], authors))
        keeps: set[int] = set()
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                (n1, s1, a1), (n2, s2, a2) = items[i], items[j]
                if abs(s1 - s2) > win or n1 not in self.clusterer.ids or n2 not in self.clusterer.ids:
                    continue
                same_accounts = len(a1 & a2) >= 0.5 * min(len(a1), len(a2))
                similar = float(self.clusterer.centroid(n1) @ self.clusterer.centroid(n2)) >= self.th["cluster_similarity"]
                if same_accounts or similar:
                    keep, drop = (n1, n2) if self.clusterer.size(n1) >= self.clusterer.size(n2) else (n2, n1)
                    self._merge(keep, drop, "same network, same time")
                    keeps.add(keep)
        return keeps

    # ------------------------------------------------------------------ slow path
    def _label_and_translate(self, touched=(), label_budget: int = 3, translate_budget: int = 5) -> None:
        """Cheap heuristic labels for every alerted narrative that changed; LLM labels (if configured)
        only when a narrative is new or has doubled in size."""
        touched = set(touched)
        rows = self.conn.execute(
            "SELECT n.id, n.size, n.labeled_at_size, n.label, n.label_source, a.signals FROM narratives n "
            "JOIN alerts a ON a.narrative_id=n.id").fetchall()
        for r in rows:
            needs_llm = self._llm_inline and (r["label_source"] != "llm" or r["size"] >= 2 * (r["labeled_at_size"] or 1))
            coord = json.loads(r["signals"] or "{}").get("coordination", {})
            groups = coord.get("groups") or []
            if not needs_llm and r["label"]:
                if r["label_source"] == "llm":
                    continue
                became_coord = bool(groups) and r["label_source"] != "auto-coord"
                grew = r["id"] in touched and r["size"] >= 1.25 * (r["labeled_at_size"] or 1)
                if not (became_coord or grew):
                    continue
            if groups:   # label a coordinated narrative by the claim being pushed
                sample = self.conn.execute(
                    f"SELECT id, text, lang FROM posts WHERE narrative_id=? AND dup_group IN ({','.join('?' * len(groups))}) "
                    "ORDER BY ts DESC LIMIT 100", (r["id"], *groups)).fetchall()
            else:
                sample = self.conn.execute(
                    "SELECT id, text, lang FROM posts WHERE narrative_id=? ORDER BY ts DESC LIMIT 200", (r["id"],)).fetchall()
            if not sample:
                continue
            label, source = None, "auto-coord" if groups else "auto"
            if needs_llm and label_budget > 0:
                label_budget -= 1
                label = self.llm.label([x["text"] for x in sample[:8]])
                source = "llm" if label else source
            rep = self._representatives(r["id"], sample)
            if not label:
                label = rep["label"]
            self.conn.execute("UPDATE narratives SET label=?, label_source=?, labeled_at_size=?, tags=?, "
                              "rep_post_id=?, en_post_id=? WHERE id=?",
                              (label, source, r["size"], json.dumps(rep["tags"]), rep["rep_id"], rep["en_id"], r["id"]))
        if self._llm_inline:
            todo = self.conn.execute(
                "SELECT p.id, p.text FROM posts p JOIN alerts a ON a.narrative_id=p.narrative_id "
                "WHERE p.translation IS NULL AND p.lang!='en' ORDER BY a.bumped_at DESC, p.followers DESC LIMIT ?",
                (translate_budget,)).fetchall()
            for t in todo:
                tr = self.llm.translate(t["text"])
                if tr:
                    self.conn.execute("UPDATE posts SET translation=? WHERE id=?", (tr, t["id"]))
        self.conn.commit()

    def _representatives(self, nid: int, sample) -> dict:
        """Pick what the UI shows for a narrative, as separate fields:
        tags (top glossary concepts), rep_id (most central post, any language),
        en_id (most central English post, if any) and the heuristic text label."""
        if self.neural:          # the neural model is slower: 60 recent posts are enough to pick a centre
            sample = sample[:60]
        texts = [s["text"] for s in sample]
        vecs = self.embedder.embed(texts)
        sims = vecs @ self.clusterer.centroid(nid)
        order = [int(i) for i in np.argsort(-sims)]
        central = order[0]
        en = next((i for i in order if sample[i]["lang"] == "en"), None)
        # label: prefer an English post if it is nearly as central (easier to read)
        best = en if en is not None and sims[en] >= sims[central] - 0.15 and en in order[:20] else central
        concepts = Counter(c for t in texts for c in set(self.glossary.concepts(normalize(t))))
        tags = [c for c, n in concepts.most_common(3) if n >= 2]
        rep = texts[best].strip().replace("\n", " ")
        rep = rep if len(rep) <= 120 else rep[:117] + "…"
        label = f"[{' · '.join(tags)}] “{rep}”" if tags else f"“{rep}”"
        return {"label": label, "tags": tags, "rep_id": sample[central]["id"],
                "en_id": sample[en]["id"] if en is not None else None}

    # ------------------------------------------------------------------ analyst feedback
    def confirmed_to_library(self, alert_id: int) -> None:
        """A confirmed narrative becomes a known claim, so repeats are recognised later."""
        a = self.conn.execute("SELECT narrative_id FROM alerts WHERE id=?", (alert_id,)).fetchone()
        if not a or a["narrative_id"] is None:
            return
        n = self.conn.execute("SELECT label, centroid FROM narratives WHERE id=?", (a["narrative_id"],)).fetchone()
        if n and n["centroid"]:
            self.factchecks.add([{"id": f"analyst-{alert_id}", "source": "Analyst-confirmed case",
                                  "claim": n["label"] or "", "verdict": "confirmed disinformation",
                                  "url": f"#alert-{alert_id}"}], kind="analyst",
                                embeddings=np.frombuffer(n["centroid"], dtype=np.float32)[None, :])
