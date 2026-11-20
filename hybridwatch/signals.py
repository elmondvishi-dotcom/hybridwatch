"""Per-narrative signals: HOW a narrative spreads, not whether it is true.

  trending      - volume jumped well above its recent baseline (awareness only)
  coordination  - many distinct accounts posted near-identical text in a short window
  new_accounts  - the accounts behind the copy-paste burst are mostly brand new
  no_credible_first - no credible outlet carried the story before the burst
  debunk_match  - several posts repeat a claim a fact-checker already rated false
"""
from __future__ import annotations

import math
from bisect import bisect_right
import sqlite3
from collections import Counter, defaultdict

DAY = 86400.0


def narrative_stats(conn: sqlite3.Connection, nid: int, now: float, th: dict) -> dict:
    rows = conn.execute(
        "SELECT id, author, author_created, followers, ts, dup_group, credibility, source_type, lang, "
        "debunk_id, debunk_score FROM posts WHERE narrative_id=? AND ts>=? ORDER BY ts",
        (nid, now - 3 * DAY)).fetchall()
    st: dict = {"size": len(rows)}
    if not rows:
        return st

    st["first_ts"] = rows[0]["ts"]
    st["last_ts"] = rows[-1]["ts"]
    st["accounts"] = len({r["author"] for r in rows})
    st["reach"] = int(sum(r["followers"] or 0 for r in rows))
    st["langs"] = dict(Counter(r["lang"] for r in rows))
    st["source_types"] = dict(Counter(r["source_type"] for r in rows))

    # --- trending: recent window vs baseline rate ---
    w = th["trend_window_minutes"] * 60
    b = th["trend_baseline_hours"] * 3600
    ts = [r["ts"] for r in rows]

    def count(lo: float, hi: float) -> int:  # posts with lo < ts <= hi (ts is sorted)
        return bisect_right(ts, hi) - bisect_right(ts, lo)

    def growth_at(end: float) -> tuple[int, float]:
        recent = count(end - w, end)
        base = count(end - w - b, end - w)
        return recent, recent / max(base / (b / w), 1.0)

    st["recent"], g = growth_at(now)
    st["growth"] = round(g, 2)
    # peak growth at any moment in the lookback (so spikes inside a large batch / backlog are not missed)
    look = th.get("trend_lookback_hours", 6) * 3600
    peak = (st["recent"], g)
    for end in ts:
        if end > now - look:
            rc, gr = growth_at(end)
            if rc >= th["trend_min_posts"] and gr > peak[1]:
                peak = (rc, gr)
    st["peak_recent"], st["peak_growth"] = peak[0], round(peak[1], 2)

    # --- coordination: copy-paste groups with many accounts in a short window ---
    win = th["coord_window_minutes"] * 60
    groups: dict[int, list] = defaultdict(list)
    for r in rows:
        groups[r["dup_group"]].append(r)
    qualifying: list[list] = []
    for gid, grp in groups.items():
        if len(grp) < th["coord_min_accounts"]:
            continue
        # sliding window: max distinct accounts within `win` seconds
        lo, peak = 0, 0
        for hi in range(len(grp)):
            while grp[hi]["ts"] - grp[lo]["ts"] > win:
                lo += 1
            peak = max(peak, len({g["author"] for g in grp[lo:hi + 1]}))
        if peak >= th["coord_min_accounts"]:
            qualifying.append(grp)
    # combine all copy-paste groups (e.g. the Albanian, Serbian and English version of one template)
    best = {"accounts": 0}
    coord_post_ids: list[int] = []
    coord_authors: set[str] = set()
    if qualifying:
        allp = sorted((p for g in qualifying for p in g), key=lambda p: p["ts"])
        coord_post_ids = [p["id"] for p in allp]
        coord_authors = {p["author"] for p in allp}
        lo = 0
        for hi in range(len(allp)):
            while allp[hi]["ts"] - allp[lo]["ts"] > win:
                lo += 1
            accts = {p["author"] for p in allp[lo:hi + 1]}
            if len(accts) > best["accounts"]:
                best = {"accounts": len(accts), "start": allp[lo]["ts"], "versions": len(qualifying),
                        "minutes": round((allp[hi]["ts"] - allp[lo]["ts"]) / 60, 1),
                        "group": max(qualifying, key=len)[0]["dup_group"],
                        "groups": [g[0]["dup_group"] for g in qualifying]}
    st["coord"] = best
    st["coord_post_ids"] = coord_post_ids
    st["coord_authors"] = sorted(coord_authors)

    if coord_authors:
        info = {}
        for r in rows:
            if r["author"] in coord_authors:
                info[r["author"]] = r
        new = sum(1 for a, r in info.items()
                  if r["author_created"] and (r["ts"] - r["author_created"]) < th["new_account_days"] * DAY)
        st["new_account_share"] = round(new / len(info), 2)
        st["new_accounts"] = new
        st["coord_accounts"] = len(info)
    # credible outlet before the burst?
    cred = [r["ts"] for r in rows if r["credibility"] == "credible"]
    st["first_credible_ts"] = cred[0] if cred else None

    # --- debunk matches ---
    deb = Counter(r["debunk_id"] for r in rows if r["debunk_id"])
    if deb:
        fid, n = deb.most_common(1)[0]
        st["debunk"] = {"factcheck_id": fid, "posts": n}
    return st


def evaluate_signals(st: dict, th: dict) -> dict:
    sig: dict = {}
    if st.get("size", 0) < th["min_narrative_size"]:
        return sig
    if st["peak_recent"] >= th["trend_min_posts"] and st["peak_growth"] >= th["trend_growth"]:
        sig["trending"] = {"recent": st["peak_recent"], "growth": st["peak_growth"]}
    c = st.get("coord", {})
    if c.get("accounts", 0) >= th["coord_min_accounts"]:
        sig["coordination"] = c
        if st.get("new_account_share", 0) >= 0.5:
            sig["new_accounts"] = {"share": st["new_account_share"], "count": st["new_accounts"],
                                   "of": st["coord_accounts"]}
        if st["first_credible_ts"] is None or st["first_credible_ts"] > c["start"]:
            sig["no_credible_first"] = {"first_credible_ts": st["first_credible_ts"]}
    d = st.get("debunk")
    if d and d["posts"] >= th["debunk_min_posts"]:
        sig["debunk_match"] = d
    return sig


def alert_type(sig: dict) -> str | None:
    if "coordination" in sig:
        return "coordination"
    if "debunk_match" in sig:
        return "debunk_match"
    if "trending" in sig:
        return "trending"
    return None


def confidence(sig: dict) -> float:
    if "coordination" in sig:
        c = 0.55
        c += 0.1 if "new_accounts" in sig else 0
        c += 0.1 if "no_credible_first" in sig else 0
        c += 0.1 if "debunk_match" in sig else 0
        c += 0.05 if sig["coordination"]["accounts"] >= 15 else 0
        return round(min(c, 0.95), 2)
    if "debunk_match" in sig:
        return round(min(0.5 + 0.05 * sig["debunk_match"]["posts"] / 3, 0.8), 2)
    if "trending" in sig:
        return 0.4
    return 0.0


def confidence_label(c: float) -> str:
    return "High" if c >= 0.75 else "Medium" if c >= 0.5 else "Low"


SEVERITY = ["low", "medium", "high", "critical"]


def severity(sig: dict, st: dict, sensitive_hits: int) -> str:
    score = 0.0
    score += {"coordination": 2, "debunk_match": 1.5, "trending": 0.5}.get(alert_type(sig) or "", 0)
    score += 1 if "debunk_match" in sig and "coordination" in sig else 0
    score += min(math.log10(max(st.get("reach", 1), 1)) - 3, 2) if st.get("reach", 0) > 1000 else 0
    score += min(sensitive_hits, 2) * 0.75
    if score >= 5:
        return "critical"
    if score >= 3.5:
        return "high"
    if score >= 2:
        return "medium"
    return "low"


def reasons(sig: dict, st: dict, fc: dict | None) -> list[str]:
    out = []
    if "coordination" in sig:
        c = sig["coordination"]
        v = c.get("versions", 1)
        out.append(f"{c['accounts']} accounts posted near-identical text within {c['minutes']:g} min"
                   + (f" ({v} versions, e.g. different languages)" if v > 1 else ""))
    if "new_accounts" in sig:
        n = sig["new_accounts"]
        out.append(f"{n['count']} of the {n.get('of', n['count'])} copy-paste accounts ({int(n['share'] * 100)}%) "
                   "were created in the last 30 days")
    if "no_credible_first" in sig:
        out.append("No credible outlet reported this before the burst" if sig["no_credible_first"]["first_credible_ts"]
                   is None else "Credible outlets only reported it after the burst")
    if "debunk_match" in sig:
        d = sig["debunk_match"]
        src = f"{fc['source']}" if fc else "a fact-checker"
        out.append(f"{d['posts']} posts repeat a claim rated '{fc['verdict'] if fc else 'false'}' by {src}")
    if "trending" in sig:
        t = sig["trending"]
        out.append(f"{t['recent']} posts within an hour, {t['growth']:g}x the normal rate")
    langs = st.get("langs", {})
    if len(langs) >= 2:
        out.append("Spreading in " + ", ".join(f"{k} ({v})" for k, v in sorted(langs.items(), key=lambda x: -x[1])))
    return out
