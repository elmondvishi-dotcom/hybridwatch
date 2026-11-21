"""Cyber-event verification. Every cyber event is a CLAIM, never a fact.

Each check is stored in `verification_checks` (name, result, detail, evidence, time), so every
verdict can be traced back to the evidence behind it. The status is derived from the checks:

  confirmed         >= 1 technical check AND >= 1 independent source support it, nothing strong contradicts
  partly_confirmed  >= 1 technical or independent check supports it
  refuted           a technical check clearly contradicts it (site up the whole time, old re-post)
                    and no technical check supports it
  unverified        nothing yet

Confidence follows the NATO Admiralty Code: source reliability A-F from the source type,
information credibility 1-6 from the checks, plus a 0-1 score derived from both.

Where checks come from:
  * own data  - our own posts and fact-check library (always on, no network)
  * recorded  - check results that come with the event (demo scenario, or a collector that already
                checked something). Each is applied once the data clock reaches its time.
  * live      - availability GET of the public homepage, IODA, CISA KEV. Off unless
                `cyber.live_checks: true`. Only public data; a single GET, never a scan.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from urllib.parse import urlparse

from .db import jloads
from .text import normalize

KINDS = ("ddos_claim", "ransomware_claim", "defacement", "data_breach_claim", "outage", "vulnerability")
SECTORS = ("government", "energy", "telecom", "finance", "health", "other")
STATUSES = ("unverified", "partly_confirmed", "confirmed", "refuted")

# Admiralty source reliability by source type
SOURCE_RELIABILITY = {
    "cert": "A", "kev": "A",                    # official CERT / CISA catalogue
    "official": "B", "ioda": "B", "media": "B",  # government statement, measurement platform, established media
    "news": "C",                                 # other news outlets
    "ransomware_tracker": "D",                   # aggregator of criminal leak-site listings
    "hacktivist": "E",                           # self-interested claimant
    "telegram": "F", "unknown": "F",
}
REL_WEIGHT = {"A": 1.0, "B": 0.9, "C": 0.75, "D": 0.6, "E": 0.45, "F": 0.4}
CRED_WEIGHT = {1: 0.95, 2: 0.85, 3: 0.6, 4: 0.4, 5: 0.3, 6: 0.05}

# what kind of evidence each check is
TECHNICAL = {"availability", "content_hash", "ioda", "kev", "repost_check"}
INDEPENDENT = {"news_reports", "official_statement", "kev"}   # KEV is an official (CISA) listing
STRONG = {"availability", "content_hash", "repost_check"}       # a contradiction here can refute a claim

CHECK_LABELS = {
    "availability": "Homepage availability", "content_hash": "Homepage content", "ioda": "Internet outage signal (IODA)",
    "kev": "CISA Known Exploited Vulnerabilities", "watch_products": "Affects products we watch",
    "repost_check": "Earlier listing of the same victim", "kosovo_target": "Target is in Kosovo",
    "news_reports": "Independent news reports", "official_statement": "Official statement",
    "own_posts": "Related posts in our data", "factcheck_library": "Fact-check library",
}

CHECKS_BY_KIND = {
    "ddos_claim": ["availability", "ioda", "news_reports", "official_statement"],
    "defacement": ["availability", "content_hash", "news_reports", "official_statement"],
    "ransomware_claim": ["kosovo_target", "repost_check", "news_reports", "official_statement"],
    "data_breach_claim": ["kosovo_target", "repost_check", "news_reports", "official_statement"],
    "outage": ["ioda", "news_reports", "official_statement"],
    "vulnerability": ["kev", "watch_products"],
}
CAUSES = {"power cut": ["power cut", "nderprer", "rrym", "nestanak struje", "struj", "blackout"],
          "maintenance": ["maintenance", "mirembajt", "odrzavanj", "planned", "planifik"],
          "cyber attack": ["cyber", "kibernet", "sajber", "hack", "haker", "ddos", "attack", "sulm", "napad"]}
DAY = 86400.0


def target_terms(target: str | None, raw: dict) -> list[str]:
    """Words that identify the target in posts: configured keywords, else target name / domain parts."""
    if raw.get("keywords"):
        return [normalize(k) for k in raw["keywords"] if k]
    t = normalize((target or "").replace(".", " "))
    stop = {"www", "com", "net", "org", "gov", "rks", "kosovo", "the", "and", "sh", "llc", "jsc", "demo", "synthetic"}
    return [w for w in t.split() if len(w) >= 4 and w not in stop]


def add_event(conn: sqlite3.Connection, ev: dict, now: float) -> int | None:
    """Insert a cyber event (a claim). Returns its id, or None if this ext_id is already stored."""
    ext = ev.get("ext_id") or f"{ev.get('source_name')}|{ev.get('kind')}|{ev.get('target')}|{ev.get('claimed_at')}"
    if conn.execute("SELECT 1 FROM cyber_events WHERE ext_id=?", (ext,)).fetchone():
        return None
    rel = SOURCE_RELIABILITY.get(ev.get("source_type") or "unknown", "F")
    cur = conn.execute(
        "INSERT INTO cyber_events(ext_id, kind, title, target, sector, claimed_actor, source_name, source_type, "
        "source_url, claimed_at, first_seen, status, source_reliability, info_credibility, confidence, raw) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (ext, ev["kind"], ev.get("title"), ev.get("target"), ev.get("sector") or "other", ev.get("claimed_actor"),
         ev.get("source_name"), ev.get("source_type") or "unknown", ev.get("source_url"),
         ev.get("claimed_at") or now, ev.get("first_seen") or now, "unverified", rel,
         5 if rel in ("E", "F") else 4, _confidence(rel, 5 if rel in ("E", "F") else 4),
         json.dumps(ev.get("raw") or {})))
    conn.commit()
    return cur.lastrowid


def _confidence(rel: str, cred: int) -> float:
    return round(0.75 * CRED_WEIGHT[cred] + 0.25 * REL_WEIGHT.get(rel, 0.4), 2)


def decide(checks: list[dict]) -> tuple[str, list[str]]:
    """Status from the checks (one aggregated result per check name). Returns (status, supporting names)."""
    # per check name: supports if any probe supports, else contradicts if any contradicts, else inconclusive
    rank = {"supports": 3, "contradicts": 2, "inconclusive": 1, "error": 0}
    agg: dict[str, str] = {}
    n_contra: dict[str, int] = {}
    for c in checks:
        n, r = c["check_name"], c["result"]
        n_contra[n] = n_contra.get(n, 0) + (r == "contradicts")
        if rank.get(r, 0) > rank.get(agg.get(n), -1):
            agg[n] = r
    sup = [n for n, r in agg.items() if r == "supports" and (n in TECHNICAL or n in INDEPENDENT)]
    sup_tech = any(n in TECHNICAL for n in sup)
    sup_ind = any(n in INDEPENDENT for n in sup)
    # "up the whole time" needs at least two up-probes (and no down-probe); one probe is just a point in time
    strong_no = any(r == "contradicts" and n in STRONG and (n != "availability" or n_contra[n] >= 2)
                    for n, r in agg.items())
    if strong_no and not sup_tech:
        return "refuted", sup
    if sup_tech and sup_ind and not strong_no:
        return "confirmed", sup
    if sup:
        return "partly_confirmed", sup
    return "unverified", sup


def admiralty(status: str, rel: str, supporting: list[str]) -> tuple[int, float]:
    if status == "confirmed":
        cred = 1 if len([n for n in supporting if n in INDEPENDENT]) >= 2 else 2
    elif status == "partly_confirmed":
        cred = 3
    elif status == "refuted":
        cred = 6
    else:
        cred = 5 if rel in ("E", "F") else 4
    return cred, _confidence(rel, cred)


class Verifier:
    def __init__(self, conn: sqlite3.Connection, cfg: dict, log=print):
        self.conn = conn
        self.cfg = cfg.get("cyber", {}) or {}
        self.live = bool(self.cfg.get("live_checks", False))
        self.log = log
        self._kev: tuple[float, dict] | None = None
        self._last_get: dict[str, float] = {}

    # ------------------------------------------------------------------ scheduling
    def run(self, now: float) -> dict:
        """Verify new events and re-check open claims (unverified / partly confirmed) for 48 h."""
        every = self.cfg.get("recheck_minutes", 30) * 60
        window = self.cfg.get("recheck_hours", 48) * 3600
        due = self.conn.execute(
            "SELECT * FROM cyber_events WHERE first_seen<=? AND (checked_at IS NULL OR "
            "(status IN ('unverified','partly_confirmed') AND ?-first_seen<=? AND ?-checked_at>=?) OR "
            "EXISTS (SELECT 1 FROM json_each(json_extract(raw,'$.recorded_checks')) j "
            "        WHERE json_extract(j.value,'$.checked_at') > COALESCE(checked_at,0) "
            "          AND json_extract(j.value,'$.checked_at') <= ?))",
            (now, now, window, now, every, now)).fetchall()
        changed = 0
        for ev in due:
            changed += self.verify(ev, now)
        return {"checked": len(due), "changed": changed}

    # ------------------------------------------------------------------ one event
    def verify(self, ev: sqlite3.Row, now: float) -> bool:
        raw = jloads(ev["raw"], {}) or {}
        new: list[dict] = []
        # 1) recorded checks whose time has come (demo scenario / collector-provided)
        for c in raw.get("recorded_checks", []):
            if c.get("checked_at", 0) <= now and (ev["checked_at"] is None or c["checked_at"] > ev["checked_at"]):
                new.append(dict(c, origin="recorded"))
        # 2) checks on our own data (posts, fact-checks, earlier events)
        recorded_names = {c["check_name"] for c in raw.get("recorded_checks", [])}
        for name in CHECKS_BY_KIND.get(ev["kind"], []) + ["own_posts", "factcheck_library"]:
            fn = getattr(self, f"_own_{name}", None)
            if fn and name not in recorded_names:
                r = fn(ev, raw, now)
                if r:
                    new.append(dict(r, check_name=name, checked_at=now, origin="own_data"))
        # 3) live probes (off by default)
        if self.live:
            for name in CHECKS_BY_KIND.get(ev["kind"], []):
                fn = getattr(self, f"_live_{name}", None)
                if fn:
                    try:
                        r = fn(ev, raw, now)
                    except Exception as e:  # graceful failure: an error is a result, not a crash
                        r = {"result": "error", "detail": f"{type(e).__name__}: {e}"[:300]}
                    if r:
                        new.append(dict(r, check_name=name, checked_at=now, origin="live"))
        for c in new:
            self._store(ev["id"], c)

        checks = [dict(r) for r in self.conn.execute(
            "SELECT check_name, result FROM verification_checks WHERE event_id=?", (ev["id"],))]
        status, sup = decide(checks)
        cred, conf = admiralty(status, ev["source_reliability"], sup)
        verdict_at = ev["verdict_at"]
        if verdict_at is None and status in ("confirmed", "refuted"):
            verdict_at = max([c["checked_at"] for c in new] or [now])
        self.conn.execute("UPDATE cyber_events SET status=?, info_credibility=?, confidence=?, checked_at=?, "
                          "verdict_at=? WHERE id=?", (status, cred, conf, now, verdict_at, ev["id"]))
        self.conn.commit()
        if status != ev["status"]:
            self.log(f"[verify] #{ev['id']} {ev['kind']} {ev['target']}: {ev['status']} -> {status} "
                     f"({ev['source_reliability']}{cred})")
        return status != ev["status"]

    def _store(self, eid: int, c: dict) -> None:
        last = self.conn.execute("SELECT result, detail FROM verification_checks WHERE event_id=? AND check_name=? "
                                 "ORDER BY checked_at DESC, id DESC LIMIT 1", (eid, c["check_name"])).fetchone()
        # own-data checks are re-run on every pass: only store them when the answer changed
        if c.get("origin") == "own_data" and last and last["result"] == c["result"] and last["detail"] == c.get("detail"):
            return
        self.conn.execute("INSERT INTO verification_checks(event_id, check_name, result, detail, evidence_url, "
                          "checked_at, origin) VALUES(?,?,?,?,?,?,?)",
                          (eid, c["check_name"], c["result"], c.get("detail"), c.get("evidence_url"),
                           c["checked_at"], c.get("origin")))

    # ------------------------------------------------------------------ own-data checks
    def _terms(self, ev, raw) -> list[str]:
        return target_terms(ev["target"], raw)

    def _posts_about(self, ev, raw, now, credible_only=False, types=None):
        terms = self._terms(ev, raw)
        if not terms:
            return []
        lo, hi = (ev["claimed_at"] or now) - 3600, min(now, (ev["claimed_at"] or now) + DAY)
        q = "SELECT id, ts, text, translation, source, source_type, credibility, url FROM posts WHERE ts BETWEEN ? AND ?"
        args: list = [lo, hi]
        if credible_only:
            q += " AND credibility='credible'"
        if types:
            q += f" AND source_type IN ({','.join('?' * len(types))})"
            args += list(types)
        out = []
        for p in self.conn.execute(q + " ORDER BY ts", args):
            n = normalize(p["text"] + " " + (p["translation"] or ""))
            if any(t in n for t in terms):
                out.append(p)
        return out

    def _own_news_reports(self, ev, raw, now):
        hits = [p for p in self._posts_about(ev, raw, now, credible_only=True)
                if p["source_type"] not in ("cert", "official")]
        if not hits:
            return {"result": "inconclusive", "detail": "No credible outlet has reported it yet"}
        first = hits[0]
        detail = f"{len(hits)} credible report(s); first by {first['source']} at {_hm(first['ts'])}"
        if ev["kind"] == "outage":
            text = normalize(" ".join(p["text"] + " " + (p["translation"] or "") for p in hits))
            causes = [c for c, words in CAUSES.items() if any(w in text for w in words)]
            detail += f"; cause mentioned: {', '.join(causes)}" if causes else "; no cause given"
        return {"result": "supports", "detail": detail, "evidence_url": first["url"]}

    def _own_official_statement(self, ev, raw, now):
        hits = self._posts_about(ev, raw, now, types=("cert", "official"))
        if hits:
            return {"result": "supports", "detail": f"Statement by {hits[0]['source']} at {_hm(hits[0]['ts'])}",
                    "evidence_url": hits[0]["url"]}
        # a CERT announcement collected as its own cyber event (cert_rss collector) also counts
        terms = self._terms(ev, raw)
        t0 = ev["claimed_at"] or now
        for o in self.conn.execute("SELECT id, title, raw, source_name, source_url, claimed_at FROM cyber_events "
                                   "WHERE id!=? AND source_type IN ('cert','official') AND claimed_at BETWEEN ? AND ?",
                                   (ev["id"], t0 - 3600, min(now, t0 + DAY))):
            text = normalize(f"{o['title']} {(jloads(o['raw'], {}) or {}).get('description', '')}")
            if terms and any(t in text for t in terms):
                return {"result": "supports", "evidence_url": o["source_url"],
                        "detail": f"Announcement by {o['source_name']} at {_hm(o['claimed_at'])} (event #{o['id']})"}
        return None   # absence of a statement is not evidence either way; don't clutter the checklist

    def _own_own_posts(self, ev, raw, now):
        hits = self._posts_about(ev, raw, now)
        return {"result": "inconclusive", "detail": f"{len(hits)} post(s) in our data mention the target"
                + (f", first at {_hm(hits[0]['ts'])}" if hits else "")}

    def _own_factcheck_library(self, ev, raw, now):
        terms = self._terms(ev, raw)
        for f in self.conn.execute("SELECT source, claim, verdict, url FROM factchecks"):
            if terms and any(t in normalize(f["claim"] or "") for t in terms):
                return {"result": "inconclusive", "detail": f"Related fact-check by {f['source']}: "
                        f"'{(f['claim'] or '')[:100]}' rated {f['verdict']}", "evidence_url": f["url"]}
        return None

    def _own_kosovo_target(self, ev, raw, now):
        domains = self.cfg.get("kosovo_domains", [".rks-gov.net", ".xk", "-ks.com"])
        terms = [normalize(t) for t in self.cfg.get("kosovo_terms", ["kosovo", "kosova", "kosove", "prishtin", "pristin"])]
        dom = (raw.get("domain") or "").lower()
        text = normalize(" ".join(str(x) for x in (ev["target"], ev["title"], raw.get("description", ""), raw.get("country", ""))))
        if any(dom.endswith(d) for d in domains) or raw.get("country") in ("XK", "Kosovo") or any(t in text for t in terms):
            return {"result": "supports", "detail": "Domain, country or description points to Kosovo"}
        return {"result": "contradicts", "detail": "Nothing links this victim to Kosovo (domain, country, description)"}

    def _own_repost_check(self, ev, raw, now):
        if raw.get("previously_listed_at"):
            return {"result": "contradicts", "detail": f"Same victim was already listed on {_date(raw['previously_listed_at'])}"
                    " - this looks like an old re-post", "evidence_url": raw.get("previous_listing_url")}
        target = normalize(ev["target"] or "")
        for o in self.conn.execute("SELECT id, target, claimed_at FROM cyber_events WHERE id!=? AND kind IN "
                                   "('ransomware_claim','data_breach_claim') AND claimed_at < ?",
                                   (ev["id"], (ev["claimed_at"] or now) - 30 * DAY)):
            if target and normalize(o["target"] or "") == target:
                return {"result": "contradicts", "detail": f"Same victim already listed on {_date(o['claimed_at'])} "
                        f"(event #{o['id']}) - this looks like an old re-post"}
        return {"result": "inconclusive", "detail": "No earlier listing of this victim found"}

    def _own_watch_products(self, ev, raw, now):
        watch = [w.lower() for w in self.cfg.get("watch_products", [])]
        prod = f"{raw.get('vendor', '')} {raw.get('product', '')}".lower()
        hit = [w for w in watch if w in prod]
        return {"result": "supports" if hit else "inconclusive",
                "detail": f"Matches watched product(s): {', '.join(hit)}" if hit else "Not in our watched products list"}

    # ------------------------------------------------------------------ live checks (cyber.live_checks: true)
    def _homepage(self, ev, raw) -> str | None:
        url = raw.get("url") or raw.get("homepage")
        if not url and raw.get("domain"):
            url = f"https://{raw['domain']}/"
        return url

    def _get(self, url: str):
        """A single GET of a public homepage, rate-limited per host. Never more than that."""
        import httpx
        host = urlparse(url).netloc
        gap = self.cfg.get("availability_min_interval_minutes", 10) * 60
        if time.time() - self._last_get.get(host, 0) < gap:
            return None
        self._last_get[host] = time.time()
        t0 = time.perf_counter()
        try:
            r = httpx.get(url, timeout=self.cfg.get("http_timeout", 10), follow_redirects=True,
                          headers={"User-Agent": "HybridWatch/0.1 (public availability check)"})
            return r, time.perf_counter() - t0
        except httpx.HTTPError as e:
            return e, time.perf_counter() - t0

    def _live_availability(self, ev, raw, now):
        url = self._homepage(ev, raw)
        if not url:
            return None
        got = self._get(url)
        if got is None:
            return None
        r, secs = got
        near = abs(time.time() - (ev["claimed_at"] or now)) <= 3600
        if isinstance(r, Exception) or r.status_code >= 500:
            what = type(r).__name__ if isinstance(r, Exception) else f"HTTP {r.status_code}"
            return {"result": "supports", "detail": f"Homepage not reachable ({what}, {secs:.1f}s)", "evidence_url": url}
        detail = f"Homepage up (HTTP {r.status_code}, {secs:.1f}s)"
        if near:
            return {"result": "contradicts", "detail": detail + " within an hour of the claimed attack", "evidence_url": url}
        return {"result": "inconclusive", "detail": detail + "; says nothing about the claimed time", "evidence_url": url}

    def _live_content_hash(self, ev, raw, now):
        url, base = self._homepage(ev, raw), raw.get("baseline_hash")
        if not url or not base:
            return None
        got = self._get(url)
        if got is None or isinstance(got[0], Exception):
            return None
        h = hashlib.sha256(got[0].content).hexdigest()
        if h == base:
            return {"result": "contradicts", "detail": "Homepage content unchanged from the known baseline", "evidence_url": url}
        return {"result": "supports", "detail": f"Homepage content changed (sha256 {h[:12]}…)", "evidence_url": url}

    def _live_ioda(self, ev, raw, now):
        import httpx
        ent_type, code = ("asn", raw["asn"]) if raw.get("asn") else ("country", "XK")
        t0 = int((ev["claimed_at"] or now) - 3600)
        url = (f"{self.cfg.get('ioda_api', 'https://api.ioda.inetintel.cc.gatech.edu/v2')}/outages/events"
               f"?entityType={ent_type}&entityCode={code}&from={t0}&until={t0 + 4 * 3600}")
        r = httpx.get(url, timeout=self.cfg.get("http_timeout", 10))
        r.raise_for_status()
        events = (r.json() or {}).get("data") or []
        page = f"https://ioda.inetintel.cc.gatech.edu/{ent_type}/{code}"
        if events:
            e = events[0]
            return {"result": "supports", "evidence_url": page,
                    "detail": f"IODA outage event for {ent_type} {code} from {_hm(float(e.get('start', t0)))}"
                              + (f", {int(e['duration']) // 60} min" if e.get("duration") else "")}
        # a small DDoS rarely shows in network-level data; only an outage claim is contradicted by silence
        return {"result": "contradicts" if ev["kind"] == "outage" else "inconclusive",
                "detail": f"No IODA outage event for {ent_type} {code} around the claimed time", "evidence_url": page}

    def _live_kev(self, ev, raw, now):
        import httpx
        cve = (raw.get("cve") or "").upper()
        if not cve:
            return None
        if not self._kev or time.time() - self._kev[0] > DAY:
            feed = self.cfg.get("kev_url", "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json")
            data = httpx.get(feed, timeout=30).json()
            self._kev = (time.time(), {v["cveID"]: v for v in data.get("vulnerabilities", [])})
        v = self._kev[1].get(cve)
        if not v:
            return {"result": "inconclusive", "detail": f"{cve} is not in the CISA KEV catalogue"}
        return {"result": "supports", "detail": f"{cve} ({v['vendorProject']} {v['product']}) listed as actively exploited "
                f"since {v['dateAdded']}" + ("; used in ransomware" if v.get("knownRansomwareCampaignUse") == "Known" else ""),
                "evidence_url": f"https://nvd.nist.gov/vuln/detail/{cve}"}


def _hm(ts: float) -> str:
    return time.strftime("%d %b %H:%M", time.gmtime(ts))


def _date(ts) -> str:
    if isinstance(ts, str):
        return ts
    return time.strftime("%d %b %Y", time.gmtime(ts))
