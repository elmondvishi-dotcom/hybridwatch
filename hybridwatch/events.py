"""Events: WHAT happened, WHERE, how sure we are, and why it matters.

Narratives are groups of posts about one story. This module turns them into events of a threat category:

  security_incident     explosions, shootings, attacks, kidnappings
  organized_activity    protests, roadblocks, mobilisation, smuggling, arrests of criminal groups
  infrastructure        power / water / road / telecom disruptions
  regional              Belgrade, Tirana, Brussels, dialogue, military moves, sanctions
  disinformation        disinformation and coordinated campaigns (from the narrative alerts)
  (cyber incidents come from the cyber module and are added to the picture by the API)

A narrative with a coordinated campaign is split: the copy-paste posts form an INFORMATION event, the organic
posts are classified on their own (e.g. the real power outage vs. the "it was sabotage" campaign).
Narratives of the same category and place that start close in time are merged into one event.

Every event keeps its sources (traceable), timeline points (first mention, first credible report, official
statement), indicators and an Admiralty-style confidence. It never says a claim is true: it reports who says what.
"""
from __future__ import annotations

import json
import sqlite3
import time
from collections import Counter, defaultdict

from .db import jloads
from .text import english_part, normalize, shorten

CATEGORIES = ("security_incident", "organized_activity", "infrastructure", "regional", "disinformation", "cyber")
LABEL = {"security_incident": "Security incident", "organized_activity": "Organized activity",
         "infrastructure": "Infrastructure disruption", "regional": "Regional development",
         "disinformation": "Disinformation", "cyber": "Cyber incident"}
LEVELS = ["info", "watch", "threat", "critical"]
# content categories come from config: threat_categories (disinformation comes from the alerts, cyber from the
# cyber module, so they are not read from post wording here)
CONTENT = ("security_incident", "organized_activity", "infrastructure", "regional")
REL_RANK = "ABCDEF"
CRED_WEIGHT = {1: 0.95, 2: 0.85, 3: 0.6, 4: 0.4, 5: 0.3, 6: 0.05}
REL_WEIGHT = {"A": 1.0, "B": 0.9, "C": 0.75, "D": 0.6, "E": 0.45, "F": 0.4}


def _tokens(n: str) -> set[str]:
    return {t.lstrip("#@") for t in n.split()}


def _match(n: str, tokens: set[str], stem: str) -> bool:
    """'$' = whole word, a space = phrase, otherwise word prefix (same rule as the glossary)."""
    if stem.endswith("$"):
        return stem[:-1] in tokens
    if " " in stem:
        return stem in n
    return any(t.startswith(stem) for t in tokens)


def _typical(texts: list[str]) -> str:
    """The text whose words are most shared by the others: personal fillers ('honestly', 'lol', 'pictures from
    today') are rare across posts and pull a text's score down."""
    docs = [set(normalize(t).split()) for t in texts]
    df = Counter(w for d in docs for w in d)
    best = max(range(len(texts)), key=lambda i: sum(df[w] for w in docs[i]) / (len(docs[i]) or 1) - 0.02 * len(docs[i]))
    return texts[best]


class Gazetteer:
    def __init__(self, places: list[dict]):
        self.places = [dict(p, stems=[normalize(s) if not s.endswith("$") else normalize(s[:-1]) + "$" for s in p["stems"]])
                       for p in places]
        self.by_name = {p["name"]: p for p in self.places}

    def find(self, n: str, tokens: set[str]) -> list[str]:
        return [p["name"] for p in self.places if any(_match(n, tokens, s) for s in p["stems"])]


class EventDetector:
    def __init__(self, conn: sqlite3.Connection, cfg: dict, threat=None):
        from .threat import StemSet, ThreatModel
        self.conn = conn
        self.cfg = cfg.get("events", {}) or {}
        self.threat = threat or ThreatModel(cfg)
        self.tax = {cat: {"words": StemSet(d.get("keywords")), "needs": StemSet(d.get("needs"))}
                    for cat, d in (cfg.get("threat_categories") or {}).items() if cat in CONTENT}
        self.gaz = Gazetteer(cfg.get("gazetteer") or [])
        self._cache: dict[int, tuple] = {}      # post id -> (categories, places); post text never changes
        self._last = 0.0

    @staticmethod
    def _stem(w: str) -> str:
        return normalize(w[:-1]) + "$" if w.endswith("$") else normalize(w)

    # ------------------------------------------------------------------ per post
    def classify_post(self, pid: int, text: str, translation: str | None) -> tuple[set, list]:
        if pid in self._cache:
            return self._cache[pid]
        n = normalize(f"{text} {translation or ''}")
        tok = _tokens(n)
        cats = set()
        for cat, d in self.tax.items():
            if d["words"].search(n) and (not d["needs"] or d["needs"].search(n)):
                cats.add(cat)
        out = (cats, self.gaz.find(n, tok))
        self._cache[pid] = out
        return out

    # ------------------------------------------------------------------ main
    def run(self, now: float, force: bool = False) -> dict:
        if not force and time.time() - self._last < self.cfg.get("recompute_seconds", 20):
            return {}
        self._last = time.time()
        min_posts = self.cfg.get("min_posts", 3)
        window = now - 7 * 86400
        narrs = {r["id"]: r for r in self.conn.execute(
            "SELECT id, size FROM narratives WHERE size>=? AND last_ts>=?", (min_posts, window))}
        if not narrs:
            return {}
        alerts = {r["narrative_id"]: r for r in self.conn.execute(
            "SELECT id, narrative_id, type, severity, score, threat, confidence, signals, status FROM alerts "
            "WHERE status!='merged'")}
        posts = defaultdict(list)
        ids = list(narrs)
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            for p in self.conn.execute(
                    f"SELECT id, narrative_id, ts, text, translation, source, source_type, credibility, author, lang, url, "
                    f"dup_group, debunk_id FROM posts WHERE narrative_id IN ({','.join('?' * len(chunk))}) ORDER BY ts", chunk):
                posts[p["narrative_id"]].append(p)

        cands = []
        for nid, ps in posts.items():
            a = alerts.get(nid)
            sig = jloads(a["signals"], {}) if a else {}
            co = sig.get("coordination", {})
            groups = set(co.get("groups") or [co.get("group")]) - {None}
            info_part, content_part = [], ps
            if a and groups:                                   # campaign vs organic posts
                info_part = [p for p in ps if p["dup_group"] in groups]
                content_part = [p for p in ps if p["dup_group"] not in groups]
            elif a and ("debunk_match" in sig or "hybrid" in sig):
                info_part, content_part = ps, []
            if info_part:
                cands.append(self._candidate("disinformation", nid, info_part, a, sig))
                if "debunk_match" in sig:
                    # the organic rest is people discussing a debunked claim, not a separate real-world event
                    content_part = []
            for part in self._split_by_place(content_part):
                cat = self._content_category(part)
                if cat:
                    cands.append(self._candidate(cat, nid, part, a if not info_part else None, {}))

        events = self._merge([c for c in cands if c])
        keep = set()
        for ev in events:
            keep.add(self._upsert(ev, now))
        stale = [r[0] for r in self.conn.execute("SELECT key FROM events WHERE last_ts>=?", (window,)) if r[0] not in keep]
        for k in stale:
            self.conn.execute("DELETE FROM events WHERE key=?", (k,))
        self.conn.commit()
        return {"events": len(keep), "removed": len(stale)}

    def _split_by_place(self, ps) -> list[list]:
        """One narrative can hold stories from different places (e.g. two border crossings share the glossary
        concept 'border'). Split it when at least two places each have enough posts; posts without a place
        go to the largest group."""
        if not ps:
            return []
        groups, unplaced = defaultdict(list), []
        for p in ps:
            _, places = self.classify_post(p["id"], p["text"], p["translation"])
            local = [x for x in places if not self.gaz.by_name[x].get("region")]
            (groups[local[0]] if local else unplaced).append(p)
        big = {k: v for k, v in groups.items() if len(v) >= self.cfg.get("min_posts", 3)}
        if len(big) < 2:
            return [ps]
        parts = sorted(big.values(), key=len, reverse=True)
        small = [p for k, v in groups.items() if k not in big for p in v]
        parts[0] = sorted(parts[0] + unplaced + small, key=lambda p: p["ts"])
        return parts

    def _content_category(self, ps) -> str | None:
        if len(ps) < self.cfg.get("min_posts", 3):
            return None
        counts = Counter()
        for p in ps:
            cats, _ = self.classify_post(p["id"], p["text"], p["translation"])
            counts.update(cats)
        if not counts:
            return None
        cat, n = counts.most_common(1)[0]
        return cat if n >= self.cfg.get("min_posts", 3) and n / len(ps) >= self.cfg.get("min_share", 0.25) else None

    def _candidate(self, cat, nid, ps, alert, sig) -> dict | None:
        if not ps:
            return None
        if cat != "disinformation":
            # evidence = posts that speak about this kind of event (or its place): an off-topic post that was
            # clustered into the narrative must not count as a source or an "official statement" for it
            on = [p for p in ps if cat in self.classify_post(p["id"], p["text"], p["translation"])[0]]
            where = Counter(x for p in on for x in self.classify_post(p["id"], p["text"], p["translation"])[1])
            top = where.most_common(1)[0][0] if where else None
            on_ids = {p["id"] for p in on}
            ps = [p for p in ps if p["id"] in on_ids
                  or (top and top in self.classify_post(p["id"], p["text"], p["translation"])[1])]
            if len(ps) < self.cfg.get("min_posts", 3):
                return None
        places = Counter()
        for p in ps:
            _, pl = self.classify_post(p["id"], p["text"], p["translation"])
            places.update(set(pl))
        place = None
        if places:
            # Kosovo places first; a regional capital only for regional developments
            ranked = [(n, name) for name, n in places.items()
                      if cat == "regional" or not self.gaz.by_name[name].get("region")]
            ranked.sort(reverse=True)
            if ranked and ranked[0][0] >= max(2, 0.1 * len(ps)):
                place = ranked[0][1]
        return {"category": cat, "place": place, "nids": [nid], "posts": list(ps),
                "alerts": [alert] if alert else [], "sig": sig, "part": "campaign" if cat == "disinformation" else "organic"}

    def _merge(self, cands: list[dict]) -> list[dict]:
        """Same category + same known place + starting within merge_hours -> one event (not for campaigns)."""
        hours = self.cfg.get("merge_hours", 12) * 3600
        out, groups = [], defaultdict(list)
        for c in cands:
            if c["category"] == "disinformation" or not c["place"]:
                out.append(c)
            else:
                groups[(c["category"], c["place"])].append(c)
        for g in groups.values():
            g.sort(key=lambda c: c["posts"][0]["ts"])
            cur = g[0]
            for c in g[1:]:
                if c["posts"][0]["ts"] - cur["posts"][-1]["ts"] <= hours:
                    cur = dict(cur, nids=cur["nids"] + c["nids"], posts=sorted(cur["posts"] + c["posts"], key=lambda p: p["ts"]),
                               alerts=cur["alerts"] + c["alerts"])
                else:
                    out.append(cur)
                    cur = c
            out.append(cur)
        return out

    # ------------------------------------------------------------------ one event
    def _upsert(self, c: dict, now: float) -> str:
        ps = c["posts"]
        cat = c["category"]
        key = f"{cat}|{c['place'] or '-'}|n{min(c['nids'])}|{c['part']}"
        sources = {}
        for p in ps:
            sources.setdefault(p["source"], p)
        credible = [p for s, p in sources.items() if p["credibility"] == "credible" and p["source_type"] == "news"]
        official = [p for s, p in sources.items() if p["source_type"] in ("official", "cert")]
        lowcred = [p for s, p in sources.items() if p["credibility"] == "low"]
        langs = Counter(p["lang"] for p in ps)
        first_cred = min((p["ts"] for p in ps if p["credibility"] == "credible" and p["source_type"] == "news"), default=None)
        first_off = min((p["ts"] for p in ps if p["source_type"] in ("official", "cert")), default=None)
        place = self.gaz.by_name.get(c["place"]) if c["place"] else None
        alerts = c["alerts"]

        # title: a credible headline (in English) beats a social post; campaigns use the fact-checked claim
        title = None
        if cat == "disinformation":
            sig = c["sig"]
            if "debunk_match" in sig:
                fc = self.conn.execute("SELECT claim, kind FROM factchecks WHERE id=?",
                                       (sig["debunk_match"]["factcheck_id"],)).fetchone()
                if fc and fc["kind"] != "analyst":
                    title = english_part(fc["claim"])
        if not title:
            credible_pool = [p for p in ps if p["credibility"] == "credible"] if cat != "disinformation" else []
            pool = credible_pool or ps
            en = [p["translation"] if p["lang"] != "en" and p["translation"] else p["text"] for p in pool
                  if p["lang"] == "en" or p["translation"]]
            if en and not credible_pool:
                en = [_typical(en)]       # social posts only: the most typical wording, not the first post
            title = shorten(en[0] if en else pool[0]["text"], 120)

        # confidence (Admiralty): reliability of the best source, credibility from corroboration
        if official:
            rel = "B"
        elif credible:
            rel = "B"
        elif lowcred:
            rel = "E"
        else:
            rel = "F"
        authors = len({p["author"] for p in ps})
        if cat == "disinformation":
            cred, conf = None, max((a["confidence"] or 0 for a in alerts), default=0.5)
        else:
            cred = (1 if official and credible else 2 if len(credible) >= 2 or (official and len(sources) >= 3)
                    else 3 if credible or official else 4 if authors >= 10 else 5)
            conf = round(0.75 * CRED_WEIGHT[cred] + 0.25 * REL_WEIGHT[rel], 2)

        # level (threat.py): a campaign takes the level of its alert; a real-world event is scored on its posts
        if cat == "disinformation" and alerts:
            top = max(alerts, key=lambda a: LEVELS.index(a["severity"]) if a["severity"] in LEVELS else 0)
            sev, score, assessment = top["severity"], top["score"], top["threat"]
        else:
            res = self.threat.assess([dict(p) for p in ps], {}, {"size": len(ps)}, key=key)
            sev, score, assessment = res["level"], res["score"], json.dumps(res)

        # indicators: why this event matters, each a checkable fact
        ind = []
        if credible:
            first = min(credible, key=lambda p: p["ts"])
            ind.append(f"Reported by {len(credible)} credible outlet(s); first: {first['author'] or first['source']}")
        if official:
            ind.append(f"Official statement: {official[0]['author'] or official[0]['source']}")
        if len([l for l in langs if l in ("sq", "sr")]) == 2:
            ind.append("Covered in both Albanian and Serbian sources")
        if place and place.get("north"):
            ind.append("Sensitive area: northern Kosovo")
        recent = sum(1 for p in ps if p["ts"] >= now - 3600)
        if recent >= 10:
            ind.append(f"Still growing: {recent} posts in the last hour")
        for a in alerts:
            s = jloads(a["signals"], {})
            if "coordination" in s:
                ind.append(f"Coordinated amplification: {s['coordination']['accounts']} accounts (alert #{a['id']})")
            if "debunk_match" in s:
                ind.append(f"Repeats a claim fact-checkers rated false (alert #{a['id']})")
            if "hybrid" in s:
                ind.append(f"Linked to cyber event #{s['hybrid']['events'][0]} (hybrid threat)")
        if not credible and not official and cat != "disinformation":
            ind.append("Not yet confirmed by any credible outlet")

        status = "developing" if now - ps[-1]["ts"] < 2 * 3600 else "ongoing" if now - ps[-1]["ts"] < 12 * 3600 else "quiet"
        row = {"key": key, "category": cat, "title": title, "place": c["place"],
               "lat": place["lat"] if place else None, "lon": place["lon"] if place else None,
               "narrative_ids": json.dumps(sorted(set(c["nids"]))), "alert_ids": json.dumps(sorted({a["id"] for a in alerts})),
               "part": c["part"], "first_ts": ps[0]["ts"], "last_ts": ps[-1]["ts"], "first_credible_ts": first_cred,
               "first_official_ts": first_off, "n_posts": len(ps), "n_sources": len(sources), "n_credible": len(credible),
               "n_official": len(official), "langs": json.dumps(dict(langs)), "severity": sev, "score": score, "threat": assessment, "reliability": rel,
               "credibility": cred, "confidence": conf, "status": status, "indicators": json.dumps(ind),
               "post_ids": json.dumps([p["id"] for p in ps]), "updated_at": now}
        cols = list(row)
        self.conn.execute(f"INSERT INTO events({','.join(cols)}) VALUES({','.join('?' * len(cols))}) "
                          f"ON CONFLICT(key) DO UPDATE SET {','.join(f'{k}=excluded.{k}' for k in cols if k != 'key')}",
                          [row[k] for k in cols])
        return key
