"""Cyber-event collectors. Same pattern as collectors.py: poll() returns a list of event dicts
(see verify.add_event for the fields). Every event is stored as a CLAIM and then verified.

  replay           recorded / synthetic events from a JSON file (demo), released by the data clock
  ransomware_live  Ransomware.live public API: victims for a country code (default XK) or a keyword
  ioda             IODA public API: internet outage events for a country or ASNs ("country:XK, asn:8661")
  kev              CISA Known Exploited Vulnerabilities feed, filtered by cyber.watch_products
  cert_rss         CERT announcements (KOS-CERT, GovCERT XK, ...): RSS, or a plain HTML page. Off while url is empty
  telegram         public Telegram channel preview (t.me/s/<channel>): hacktivist claims that mention Kosovo targets

Rules (enforced here, not left to configuration):
  * only public data, one GET per poll, a minimum interval per source, a timeout on every request
  * never download leaked data and never visit leak sites: leak-site links (post_url, claim_url, .onion)
    returned by trackers are dropped before anything is stored
  * never scan or attack a target
  * a failing source raises; the worker logs it and carries on with the others (graceful failure)
"""
from __future__ import annotations

import html
import json
import re
import time
from datetime import datetime

from .config import resolve
from .text import normalize

UA = {"User-Agent": "HybridWatch/0.1 (public-source research prototype)"}
SECTOR_WORDS = {
    "government": ["government", "ministry", "ministri", "ministarstv", "qeveri", "vlad", "municipal", "komuna", "opstina",
                   "agency", "agjenci", "parliament", "kuvend", "skupstin", "rks-gov", "police", "polici"],
    "energy": ["energy", "energji", "energet", "power", "electric", "grid", "kek", "keds", "kostt", "struj", "rrym"],
    "telecom": ["telecom", "telekom", "internet", "isp", "mobile", "ipko", "vala", "network"],
    "finance": ["bank", "banka", "finance", "financ", "insurance", "sigurim", "osiguranj", "payment"],
    "health": ["health", "hospital$", "hospitals$", "spital", "bolnic", "clinic", "klinik", "shendet", "zdravl"],
}
# Ransomware.live 'activity' values -> our sectors (checked before guessing from the description)
ACTIVITY_SECTOR = {"healthcare": "health", "public sector": "government", "government": "government",
                   "financial services": "finance", "finance": "finance", "banking": "finance", "energy": "energy",
                   "utilities": "energy", "telecommunication": "telecom", "telecommunications": "telecom"}
CLAIM_KINDS = [  # (kind, words) - first match wins
    ("ransomware_claim", ["ransomware", "ransom", "encrypted", "lockbit", "akira", "qilin"]),
    ("data_breach_claim", ["leak", "leaked", "breach", "dump", "database", "rrjedh", "curenj", "podaci", "te dhenat"]),
    ("defacement", ["deface", "defaced", "hacked website", "hakuar faqen"]),
    ("ddos_claim", ["ddos", "took down", "taken down", "brought down", "is down", "offline", "rrezuam", "srusili", "oboren"]),
    ("outage", ["outage", "nderprerje", "prekid", "nestanak"]),
    ("vulnerability", ["cve-", "vulnerability", "exploit", "dobesi", "ranjivost"]),
]
DOMAIN = re.compile(r"\b((?:[a-z0-9-]+\.)+(?:rks-gov\.net|gov\.rks|xk|com|net|org|eu|al|rs))\b", re.I)


def _has(n: str, tokens: set[str], word: str) -> bool:
    """Phrase -> substring; short word (<= 4 letters) -> whole token; longer word -> token prefix.
    Keeps 'isp' from matching 'ispit' (exam) and 'down' from matching 'download'."""
    if word.endswith("$"):        # explicit whole-word match ('hospital$' must not hit 'hospitality')
        return normalize(word[:-1]) in tokens
    w = normalize(word)
    if " " in w or "-" in word:
        return w in n
    if len(w) <= 4:
        return w in tokens
    return any(t.startswith(w) for t in tokens)


def guess_sector(text: str) -> str:
    n = normalize(text)
    tokens = {t.lstrip("#@") for t in n.split()}   # '#ddos' counts as 'ddos'
    for sector, words in SECTOR_WORDS.items():
        if any(_has(n, tokens, w) for w in words):
            return sector
    return "other"


def guess_kind(text: str) -> str | None:
    n = normalize(text)
    tokens = {t.lstrip("#@") for t in n.split()}   # '#ddos' counts as 'ddos'
    for kind, words in CLAIM_KINDS:
        if any(_has(n, tokens, w) for w in words):
            return kind
    return None


def _ts(s) -> float | None:
    if not s:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


class CyberCollector:
    kind = "base"
    min_interval = 600          # seconds between polls of one source (rate limit)

    def __init__(self, cfg: dict, source: dict, state: dict):
        self.cfg, self.source, self.state = cfg, source, state
        self.c = cfg.get("cyber", {}) or {}

    def due(self) -> bool:
        """Rate limit: at most one poll per min_interval (and respect a back-off after HTTP 429)."""
        interval = self.source.get("interval_minutes", self.min_interval / 60) * 60
        return time.time() >= max(self.state.get("last_poll", 0) + interval, self.state.get("backoff_until", 0))

    def get(self, url: str, **kw):
        import httpx
        self.state["last_poll"] = time.time()
        r = httpx.get(url, timeout=self.c.get("http_timeout", 10), headers=UA, follow_redirects=True, **kw)
        if r.status_code == 429:
            self.state["backoff_until"] = time.time() + 900
            raise RuntimeError("rate limited by the source (HTTP 429), backing off 15 min")
        return r

    def seen(self, key: str) -> bool:
        """Remember the last 2000 item ids, so re-polling never returns the same item twice."""
        ids = self.state.setdefault("seen", [])
        if key in ids:
            return True
        ids.append(key)
        del ids[:-2000]
        return False

    def poll(self, now: float) -> list[dict]:
        raise NotImplementedError


class ReplayCyber(CyberCollector):
    """Events from a JSON list; each is released once the data clock passes its first_seen."""
    kind = "replay"

    def __init__(self, cfg, source, state):
        super().__init__(cfg, source, state)
        with open(resolve(cfg, source["url"]), encoding="utf-8") as f:
            self.events = sorted(json.load(f), key=lambda e: e["first_seen"])
        self.state.setdefault("offset", 0)

    def due(self) -> bool:
        return True

    def poll(self, now: float) -> list[dict]:
        out = []
        i = self.state["offset"]
        while i < len(self.events) and self.events[i]["first_seen"] <= now:
            out.append(self.events[i])
            i += 1
        self.state["offset"] = i
        return out


class RansomwareLive(CyberCollector):
    """Ransomware.live public API (an aggregator; we never contact the leak sites themselves).
    url = country code (default XK) or 'search:<keyword>'."""
    kind = "ransomware_live"
    min_interval = 3600

    def poll(self, now):
        q = (self.source.get("url") or "XK").strip()
        base = self.c.get("ransomware_live_api", "https://api.ransomware.live/v2")
        url = f"{base}/searchvictims/{q[7:]}" if q.lower().startswith("search:") else f"{base}/countryvictims/{q}"
        r = self.get(url)
        if r.status_code == 404:          # "no victims found" is a normal answer
            return []
        r.raise_for_status()
        rows = r.json() if isinstance(r.json(), list) else []
        lookback = now - self.c.get("lookback_days", 30) * 86400
        known = self.state.setdefault("victims", {})   # victim -> first time we saw it listed
        out = []
        for v in rows:
            victim = (v.get("post_title") or v.get("victim") or "").strip()
            when = _ts(v.get("published") or v.get("attackdate") or v.get("discovered"))
            if not victim or not when:
                continue
            key = normalize(victim)
            first = known.setdefault(key, when)
            if when < lookback or self.seen(f"{key}|{int(when)}"):
                continue
            desc = (v.get("description") or "")[:400]
            raw = {"country": v.get("country"), "description": desc, "domain": (v.get("website") or "").lower() or None,
                   "activity": v.get("activity")}
            if first < when - 30 * 86400:   # same victim listed long before: likely a re-post
                raw["previously_listed_at"] = time.strftime("%d %b %Y", time.gmtime(first))
            # post_url / claim_url point to criminal leak sites: deliberately NOT stored
            out.append({
                "ext_id": f"rl:{key}:{int(when)}", "kind": "ransomware_claim", "title": f"Ransomware listing: {victim}",
                "target": victim,
                "sector": ACTIVITY_SECTOR.get((v.get("activity") or "").lower()) or guess_sector(desc),
                "claimed_actor": v.get("group_name"), "source_name": "Ransomware.live", "source_type": "ransomware_tracker",
                "source_url": f"https://www.ransomware.live/group/{v.get('group_name', '')}", "claimed_at": when,
                "first_seen": time.time(), "raw": raw})
        return out


class IodaOutages(CyberCollector):
    """IODA outage events. url = 'country:XK' (default) and/or 'asn:<n>' entries, comma separated."""
    kind = "ioda"
    min_interval = 900

    def poll(self, now):
        base = self.c.get("ioda_api", "https://api.ioda.inetintel.cc.gatech.edu/v2")
        out = []
        for ent in [e.strip() for e in (self.source.get("url") or "country:XK").split(",") if e.strip()]:
            etype, _, code = ent.partition(":")
            until = int(time.time())
            r = self.get(f"{base}/outages/events?entityType={etype}&entityCode={code}&from={until - 6 * 3600}&until={until}")
            r.raise_for_status()
            for e in r.json().get("data") or []:
                start = float(e.get("start") or 0)
                if not start or self.seen(f"{etype}:{code}:{int(start)}:{e.get('datasource')}"):
                    continue
                name = e.get("location_name") or f"{etype} {code}"
                dur = e.get("duration")
                detail = (f"IODA {e.get('datasource')} signal ({e.get('method')}) flagged an outage for {name}"
                          + (f", {int(dur) // 60} min" if dur else "") + f", score {float(e.get('score') or 0):,.0f}")
                page = f"https://ioda.inetintel.cc.gatech.edu/{etype}/{code}"
                out.append({
                    "ext_id": f"ioda:{etype}:{code}:{int(start)}", "kind": "outage",
                    "title": f"Internet outage signal: {name}", "target": name,
                    "sector": "telecom", "source_name": "IODA", "source_type": "ioda", "source_url": page,
                    "claimed_at": start, "first_seen": time.time(),
                    "raw": {"asn": code if etype == "asn" else None, "keywords": [code.lower()] if etype == "asn" else [],
                            "recorded_checks": [{"check_name": "ioda", "result": "supports", "detail": detail,
                                                 "evidence_url": page, "checked_at": time.time()}]}})
        return out


class CisaKev(CyberCollector):
    """CISA KEV feed: newly listed, actively exploited vulnerabilities in products from cyber.watch_products."""
    kind = "kev"
    min_interval = 6 * 3600

    def poll(self, now):
        r = self.get(self.c.get("kev_url", "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"))
        r.raise_for_status()
        watch = [w.lower() for w in self.c.get("watch_products", [])]
        since = time.time() - self.c.get("kev_lookback_days", 14) * 86400
        out = []
        for v in r.json().get("vulnerabilities", []):
            prod = f"{v.get('vendorProject', '')} {v.get('product', '')}".lower()
            added = _ts(v.get("dateAdded"))
            if not added or added < since or not any(w in prod for w in watch) or self.seen(v["cveID"]):
                continue
            detail = (f"{v['cveID']} ({v.get('vendorProject')} {v.get('product')}) listed as actively exploited since "
                      f"{v.get('dateAdded')}" + ("; used in ransomware" if v.get("knownRansomwareCampaignUse") == "Known" else ""))
            out.append({
                "ext_id": f"kev:{v['cveID']}", "kind": "vulnerability", "title": f"{v['cveID']}: {v.get('vulnerabilityName', '')}",
                "target": f"{v.get('vendorProject')} {v.get('product')}", "sector": "other", "source_name": "CISA KEV",
                "source_type": "kev", "source_url": f"https://nvd.nist.gov/vuln/detail/{v['cveID']}",
                "claimed_at": added, "first_seen": time.time(),
                "raw": {"cve": v["cveID"], "vendor": v.get("vendorProject"), "product": v.get("product"),
                        "description": (v.get("shortDescription") or "")[:400],
                        "recorded_checks": [{"check_name": "kev", "result": "supports", "detail": detail,
                                             "evidence_url": f"https://nvd.nist.gov/vuln/detail/{v['cveID']}",
                                             "checked_at": time.time()}]}})
        return out


class CertFeed(CyberCollector):
    """CERT announcements (official, reliability A). RSS first; for a plain HTML page, linked headings.
    Off while the url is empty (set the real KOS-CERT / GovCERT XK URL in Settings)."""
    kind = "cert_rss"
    min_interval = 1800

    def poll(self, now):
        url = (self.source.get("url") or "").strip()
        if not url:
            return []
        r = self.get(url)
        r.raise_for_status()
        items = []
        import feedparser
        feed = feedparser.parse(r.content)
        for e in feed.entries[:50]:
            ts = time.mktime(e.published_parsed) if getattr(e, "published_parsed", None) else time.time()
            items.append((e.get("title", ""), e.get("link", url), html.unescape(re.sub("<[^>]+>", " ", e.get("summary", ""))), ts))
        if not items:   # HTML fallback: <h2>/<h3> with a link
            for href, title in re.findall(r"<h[23][^>]*>\s*<a[^>]+href=\"([^\"]+)\"[^>]*>(.*?)</a>", r.text, re.S | re.I)[:50]:
                items.append((html.unescape(re.sub("<[^>]+>", "", title)).strip(), href, "", time.time()))
        out = []
        for title, link, summary, ts in items:
            text = f"{title} {summary}"
            kind = guess_kind(text)
            if not kind or self.seen(link or title):
                continue
            cve = re.search(r"CVE-\d{4}-\d{4,7}", text, re.I)
            dom = DOMAIN.search(text)
            out.append({
                "ext_id": f"cert:{link or title}", "kind": kind, "title": title[:200],
                "target": dom.group(1) if dom else title[:120], "sector": guess_sector(text),
                "source_name": self.source.get("name", "CERT"), "source_type": "cert", "source_url": link,
                "claimed_at": ts, "first_seen": time.time(),
                "raw": {"description": summary[:500], "cve": cve.group(0).upper() if cve else None,
                        "domain": dom.group(1).lower() if dom else None}})
        return out


class TelegramChannel(CyberCollector):
    """Public channel preview t.me/s/<channel>: claims (DDoS, leak, deface, ransomware) that mention Kosovo targets.
    The channel is treated as a self-interested claimant (Admiralty E) unless the source sets another source_type."""
    kind = "telegram"
    min_interval = 600
    MSG = re.compile(r'data-post="([^"]+)".*?tgme_widget_message_text[^>]*>(.*?)</div>.*?datetime="([^"]+)"', re.S)

    def poll(self, now):
        ch = (self.source.get("url") or "").strip().lstrip("@").replace("https://t.me/s/", "").replace("https://t.me/", "")
        if not ch:
            return []
        r = self.get(f"https://t.me/s/{ch}")
        r.raise_for_status()
        kos = [normalize(t) for t in self.c.get("kosovo_terms", [])] + [d.strip(".") for d in self.c.get("kosovo_domains", [])]
        out = []
        for post, body, dt in self.MSG.findall(r.text):
            text = html.unescape(re.sub(r"<br\s*/?>", " ", re.sub(r"<(?!br)[^>]+>", "", body))).strip()
            n = normalize(text)
            kind = guess_kind(text)
            if not kind or kind == "vulnerability" or not any(k and k in n for k in kos) or self.seen(post):
                continue
            dom = DOMAIN.search(text)
            target = dom.group(1).lower() if dom else (text[:80] + "…" if len(text) > 80 else text)
            out.append({
                "ext_id": f"tg:{post}", "kind": kind, "title": f"{ch}: {kind.replace('_', ' ')} — {text[:120]}",
                "target": target, "sector": guess_sector(text), "claimed_actor": ch,
                "source_name": f"t.me/{ch}", "source_type": self.source.get("source_type", "hacktivist"),
                "source_url": f"https://t.me/{post}", "claimed_at": _ts(dt) or time.time(), "first_seen": time.time(),
                "raw": {"text": text[:600], "domain": dom.group(1).lower() if dom else None}})
        return out


COLLECTORS = {c.kind: c for c in (ReplayCyber, RansomwareLive, IodaOutages, CisaKev, CertFeed, TelegramChannel)}


def make_cyber_collector(cfg: dict, source: dict, state: dict) -> CyberCollector | None:
    cls = COLLECTORS.get(source.get("type"))
    return cls(cfg, source, state) if cls else None
