"""Data for the visual layer: the Overview page, the alert charts (score breakdown, network, spread, splits),
the monitored-sites grid and the cyber "claim vs reality" chart.

Read-only, like the rest of the API. Charts show evidence; nothing here decides anything. Wording stays
"suspected", "claimed", "confirmed by". Times are data time (the replay clock in the demo), in UTC.

Included by api.py (app.include_router). Imports api's helpers, so api must be fully defined first.
"""
from __future__ import annotations

import re
import time
from collections import Counter, defaultdict
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query

from .api import (ALERT_SQL, CFG, SEV_RANK, _coord_groups, _network_accounts, _now, _title, db)
from .db import jloads
from .hybrid import KIND_LABEL, links_for_alert
from .places import municipalities
from .text import has_cyrillic

router = APIRouter()

DAY = 86400
LEVELS = ("watch", "threat", "critical")
CATEGORIES = ("security_incident", "organized_activity", "infrastructure", "regional", "disinformation", "cyber")
POST_CATEGORIES = CATEGORIES[:5]          # cyber incidents are not post-based: shown as markers
# statuses that count as "active" on the overview (dismissed / merged are the analyst's "not a threat")
ACTIVE = ("new", "needs_review", "watching", "confirmed")
LANG_NAME = {"sq": "Albanian", "sr_latn": "Serbian (Latin)", "sr_cyrl": "Serbian (Cyrillic)", "en": "English",
             "other": "Other / unknown"}
PLATFORM_NAME = {"telegram": "Telegram", "facebook": "Facebook", "x": "X (Twitter)", "tiktok": "TikTok",
                 "youtube": "YouTube", "news": "News site", "rss": "News site", "official": "Official source",
                 "cert": "CERT", "replay": "Replay file"}
SLOW_SECONDS = 2.0                        # a homepage answering slower than this counts as "slow"
# glossary concepts that say what a narrative CLAIMS (place and target concepts don't): used in the banner
STORY_WORDS = ("sabotage", "poison", "withdraw", "freeze", "leak", "hackers", "ddos", "ransomware", "closed", "price")


def _lang_key(lang: str | None, text: str) -> str:
    if lang == "sr":
        return "sr_cyrl" if has_cyrillic(text or "") else "sr_latn"
    return lang if lang in ("sq", "en") else "other"


def _threat(r) -> dict:
    return jloads(r["threat"], {}) or {}


def _categories(r) -> list[str]:
    cats = [c for c in _threat(r).get("categories", []) if c in CATEGORIES]
    return cats or ["disinformation"]     # every alert is an information case at least


# ------------------------------------------------------------------ where: municipalities per alert
_PLACE_CACHE: dict[int, tuple[int, list[str], dict]] = {}


def alert_places(c, r) -> list[str]:
    """Municipalities an alert's posts talk about (up to 3). A municipality counts when it is named in at least
    2 posts and 5% of the posts. Cached until the narrative grows."""
    size = (jloads(r["stats"], {}) or {}).get("size", 0)
    hit = _PLACE_CACHE.get(r["id"])
    if hit and hit[0] == size:
        return hit[1]
    M = municipalities()
    cnt: Counter = Counter()
    for p in c.execute("SELECT text FROM posts WHERE narrative_id=?", (r["narrative_id"],)):
        cnt.update(M.find(p["text"] or ""))
    need = max(2, 0.05 * size)
    places = [m for m, n in cnt.most_common(3) if n >= need]
    _PLACE_CACHE[r["id"]] = (size, places, dict(cnt))
    return places


def place_counts(r) -> dict:
    hit = _PLACE_CACHE.get(r["id"])
    return hit[2] if hit else {}


# ------------------------------------------------------------------ list filters (used by /api/alerts)
def matches_filter(c, r, level: str | None, category: str | None, place: str | None, since: float | None) -> bool:
    if level and r["severity"] != level:
        return False
    if category and category not in _categories(r):
        return False
    if since is not None and (jloads(r["stats"], {}) or {}).get("last_ts", 0) < since:
        return False
    if place:
        pl = alert_places(c, r)
        if (place == "none" and pl) or (place != "none" and place not in pl):
            return False
    return True


# ------------------------------------------------------------------ overview
def _trend(growth: float) -> tuple[str, str]:
    if growth >= 3:
        return "Growing fast", "up2"
    if growth >= 1.5:
        return "Growing", "up"
    if growth >= 0.5:
        return "Stable", "flat"
    return "Slowing", "down"


def _target_name(t: dict) -> str | None:
    m = re.match(r"Targets (.+?) \(weight", (t.get("factors", {}).get("target", {}) or {}).get("reason", "") or "")
    if not m:
        return None
    name = m.group(1).split(" (")[0].strip()
    return name if name.isupper() else f"the {name[0].lower()}{name[1:]}"


def _banner(c, top, sig: dict) -> dict:
    """One sentence for the highest current level, built from the detected signals only."""
    t = _threat(top)
    target = _target_name(t)
    about = f" against {target}" if target else ""
    if "hybrid" in sig:
        links = links_for_alert(c, top["id"])
        kind = KIND_LABEL.get(links[0]["kind"], "cyber event").lower() if links else "cyber event"
        tags = jloads(top["tags"], []) or []
        story = next((f"{x} narrative" for x in STORY_WORDS if x in tags), "disinformation narrative")
        text = f"suspected hybrid threat{about} ({kind} + {story})"
    elif "coordination" in sig:
        co = sig["coordination"]
        text = f"suspected coordinated campaign{about} ({co.get('accounts', '?')} accounts posting near-identical text)"
    elif "debunk_match" in sig:
        text = f"claim already rated false by fact-checkers is spreading{about}"
    elif "trending" in sig:
        text = f"fast-growing story{about} ({sig['trending'].get('growth', 0):g}x the normal rate)"
    else:
        text = f"story{about} on the watch list"
    return {"level": top["severity"], "text": text[0].upper() + text[1:], "alert_id": top["id"], "score": top["score"]}


@router.get("/api/dashboard")
def dashboard():
    c = db()
    now = _now(c)
    since = now - DAY
    rows = c.execute(ALERT_SQL + f"WHERE a.status IN ({','.join('?' * len(ACTIVE))}) AND a.severity IN "
                     f"('watch','threat','critical')", ACTIVE).fetchall()
    active = [r for r in rows if (jloads(r["stats"], {}) or {}).get("last_ts", 0) >= since]
    sigs = {r["id"]: jloads(r["signals"], {}) for r in rows}

    # (a) banner
    top = max(active, key=lambda r: (SEV_RANK.get(r["severity"], 0), r["score"] or 0)) if active else None
    banner = _banner(c, top, sigs[top["id"]]) if top else {
        "level": "info", "text": "No alert at Watch level or above in the last 24 hours. Monitoring continues.",
        "alert_id": None, "score": None}

    # (b) counters
    cy = Counter(r[0] for r in c.execute("SELECT status FROM cyber_events WHERE claimed_at>=? AND first_seen<=?",
                                         (since, now)))
    lv = Counter(r["severity"] for r in active)
    counters = {"critical": lv["critical"], "threat": lv["threat"], "watch": lv["watch"],
                "cyber_confirmed": cy["confirmed"], "cyber_refuted": cy["refuted"]}

    # (c) map + (d) matrix + (f) top 5
    M = municipalities()
    per_place: dict[str, dict] = {}
    unlocated, matrix, items = [], {k: {l: 0 for l in LEVELS} for k in CATEGORIES}, []
    for r in active:
        sig = sigs[r["id"]]
        st = jloads(r["stats"], {}) or {}
        title = _title(c, r, sig)[0]
        places = alert_places(c, r)
        cats = _categories(r)
        word, arrow = _trend(st.get("growth", 0) or 0)
        item = {"id": r["id"], "title": title, "level": r["severity"], "score": r["score"], "categories": cats,
                "places": places, "trend": word, "trend_dir": arrow, "growth": st.get("growth", 0),
                "posts": st.get("size", 0), "updated": r["bumped_sim"], "hybrid": "hybrid" in sig}
        items.append(item)
        for k in cats:
            matrix[k][r["severity"]] += 1
        if not places:
            unlocated.append(item)
        for p in places:
            d = per_place.setdefault(p, {"id": p, "name": M.label(p), "count": 0, "level": "watch", "alerts": [],
                                          "mentions": 0})
            d["count"] += 1
            d["mentions"] += place_counts(r).get(p, 0)
            if SEV_RANK[r["severity"]] > SEV_RANK[d["level"]]:
                d["level"] = r["severity"]
            d["alerts"].append({"id": r["id"], "title": title, "level": r["severity"]})
    for d in per_place.values():
        d["alerts"].sort(key=lambda a: -SEV_RANK[a["level"]])
    items.sort(key=lambda a: (-(a["score"] or 0), -SEV_RANK[a["level"]]))
    unlocated.sort(key=lambda a: (-SEV_RANK[a["level"]], -(a["score"] or 0)))

    return {"now": now, "since": since, "banner": banner, "counters": counters,
            "map": {"places": list(per_place.values()), "unlocated": unlocated, "total": len(active)},
            "matrix": matrix, "timeline": _week(c, now, rows), "top": items[:5]}


def _week(c, now: float, rows) -> dict:
    """7 days: posts per day in detected events by category, critical-alert and hybrid-link markers."""
    day1 = int(now // DAY) * DAY
    t0 = day1 - 6 * DAY
    days = {t0 + i * DAY: {k: 0 for k in POST_CATEGORIES} for i in range(7)}
    for cat, d, n in c.execute(
            "SELECT e.category, CAST(p.ts / 86400 AS INTEGER) * 86400 AS d, COUNT(DISTINCT p.id) "
            "FROM events e, json_each(e.post_ids) j JOIN posts p ON p.id = j.value "
            "WHERE e.last_ts >= ? AND e.first_ts <= ? AND p.ts >= ? AND p.ts <= ? GROUP BY 1, 2",
            (t0, now, t0, now)):
        if d in days and cat in days[d]:
            days[d][cat] += n
    markers = []
    for r in rows:
        if r["severity"] != "critical":
            continue
        hist = jloads(r["history"], []) or []
        t = next((h["sim"] for h in hist if "critical" in h.get("event", "") and h.get("by") != "analyst"),
                 None) or r["created_sim"]
        if t and t0 <= t <= now:
            markers.append({"kind": "critical", "t": t, "alert_id": r["id"],
                            "label": f"Alert #{r['id']} reached Critical: {_title(c, r, jloads(r['signals'], {}))[0]}"})
    for h in c.execute("SELECT h.alert_id, h.reason, e.id, e.claimed_at, e.title, e.status FROM hybrid_links h "
                       "JOIN cyber_events e ON e.id=h.cyber_event_id WHERE e.status!='refuted'"):
        if h["claimed_at"] and t0 <= h["claimed_at"] <= now:
            markers.append({"kind": "hybrid", "t": h["claimed_at"], "alert_id": h["alert_id"], "cyber_id": h[2],
                            "label": f"Hybrid link: cyber #{h[2]} ({h['title']}, {h['status'].replace('_', ' ')}) "
                                     f"and alert #{h['alert_id']}"})
    markers.sort(key=lambda m: m["t"])
    return {"start": t0, "end": day1 + DAY, "now": now, "categories": list(POST_CATEGORIES),
            "days": [{"t": t, **v} for t, v in sorted(days.items())], "markers": markers}


# ------------------------------------------------------------------ alert: network graph
@router.get("/api/alerts/{aid}/network")
def alert_network(aid: int, show_all: int = Query(0, alias="all")):
    """Accounts in this alert's story, linked when they posted near-identical text (same copy-paste group).
    Capped at 150 nodes unless all=1 (hard cap 2000): accounts in copy-paste groups first, then the most active."""
    c = db()
    r = c.execute(ALERT_SQL + "WHERE a.id=?", (aid,)).fetchone()
    if not r:
        raise HTTPException(404)
    nid = r["narrative_id"]
    posts = c.execute("SELECT author, author_created, followers, ts, dup_group FROM posts WHERE narrative_id=? "
                      "ORDER BY ts", (nid,)).fetchall() if nid is not None else []
    acc: dict[str, dict] = {}
    groups: dict[int, list[str]] = defaultdict(list)
    for p in posts:
        a = acc.setdefault(p["author"], {"id": p["author"], "posts": 0, "created": p["author_created"],
                                         "first": p["ts"], "followers": p["followers"] or 0, "copy": 0})
        a["posts"] += 1
        a["followers"] = max(a["followers"], p["followers"] or 0)
        if p["dup_group"] is not None and p["author"] not in groups[p["dup_group"]]:
            groups[p["dup_group"]].append(p["author"])      # in order of first post in the group
    linked = {g: authors for g, authors in groups.items() if len(authors) >= 2}
    for authors in linked.values():
        for a in authors:
            acc[a]["copy"] += 1
    cap = 2000 if show_all else 150
    order = sorted(acc.values(), key=lambda a: (-(a["copy"] > 0), -a["copy"], -a["posts"], a["first"]))
    shown = {a["id"] for a in order[:cap]}
    # edges: each account to the next 3 that posted the same text after it (a full clique of 50 accounts
    # would be 1,225 lines and hide the structure; this keeps every copy-paste group connected)
    edges: Counter = Counter()
    for authors in linked.values():
        authors = [a for a in authors if a in shown]
        for i, a in enumerate(authors):
            for b in authors[i + 1:i + 4]:
                edges[tuple(sorted((a, b)))] += 1
    # other alerts whose coordinated accounts include these accounts
    sig = jloads(r["signals"], {})
    others: dict[str, list[int]] = defaultdict(list)
    other_alerts = {}
    for o in c.execute(ALERT_SQL + "WHERE a.id!=? AND a.status!='merged' AND a.signals LIKE '%coordination%'",
                       (aid,)).fetchall():
        shared = _network_accounts(c, o["narrative_id"], jloads(o["signals"], {})) & shown
        if shared:
            other_alerts[o["id"]] = {"id": o["id"], "title": _title(c, o, jloads(o["signals"], {}))[0],
                                     "level": o["severity"], "shared": len(shared)}
            for a in shared:
                others[a].append(o["id"])
    nodes = [dict(a, age_days=round((a["first"] - a["created"]) / DAY) if a["created"] else None,
                  also_in=others.get(a["id"], [])) for a in order[:cap]]
    coord = _network_accounts(c, nid, sig) if nid is not None else set()
    for n in nodes:
        n["coordinated"] = n["id"] in coord
    return {"nodes": nodes, "edges": [{"a": a, "b": b, "w": w} for (a, b), w in edges.items()],
            "total": len(acc), "shown": len(nodes), "capped": len(acc) > len(nodes), "groups": len(linked),
            "other_alerts": sorted(other_alerts.values(), key=lambda x: -x["shared"])}


# ------------------------------------------------------------------ alert: spread timeline + splits
def spread(c, r, nid: int, sig: dict, stats: dict, history: list) -> dict:
    """Posts per 30 min by language, copy-paste per bucket, and markers."""
    bucket = 1800
    ps = c.execute("SELECT ts, lang, text, dup_group, source_type FROM posts WHERE narrative_id=?", (nid,)).fetchall()
    if not ps:
        return {"bars": [], "markers": [], "langs": {}, "platforms": {}}
    groups = _coord_groups(sig)
    bars: dict[int, Counter] = defaultdict(Counter)
    langs, platforms = Counter(), Counter()
    for p in ps:
        b = int(p["ts"] // bucket * bucket)
        lk = _lang_key(p["lang"], p["text"])
        bars[b][lk] += 1
        bars[b]["_copy"] += p["dup_group"] in groups
        langs[lk] += 1
        platforms[p["source_type"] or "other"] += 1
    markers = [{"kind": "flagged", "label": "Flagged", "t": r["created_sim"]}]
    coord_hist = [h["sim"] for h in history if "coordination" in h.get("event", "") and h.get("by") != "analyst"]
    if coord_hist and abs(coord_hist[0] - r["created_sim"]) > bucket:
        markers.append({"kind": "coordination", "label": "Coordination detected", "t": coord_hist[0]})
    if stats.get("first_credible_ts"):
        markers.append({"kind": "credible", "label": "First credible report", "t": stats["first_credible_ts"]})
    for l in links_for_alert(c, r["id"]):
        markers.append({"kind": "cyber", "label": f"Cyber #{l['event_id']} claimed", "t": l["claimed_at"],
                        "detail": f"{l['title']} ({l['status'].replace('_', ' ')})"})
    for h in history:
        if h.get("by") == "analyst" and h.get("sim"):
            markers.append({"kind": "analyst", "label": h["event"].split(" — ")[0], "t": h["sim"]})
    ts = [p["ts"] for p in ps] + [m["t"] for m in markers if m["t"]]
    start, end = int(min(ts) // bucket * bucket), int(max(ts) // bucket * bucket) + bucket
    burst_min = max(3, int(CFG.get("thresholds", {}).get("coord_min_accounts", 5)) // 2)
    keys = [k for k in LANG_NAME if langs[k]]
    return {"start": start, "end": end, "bucket": bucket, "langs_order": keys,
            "lang_names": {k: LANG_NAME[k] for k in keys}, "burst_min": burst_min,
            "bars": [{"t": t, **{k: v[k] for k in keys}, "copy": v["_copy"], "burst": v["_copy"] >= burst_min}
                     for t, v in sorted(bars.items())],
            "markers": sorted([m for m in markers if m["t"]], key=lambda m: m["t"]),
            "split": {"languages": [{"key": k, "name": LANG_NAME[k], "n": n} for k, n in langs.most_common()],
                      "platforms": [{"key": k, "name": PLATFORM_NAME.get(k, k.title()), "n": n}
                                    for k, n in platforms.most_common()]}}


def threat_with_weights(t: dict) -> dict:
    """Adds the configured weight and the points each factor contributes (score = sum of points)."""
    if not t or not t.get("factors"):
        return t or {}
    w = (CFG.get("threat_scoring") or {}).get("weights", {})
    t = dict(t)
    t["factors"] = {k: dict(f, weight=w.get(k), points=round(100 * w.get(k, 0) * (f.get("value", 1) - 1) / 4, 1))
                    for k, f in t["factors"].items()}
    t["thresholds"] = (CFG.get("threat_scoring") or {}).get("levels", {})
    return t


# ------------------------------------------------------------------ cyber: availability parsing
_SECS = re.compile(r"(\d+(?:\.\d+)?)\s?s\b")
_HTTP = re.compile(r"HTTP (\d{3})")
_DROP = re.compile(r"(?:dropped|-)\s?(\d+(?:\.\d+)?)%")


def _host(ev_raw: dict, url: str | None) -> str | None:
    if url:
        h = urlparse(url).netloc
        if h:
            return h.lower()
    d = ev_raw.get("domain")
    return d.lower() if d else None


def _avail_point(ch) -> dict:
    d = ch["detail"] or ""
    down = "not reachable" in d.lower() or ch["result"] == "supports"
    m, h = _SECS.search(d), _HTTP.search(d)
    secs = float(m.group(1)) if m else None
    state = "down" if down else ("slow" if secs is not None and secs >= SLOW_SECONDS else "up")
    return {"t": ch["checked_at"], "state": state, "secs": None if down else secs, "http": int(h.group(1)) if h else None,
            "detail": d, "event_id": ch["event_id"]}


def _availability(c, now: float) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    raws = {}
    for ch in c.execute("SELECT v.*, e.raw FROM verification_checks v JOIN cyber_events e ON e.id=v.event_id "
                        "WHERE v.check_name='availability' AND v.checked_at<=? ORDER BY v.checked_at", (now,)):
        raw = raws.setdefault(ch["event_id"], jloads(ch["raw"], {}) or {})
        h = _host(raw, ch["evidence_url"])
        if h:
            out[h].append(_avail_point(ch))
    return out


@router.get("/api/cyber/sites")
def cyber_sites():
    """Every site the verifier has checked (one GET of the public homepage, never a scan): state of the latest
    check, uptime and response times over the last 24 hours (data time)."""
    c = db()
    now = _now(c)
    since = now - DAY
    avail = _availability(c, now)
    targets = {}
    for e in c.execute("SELECT id, target, raw, title FROM cyber_events WHERE first_seen<=?", (now,)):
        h = _host(jloads(e["raw"], {}) or {}, None)
        if h:
            targets.setdefault(h, {"target": e["target"], "events": []})["events"].append(e["id"])
    sites = []
    for h in sorted(set(avail) | set(targets)):
        pts = avail.get(h, [])
        last = pts[-1] if pts else None
        day = [p for p in pts if p["t"] >= since]
        ok = sum(p["state"] != "down" for p in day)
        sites.append({"host": h, "target": (targets.get(h) or {}).get("target"),
                      "events": sorted(set((targets.get(h) or {}).get("events", []) + [p["event_id"] for p in pts])),
                      "state": last["state"] if last else "unknown", "last": last,
                      "stale": not last or last["t"] < since, "checks_24h": len(day),
                      "uptime_24h": round(100 * ok / len(day)) if day else None, "points": day})
    sites.sort(key=lambda s: ({"down": 0, "slow": 1, "up": 2}.get(s["state"], 3), s["stale"], s["host"]))
    return {"now": now, "since": since, "slow_seconds": SLOW_SECONDS, "sites": sites,
            "live_checks": bool((CFG.get("cyber") or {}).get("live_checks"))}


def reality(c, ev, checks: list[dict]) -> dict:
    """Claim vs reality for one cyber event: the site's response time / errors and the IODA signal over time,
    with the claimed attack time. The visual evidence behind "Confirmed" or "Refuted"."""
    now = _now(c)
    raw = jloads(ev["raw"], {}) or {}
    host = _host(raw, next((x["evidence_url"] for x in checks if x["check_name"] == "availability"), None))
    claimed = ev["claimed_at"] or ev["first_seen"]
    own = [x for x in checks if x["check_name"] in ("availability", "ioda")]
    ts = [claimed] + [x["checked_at"] for x in own] + ([ev["verdict_at"]] if ev["verdict_at"] else [])
    t0, t1 = min(ts) - 3600, max(ts) + 3600
    t1 = max(t1, min(claimed + 6 * 3600, now))
    site = [p for p in _availability(c, now).get(host, []) if t0 <= p["t"] <= t1] if host else []
    ioda = []
    for x in checks:
        if x["check_name"] != "ioda":
            continue
        m = _DROP.search(x["detail"] or "")
        val = round(100 - float(m.group(1)), 1) if m else (100.0 if x["result"] in ("contradicts", "inconclusive")
                                                            and "no " in (x["detail"] or "").lower() else None)
        ioda.append({"t": x["checked_at"], "value": val, "result": x["result"], "detail": x["detail"]})
    return {"start": t0, "end": t1, "host": host, "claimed_at": claimed, "first_seen": ev["first_seen"],
            "verdict_at": ev["verdict_at"], "status": ev["status"], "site": site, "ioda": ioda,
            "slow_seconds": SLOW_SECONDS, "timeout_seconds": (CFG.get("cyber") or {}).get("http_timeout", 10)}
