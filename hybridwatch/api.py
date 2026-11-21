"""Web API + static interface. Only READS what the worker produced (plus analyst decisions),
so closing the interface never stops the analysis.

    py -m uvicorn hybridwatch.api:app --port 8000
"""
from __future__ import annotations

import json
import re
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import load_config, resolve
from .db import connect, get_setting, jloads, set_setting
from .hybrid import KIND_LABEL, links_for_alert
from .text import english_part, shorten
from .verify import CHECK_LABELS, INDEPENDENT, TECHNICAL

CFG = load_config()
STATIC = Path(__file__).parent / "static"
# threat levels (threat.py); old severity names map rank for rank (databases from before the threat model)
SEV_RANK = {"critical": 3, "threat": 2, "watch": 1, "info": 0, "high": 2, "medium": 1, "low": 0}
# folder -> (statuses, level filter). Info-level cases are stored but kept out of the inbox.
FOLDERS = {"inbox": (("new",), "a.severity!='info'"), "info": (("new", "needs_review", "watching"), "a.severity='info'"),
           "review": (("needs_review",), "a.severity!='info'"), "confirmed": (("confirmed",), None),
           "dismissed": (("dismissed",), None), "watching": (("watching",), "a.severity!='info'"),
           # everything still in play at Watch or above (the Overview's counters, map and matrix open this)
           "active": (("new", "needs_review", "watching", "confirmed"), "a.severity!='info'")}


def _folder_where(folder: str) -> tuple[str, tuple]:
    st, lvl = FOLDERS.get(folder, FOLDERS["inbox"])
    return f"a.status IN ({','.join('?' * len(st))})" + (f" AND {lvl}" if lvl else ""), st

app = FastAPI(title="HybridWatch")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


_local = threading.local()


def db():
    # one connection per server thread (SQLite connections should not be shared across threads)
    if not hasattr(_local, "conn"):
        _local.conn = connect(resolve(CFG, CFG["db_path"]))
    return _local.conn


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


# ---------------------------------------------------------------- status
@app.get("/api/status")
def status():
    c = db()
    counts = {f: c.execute(f"SELECT COUNT(*) FROM alerts a WHERE {w}", args).fetchone()[0]
              for f, (w, args) in ((f, _folder_where(f)) for f in FOLDERS)}
    hb = get_setting(c, "heartbeat", 0) or 0
    return {
        "sim_now": get_setting(c, "sim_now", 0),
        "posts": c.execute("SELECT COUNT(*) FROM posts").fetchone()[0],
        "narratives": c.execute("SELECT COUNT(*) FROM narratives").fetchone()[0],
        "sources": len([s for s in (get_setting(c, "sources", []) or []) if s.get("enabled")]),
        "factchecks": c.execute("SELECT COUNT(*) FROM factchecks").fetchone()[0],
        "folders": counts,
        "worker_alive": time.time() - hb < 30,
        # sidebar badges: open cyber claims, critical events in the last 48 h (data time)
        "cyber_open": c.execute("SELECT COUNT(*) FROM cyber_events WHERE status IN ('unverified','partly_confirmed') "
                                "AND first_seen<=?", (get_setting(c, "sim_now", 0) or time.time(),)).fetchone()[0],
        "events_critical": c.execute("SELECT COUNT(*) FROM events WHERE severity='critical' AND last_ts>=?",
                                     ((get_setting(c, "sim_now", 0) or time.time()) - 48 * 3600,)).fetchone()[0],
        "llm": CFG.get("llm", {}).get("provider", "none"),
    }


# ---------------------------------------------------------------- alerts
ALERT_SQL = ("SELECT a.*, n.label, n.label_source, n.tags, n.rep_post_id, n.en_post_id FROM alerts a "
             "LEFT JOIN narratives n ON n.id=a.narrative_id ")
OPEN = ("new", "needs_review", "watching")
_english_part, _shorten = english_part, shorten   # shared with events.py (text.py)


def _factcheck(c, sig: dict):
    if "debunk_match" not in sig:
        return None
    fc = c.execute("SELECT source, claim, verdict, url, date, kind FROM factchecks WHERE id=?",
                   (sig["debunk_match"]["factcheck_id"],)).fetchone()
    return dict(fc) if fc else None


def _title(c, r, sig: dict) -> tuple[str, str]:
    """Plain-English title: LLM label > fact-check claim (English part) > central English post > heuristic."""
    if r["label_source"] == "llm" and r["label"]:
        return r["label"], "llm"
    fc = _factcheck(c, sig)
    if fc and fc["kind"] != "analyst":
        en = _english_part(fc["claim"])
        if en:
            return en, "factcheck"
    if r["en_post_id"]:
        p = c.execute("SELECT text FROM posts WHERE id=?", (r["en_post_id"],)).fetchone()
        if p:
            return _shorten(p["text"]), "post"
    return r["label"] or "(labelling…)", "heuristic"


def _row_summary(r, c) -> dict:
    stats = jloads(r["stats"], {})
    seen = jloads(r["seen_snapshot"])
    new_posts = max(0, stats.get("size", 0) - seen["posts"]) if seen and r["unread"] else 0
    title, title_source = _title(c, r, jloads(r["signals"], {}))
    return {"id": r["id"], "status": r["status"], "type": r["type"], "severity": r["severity"],
            "confidence": r["confidence"], "title": title, "title_source": title_source,
            "tags": jloads(r["tags"], []) or [], "label": r["label"] or "(labelling…)", "posts": stats.get("size", 0),
            "growth": stats.get("growth", 0), "accounts": stats.get("accounts", 0),
            "langs": stats.get("langs", {}), "unread": bool(r["unread"]), "bumped_at": r["bumped_at"],
            "bumped_sim": r["bumped_sim"], "created_sim": r["created_sim"],
            "previously_dismissed": bool(r["previously_dismissed"]), "new_posts": new_posts,
            "score": r["score"], "categories": (jloads(r["threat"], {}) or {}).get("categories", [])}


CY_STATUS_WORD = {"confirmed": "a confirmed", "partly_confirmed": "a partly confirmed", "unverified": "an unverified"}


def _summary(sig: dict, fc: dict | None, links: list | None = None) -> str:
    """Plain-language verdict. For a hybrid threat, says which cyber event it follows and when."""
    base = _summary_base(sig, fc)
    if not links:
        return base
    ln = links[0]
    m = re.search(r"\(([^()]*) (later|earlier)\)\.?$", ln["reason"] or "")
    when = f"{m.group(1)} {'after' if m.group(2) == 'later' else 'before'}" if m else "close in time to"
    same = "the same target" if (ln["reason"] or "").startswith("Same target") else "the same sector"
    return (f"Hybrid threat. {base} It started {when} {CY_STATUS_WORD.get(ln['status'], 'a')} cyber event on "
            f"{same}: {ln['title']}.")


def _summary_base(sig: dict, fc: dict | None) -> str:
    """One plain-language sentence built only from detected signals. Never says the claim is true or
    false: a rating is always attributed to the fact-checker."""
    rated = None
    if "debunk_match" in sig:
        if fc and fc.get("kind") == "analyst":
            rated = "your team already confirmed this claim as disinformation"
        else:
            rated = f"fact-checkers already rated this claim {fc['verdict'] if fc else 'False'}"
    if "coordination" in sig:
        co = sig["coordination"]
        out = (f"Likely coordinated campaign. {co['accounts']} accounts posted the same message "
               f"within {max(1, round(co.get('minutes', 0)))} minutes.")
        extra = []
        if "new_accounts" in sig:
            extra.append(f"{int(sig['new_accounts']['share'] * 100)}% of the accounts are newly created")
        if rated:
            extra.append(rated)
        if extra:
            s = ", and ".join(extra)
            out += " " + s[0].upper() + s[1:] + "."
        return out
    if "debunk_match" in sig:
        if fc and fc.get("kind") == "analyst":
            out = "Repeats a claim your team already confirmed as disinformation."
        else:
            out = f"Repeats a claim fact-checkers rated {fc['verdict'] if fc else 'False'}."
        out += f" {sig['debunk_match']['posts']} posts repeat it."
        if "trending" in sig:
            out += f" Volume is {sig['trending']['growth']:g}x its normal rate."
        return out
    if "trending" in sig:
        t = sig["trending"]
        return (f"Fast-growing story. No sign of coordination. {t['recent']} posts within an hour, "
                f"{t['growth']:g}x the normal rate.")
    return ""


def _timeline(c, r, nid: int, sig: dict, stats: dict, history: list) -> dict:
    """Posts over the narrative's lifetime (min. 12 h), with 'Flagged' / 'First credible report' markers
    and the early-warning lead time between them."""
    lo, hi = c.execute("SELECT MIN(ts), MAX(ts) FROM posts WHERE narrative_id=?", (nid,)).fetchone()
    if lo is None:
        return {}
    flagged = r["created_sim"]
    coord_hist = [h["sim"] for h in history if "coordination" in h.get("event", "") and h.get("by") != "analyst"]
    coord_at = coord_hist[0] if coord_hist else (flagged if "coordination" in sig else None)
    cred = stats.get("first_credible_ts")
    now = get_setting(c, "sim_now", 0) or hi
    marks = [m for m in (flagged, coord_at, cred) if m]
    start, end = min([lo, *marks]), max([hi, *marks])
    span_min = 12 * 3600
    if end - start < span_min:
        end = max(end, min(start + span_min, now))
        start = min(start, end - span_min)
    bucket = max(1800, int((end - start) / 60 // 1800 + 1) * 1800)   # at most ~60 bars
    start = start // bucket * bucket
    bars = defaultdict(lambda: [0, 0])
    groups = _coord_groups(sig)
    for p in c.execute("SELECT ts, dup_group FROM posts WHERE narrative_id=?", (nid,)):
        b = int(p["ts"] // bucket * bucket)
        bars[b][0] += 1
        bars[b][1] += p["dup_group"] in groups
    markers = [{"kind": "flagged", "label": "Flagged", "t": flagged}]
    if coord_at and abs(coord_at - flagged) > bucket:
        markers.append({"kind": "coordination", "label": "Coordination detected", "t": coord_at})
    if cred:
        markers.append({"kind": "credible", "label": "First credible report", "t": cred})
    alert_at = coord_at or flagged
    lead = None
    if cred and alert_at and alert_at < cred:
        lead = {"seconds": cred - alert_at, "from": alert_at, "to": cred,
                "what": "Coordination detected" if coord_at and coord_at != flagged else "Flagged"}
    return {"start": start, "end": end, "bucket": bucket, "markers": markers, "lead": lead,
            "bars": [{"t": t, "posts": v[0], "copy_paste": v[1]} for t, v in sorted(bars.items())]}


def _coord_groups(sig: dict) -> set:
    co = sig.get("coordination", {})
    return set(co.get("groups") or [co.get("group")]) - {None}


def _network_accounts(c, nid: int, sig: dict) -> set:
    """Accounts that took part in this narrative's copy-paste bursts (not organic posters)."""
    groups = list(_coord_groups(sig))
    if not groups:
        return set()
    return {r[0] for r in c.execute(
        f"SELECT DISTINCT author FROM posts WHERE narrative_id=? AND dup_group IN ({','.join('?' * len(groups))})",
        (nid, *groups))}


def _linked(a: set, b: set) -> bool:
    """Two coordinated account sets belong to the same network."""
    shared = len(a & b)
    return shared >= 3 and shared / min(len(a), len(b)) >= 0.2


def _related(c, aid: int, nid: int, sig: dict) -> tuple[list, dict]:
    """Other alerts pushed by the same coordinated accounts.
    Returns (related alerts, {author: [alert ids]})."""
    mine = _network_accounts(c, nid, sig)
    if len(mine) < 3:
        return [], {}
    related, seen_in = [], defaultdict(list)
    for r in c.execute(ALERT_SQL + "WHERE a.id!=? AND a.status!='merged' AND a.signals LIKE '%coordination%'",
                       (aid,)).fetchall():
        sig_r = jloads(r["signals"], {})
        theirs = _network_accounts(c, r["narrative_id"], sig_r)
        shared = mine & theirs
        if _linked(mine, theirs):
            related.append({"id": r["id"], "title": _title(c, r, sig_r)[0], "status": r["status"],
                            "severity": r["severity"], "shared": len(shared), "of": len(mine),
                            "share": round(len(shared) / len(mine), 2)})
            for a in shared:
                seen_in[a].append(r["id"])
    related.sort(key=lambda x: -x["shared"])
    return related, dict(seen_in)


def _changes_since(prev: dict | None, now: dict, history: list) -> dict | None:
    """What changed between the analyst's last look and now (None = nothing / first look)."""
    if not prev:
        return None
    sev_up = SEV_RANK.get(now["severity"], 0) > SEV_RANK.get(prev.get("severity"), 0)
    out = {
        "since_sim": prev.get("sim"),
        "posts": now["posts"] - prev.get("posts", 0),
        "accounts": now["accounts"] - prev.get("accounts", 0),
        "new_signals": [s for s in now["signals"] if s not in prev.get("signals", [])],
        "severity": [prev.get("severity"), now["severity"]] if sev_up else None,
        "events": [h["event"] for h in history if h.get("t", 0) > prev.get("at", 0) and h.get("by") != "analyst"],
    }
    if out["posts"] <= 0 and not out["new_signals"] and not out["severity"] and not out["events"]:
        return None
    return out


@app.get("/api/alerts")
def list_alerts(folder: str = "inbox", snapshot: float | None = None, level: str | None = None,
                category: str | None = None, place: str | None = None, hours: float | None = None):
    """snapshot = wall time the analyst paused at. Alerts updated after it are counted, not shown
    (except critical ones, which always break through).
    level / category / place (municipality id, or 'none' = not located) / hours (active in the last N hours):
    filters set by clicking the Overview."""
    c = db()
    where, args = _folder_where(folder)
    rows = c.execute(ALERT_SQL + "WHERE " + where, args).fetchall()
    if level or category or place or hours:
        since = _now(c) - hours * 3600 if hours else None
        rows = [r for r in rows if viz.matches_filter(c, r, level, category, place, since)]
    shown, pending = [], 0
    for r in rows:
        if snapshot and r["bumped_at"] > snapshot and r["severity"] != "critical":
            pending += 1
            continue
        shown.append(_row_summary(r, c))
    shown.sort(key=lambda a: (-SEV_RANK.get(a["severity"], 0), -(a["score"] or 0), -a["bumped_at"]))
    return {"alerts": shown, "pending": pending}


@app.get("/api/overview")
def overview():
    """What the right pane shows when no alert is selected."""
    c = db()
    rows = c.execute(ALERT_SQL + f"WHERE a.status IN ({','.join('?' * len(OPEN))})", OPEN).fetchall()
    sigs = {r["id"]: jloads(r["signals"], {}) for r in rows}
    coord = [r for r in rows if "coordination" in sigs[r["id"]]]
    # linked networks: open coordinated alerts that share accounts, grouped into connected components
    nets = {r["id"]: _network_accounts(c, r["narrative_id"], sigs[r["id"]]) for r in coord}
    parent = {i: i for i in nets}

    def root(i):
        while parent[i] != i:
            i = parent[i]
        return i
    ids = list(nets)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if _linked(nets[a], nets[b]):
                parent[root(a)] = root(b)
    groups = defaultdict(list)
    for i in ids:
        groups[root(i)].append(i)
    networks = [sorted(g) for g in groups.values() if len(g) >= 2]

    def rank(r):
        return (r["status"] == "new", SEV_RANK.get(r["severity"], 0), r["bumped_at"] or 0)
    top = max(rows, key=rank) if rows else None
    latest = max(rows, key=lambda r: r["created_sim"] or 0) if rows else None
    brief = lambda r: {"id": r["id"], "title": _title(c, r, sigs[r["id"]])[0], "severity": r["severity"],
                       "t": r["created_sim"]}
    # last 24 h (data time): cyber claims by verdict, campaigns, hybrid threats
    now = get_setting(c, "sim_now", 0) or time.time()
    since = now - 24 * 3600
    cy = Counter(r[0] for r in c.execute("SELECT status FROM cyber_events WHERE claimed_at>=? AND first_seen<=?",
                                         (since, now)))
    hybrid = [r for r in rows if "hybrid" in sigs[r["id"]]]
    return {
        "open": len(rows),
        "critical": sum(r["severity"] == "critical" for r in rows),
        "threat": sum(r["severity"] == "threat" for r in rows),
        "coordinated": len(coord),
        "linked_alerts": sum(len(g) for g in networks),
        "networks": networks,
        "latest": brief(latest) if latest else None,
        "top": brief(top) if top else None,
        "last24h": {"confirmed_incidents": cy["confirmed"], "open_claims": cy["unverified"] + cy["partly_confirmed"],
                    "refuted_claims": cy["refuted"], "campaigns": len(coord), "hybrid": len(hybrid)},
        "hybrid": [brief(r) for r in hybrid],
    }


@app.get("/api/alerts/{aid}")
def alert_detail(aid: int):
    c = db()
    r = c.execute(ALERT_SQL + "WHERE a.id=?", (aid,)).fetchone()
    if not r:
        raise HTTPException(404)
    sig = jloads(r["signals"], {})
    sug = jloads(r["suggestion"], {})
    fc = _factcheck(c, sig)
    out = _row_summary(r, c)
    out["threat"] = viz.threat_with_weights(jloads(r["threat"], {}) or {})  # score, factors, why, why not higher
    out.update({"reasons": jloads(r["reasons"], []), "signals": sig, "stats": jloads(r["stats"], {}),
                "suggestion": sug, "assessment": jloads(r["assessment"]), "history": jloads(r["history"], []),
                "dismiss_reason": r["dismiss_reason"], "label_source": r["label_source"],
                "summary": _summary(sig, fc, links_for_alert(c, aid) if "hybrid" in sig else None),
                "factcheck": fc})
    # one representative original post, shown as a quote under the title
    if r["rep_post_id"]:
        q = c.execute("SELECT text, translation, lang, source_type, author FROM posts WHERE id=?",
                      (r["rep_post_id"],)).fetchone()
        if q and _shorten(q["text"]) != out["title"]:
            out["quote"] = dict(q)
    # what changed since the analyst last opened this alert; then remember what they see now
    stats = out["stats"]
    snap = {"posts": stats.get("size", 0), "accounts": stats.get("accounts", 0), "severity": r["severity"],
            "signals": sorted(sig), "at": time.time(), "sim": get_setting(c, "sim_now", 0)}
    out["since_last"] = _changes_since(jloads(r["seen_snapshot"]), snap, out["history"])
    c.execute("UPDATE alerts SET seen_snapshot=? WHERE id=?", (json.dumps(snap), aid))
    c.commit()

    nid = r["narrative_id"]
    if nid is None:
        return out

    # same network: other alerts pushed by the same coordinated accounts
    out["related"], seen_in = _related(c, aid, nid, sig)

    out["timeline"] = _timeline(c, r, nid, sig, stats, out["history"])
    out["spread"] = viz.spread(c, r, nid, sig, stats, out["history"])   # per 30 min by language + splits
    out["places"] = [{"id": p, "name": viz.municipalities().label(p)} for p in viz.alert_places(c, r)]
    coord_groups = _coord_groups(sig)

    # evidence: AI-picked key posts first, then most-followed
    key = sug.get("key_evidence", {}).get("value", [])
    cols = "id, source, source_type, credibility, author, author_created, followers, ts, text, translation, lang, url, dup_group, debunk_id"
    posts = []
    if key:
        posts = c.execute(f"SELECT {cols} FROM posts WHERE id IN ({','.join('?' * len(key))})", key).fetchall()
        posts = sorted(posts, key=lambda p: key.index(p["id"]))
    more = c.execute(f"SELECT {cols} FROM posts WHERE narrative_id=? ORDER BY followers DESC LIMIT 12", (nid,)).fetchall()
    seen = {p["id"] for p in posts}
    posts += [p for p in more if p["id"] not in seen]
    out["evidence"] = [dict(p) | {"key": p["id"] in key, "copy_paste": p["dup_group"] in coord_groups,
                                  "new_account": bool(p["author_created"] and p["ts"] - p["author_created"] < 30 * 86400)}
                       for p in posts[:12]]

    # network: top accounts
    out["linked_cyber"] = links_for_alert(c, aid)
    out["accounts"] = [dict(a) | {"also_in": seen_in.get(a["author"], [])} for a in c.execute(
        "SELECT author, COUNT(*) AS posts, MAX(followers) AS followers, MIN(author_created) AS created, MIN(ts) AS first "
        "FROM posts WHERE narrative_id=? GROUP BY author ORDER BY first LIMIT 15", (nid,))]
    if r["unread"]:
        c.execute("UPDATE alerts SET unread=0 WHERE id=?", (aid,))
        c.commit()
    return out


UNDO_COLS = ("status", "assessment", "dismiss_reason", "dismissed_signals", "dismissed_posts", "unread")


class Decision(BaseModel):
    action: str                    # confirm | dismiss | review | watch | reopen
    reason: str | None = None      # dismiss reason, or review assignee
    fields: dict | None = None     # confirm: assessment fields (type, severity, + optional details)
    note: str | None = None
    duplicate_of: int | None = None


@app.post("/api/alerts/{aid}/decision")
def decide(aid: int, d: Decision):
    c = db()
    r = c.execute("SELECT * FROM alerts WHERE id=?", (aid,)).fetchone()
    if not r:
        raise HTTPException(404)
    now = time.time()
    sim = get_setting(c, "sim_now", 0)
    history = jloads(r["history"], [])
    sug = jloads(r["suggestion"], {})
    changed = []
    prev = {k: r[k] for k in UNDO_COLS}  # state before this decision, for undo

    if d.action == "confirm":
        fields = d.fields or {}
        # which AI suggestions did the analyst change? (measures how good the pre-fill is)
        for k, v in fields.items():
            if k in sug and "value" in sug[k] and v not in (None, "", []) and v != sug[k]["value"]:
                changed.append(k)
        c.execute("UPDATE alerts SET status='confirmed', assessment=?, unread=0 WHERE id=?",
                  (json.dumps({"fields": fields, "note": d.note, "at": now, "changed": changed}), aid))
        # feedback loop: the confirmed narrative becomes a known claim
        n = c.execute("SELECT label, centroid FROM narratives WHERE id=?", (r["narrative_id"],)).fetchone()
        if n and n["centroid"]:
            c.execute("INSERT OR IGNORE INTO factchecks(ext_id, source, claim, verdict, url, date, kind, embedding) "
                      "VALUES(?,?,?,?,?,?,?,?)",
                      (f"analyst-{aid}", "Analyst-confirmed case", n["label"] or "", "confirmed disinformation",
                       f"#alert-{aid}", time.strftime("%Y-%m-%d"), "analyst",
                       np.frombuffer(n["centroid"], dtype=np.float32).tobytes()))
        event = f"Confirmed ({', '.join(fields.get('type') or []) or 'unspecified'}, {fields.get('severity', '?')})"
    elif d.action == "dismiss":
        if d.reason == "duplicate" and d.duplicate_of:
            c.execute("UPDATE alerts SET status='merged', dismiss_reason='duplicate', unread=0 WHERE id=?", (aid,))
            event = f"Dismissed as duplicate of #{d.duplicate_of}"
        else:
            c.execute("UPDATE alerts SET status='dismissed', dismiss_reason=?, dismissed_signals=?, "
                      "dismissed_posts=?, unread=0 WHERE id=?",
                      (d.reason or "unspecified",
                       json.dumps({"signals": list(jloads(r["signals"], {}).keys()), "sim": sim}),
                       jloads(r["stats"], {}).get("size", 0), aid))
            event = f"Dismissed: {d.reason}"
    elif d.action == "review":
        c.execute("UPDATE alerts SET status='needs_review', unread=0 WHERE id=?", (aid,))
        event = "Marked for review" + (f" (assigned to {d.reason})" if d.reason else "")
    elif d.action == "watch":
        c.execute("UPDATE alerts SET status='watching', unread=0 WHERE id=?", (aid,))
        event = "Watching"
    elif d.action == "reopen":
        c.execute("UPDATE alerts SET status='new' WHERE id=?", (aid,))
        event = "Re-opened by analyst"
    else:
        raise HTTPException(400, "unknown action")

    if d.note:
        event += f" — note: {d.note}"
    history.append({"t": now, "sim": sim, "event": event, "by": "analyst"})
    c.execute("UPDATE alerts SET history=? WHERE id=?", (json.dumps(history), aid))
    cur = c.execute("INSERT INTO decisions(alert_id, action, reason, fields, changed_fields, note, ts, prev) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (aid, d.action, d.reason, json.dumps(d.fields), json.dumps(changed), d.note, now, json.dumps(prev)))
    c.commit()
    return {"ok": True, "changed_fields": changed, "decision_id": cur.lastrowid, "event": event}


@app.post("/api/alerts/{aid}/undo")
def undo(aid: int):
    """Revert the analyst's latest decision on this alert. The undo itself stays in the history (audit trail)."""
    c = db()
    d = c.execute("SELECT * FROM decisions WHERE alert_id=? AND undone=0 ORDER BY id DESC LIMIT 1", (aid,)).fetchone()
    r = c.execute("SELECT history FROM alerts WHERE id=?", (aid,)).fetchone()
    if not d or not r or not d["prev"]:
        raise HTTPException(400, "nothing to undo")
    prev = jloads(d["prev"], {})
    c.execute(f"UPDATE alerts SET {', '.join(f'{k}=?' for k in UNDO_COLS)} WHERE id=?",
              (*[prev.get(k) for k in UNDO_COLS], aid))
    if d["action"] == "confirm":  # take the confirmed case back out of the known-claims library
        c.execute("DELETE FROM factchecks WHERE ext_id=? AND kind='analyst'", (f"analyst-{aid}",))
    history = jloads(r["history"], [])
    undone = next((h["event"] for h in reversed(history) if h.get("by") == "analyst" and h.get("t") == d["ts"]),
                  d["action"])
    history.append({"t": time.time(), "sim": get_setting(c, "sim_now", 0), "event": f"Undone: {undone}",
                    "by": "analyst"})
    c.execute("UPDATE alerts SET history=? WHERE id=?", (json.dumps(history), aid))
    c.execute("UPDATE decisions SET undone=1 WHERE id=?", (d["id"],))
    c.commit()
    return {"ok": True, "status": prev.get("status")}


# ---------------------------------------------------------------- cyber
CYBER_FOLDERS = {"all": ("unverified", "partly_confirmed", "confirmed", "refuted"),
                 "open": ("unverified", "partly_confirmed"), "confirmed": ("confirmed",), "refuted": ("refuted",)}


def _cyber_row(r, n_links: int = 0) -> dict:
    return {"id": r["id"], "kind": r["kind"], "kind_label": KIND_LABEL.get(r["kind"], r["kind"]), "title": r["title"],
            "target": r["target"], "sector": r["sector"], "claimed_actor": r["claimed_actor"],
            "source_name": r["source_name"], "source_type": r["source_type"], "source_url": r["source_url"],
            "claimed_at": r["claimed_at"], "first_seen": r["first_seen"], "status": r["status"],
            "admiralty": f"{r['source_reliability']}{r['info_credibility']}", "confidence": r["confidence"],
            "severity": r["severity"] or "info", "score": r["score"], "verdict_at": r["verdict_at"], "linked": n_links,
            "synthetic": bool((jloads(r["raw"], {}) or {}).get("synthetic"))}


def _cyber_verdict(ev: dict, checks: list[dict]) -> str:
    """One plain-language sentence. Describes what the checks show; never says the claim is true."""
    latest: dict[str, dict] = {}
    for ch in checks:   # strongest result per check: supports > contradicts > inconclusive
        rank = {"supports": 3, "contradicts": 2, "inconclusive": 1}.get(ch["result"], 0)
        if rank > latest.get(ch["check_name"], {}).get("_r", -1):
            latest[ch["check_name"]] = dict(ch, _r=rank)
    sup = [x for x in latest.values() if x["result"] == "supports" and x["check_name"] in TECHNICAL | INDEPENDENT]
    con = [x for x in latest.values() if x["result"] == "contradicts"]
    who = re.sub(r"\s*\([^)]*\)", "", ev["claimed_actor"] or ev["source_name"] or "") or "A source"
    what = f"{who} reported: {ev['title']}." if ev["kind"] in ("outage", "vulnerability") else f"Claim by {who}."
    phrase = lambda xs: "; ".join(f"{CHECK_LABELS.get(x['check_name'], x['check_name']).lower()}: {x['detail']}" for x in xs[:2])
    if ev["status"] == "confirmed":
        return f"{what} Technical and independent checks support it ({phrase(sup)})."
    if ev["status"] == "refuted":
        return f"{what} Checks contradict it ({phrase(con)})."
    if ev["status"] == "partly_confirmed":
        missing = "an independent source" if not any(x["check_name"] in INDEPENDENT for x in sup) else "technical evidence"
        return f"{what} Partly supported ({phrase(sup)}). Still missing {missing}."
    off = [x for x in con if x["check_name"] == "kosovo_target"]
    return (f"{what} Nothing supports it yet; re-checking automatically for 48 hours."
            + (" Nothing links the target to Kosovo." if off else ""))


@app.get("/api/cyber/events")
def cyber_events(folder: str = "all", hours: float | None = None):
    """hours: only claims made in the last N hours (data time), set by the Overview counters."""
    c = db()
    st = CYBER_FOLDERS.get(folder, CYBER_FOLDERS["all"])
    now = get_setting(c, "sim_now", 0) or time.time()
    since = now - hours * 3600 if hours else 0
    links = Counter(r[0] for r in c.execute("SELECT cyber_event_id FROM hybrid_links"))
    rows = c.execute(f"SELECT * FROM cyber_events WHERE status IN ({','.join('?' * len(st))}) AND first_seen<=? "
                     "AND claimed_at>=? ORDER BY claimed_at DESC", (*st, now, since)).fetchall()
    counts = Counter(r[0] for r in c.execute("SELECT status FROM cyber_events WHERE first_seen<=? AND claimed_at>=?",
                                              (now, since)))
    return {"events": [_cyber_row(r, links[r["id"]]) for r in rows],
            "counts": {"all": sum(counts.values()), "open": counts["unverified"] + counts["partly_confirmed"],
                       "confirmed": counts["confirmed"], "refuted": counts["refuted"]}}


@app.get("/api/cyber/events/{eid}")
def cyber_event(eid: int):
    c = db()
    r = c.execute("SELECT * FROM cyber_events WHERE id=?", (eid,)).fetchone()
    if not r:
        raise HTTPException(404)
    checks = [dict(x) | {"label": CHECK_LABELS.get(x["check_name"], x["check_name"]),
                         "category": "technical" if x["check_name"] in TECHNICAL else
                                     "independent" if x["check_name"] in INDEPENDENT else "context"}
              for x in c.execute("SELECT * FROM verification_checks WHERE event_id=? ORDER BY checked_at, id", (eid,))]
    links = [dict(x) for x in c.execute("SELECT alert_id, reason, score FROM hybrid_links WHERE cyber_event_id=?", (eid,))]
    alerts = []
    for h in links:
        a = c.execute(ALERT_SQL + "WHERE a.id=?", (h["alert_id"],)).fetchone()
        if a:
            alerts.append({"id": a["id"], "title": _title(c, a, jloads(a["signals"], {}))[0], "severity": a["severity"],
                           "type": a["type"], "status": a["status"], "reason": h["reason"], "score": h["score"]})
    out = _cyber_row(r, len(links))
    raw = jloads(r["raw"], {}) or {}
    # sources: the claim itself, then every distinct piece of evidence the checks point to
    sources = [{"role": "Claim", "name": r["source_name"], "type": r["source_type"], "url": r["source_url"],
                "reliability": r["source_reliability"]}]
    seen = {r["source_url"]}
    for ch in checks:
        if ch["evidence_url"] and ch["evidence_url"] not in seen:
            seen.add(ch["evidence_url"])
            sources.append({"role": ch["label"], "name": None, "type": ch["category"], "url": ch["evidence_url"]})
    out["threat"] = viz.threat_with_weights(jloads(r["threat"], {}) or {})
    out["reality"] = viz.reality(c, r, checks)
    out.update({"checks": checks, "alerts": alerts, "sources": sources, "summary": _cyber_verdict(out, checks),
                "recheck_until": r["first_seen"] + CFG.get("cyber", {}).get("recheck_hours", 48) * 3600,
                "details": {k: v for k, v in raw.items() if k in ("domain", "url", "asn", "cve", "vendor", "product",
                                                                 "country", "description")}})
    return out


# ---------------------------------------------------------------- events & situation picture
EV_LABEL = {"security_incident": "Security incident", "organized_activity": "Organized activity",
            "infrastructure": "Infrastructure disruption", "regional": "Regional development",
            "disinformation": "Disinformation", "cyber": "Cyber incident"}
EV_ORDER = list(EV_LABEL)
CRED_TEXT = {1: "confirmed by independent sources, including an official statement", 2: "reported by several independent credible sources",
             3: "reported by one credible source", 4: "only social media so far, many accounts", 5: "only a few social media accounts"}
_GAZ = None


def _gaz():
    global _GAZ
    if _GAZ is None:
        from .events import Gazetteer
        _GAZ = Gazetteer(CFG.get("gazetteer") or [])
    return _GAZ


def _hm(ts) -> str:
    return time.strftime("%d %b %H:%M", time.gmtime(ts)) if ts else "—"


def _now(c) -> float:
    return get_setting(c, "sim_now", 0) or time.time()


def _event_row(e) -> dict:
    return {"id": f"e{e['id']}", "kind": "event", "category": e["category"], "label": EV_LABEL.get(e["category"]),
            "title": e["title"], "place": e["place"], "lat": e["lat"], "lon": e["lon"], "first_ts": e["first_ts"],
            "last_ts": e["last_ts"], "severity": e["severity"], "score": e["score"], "status": e["status"], "confidence": e["confidence"],
            "admiralty": f"{e['reliability']}{e['credibility']}" if e["credibility"] else None,
            "n_posts": e["n_posts"], "n_sources": e["n_sources"], "n_credible": e["n_credible"],
            "hybrid": "hybrid threat" in (e["indicators"] or "")}


def _cyber_as_event(r, linked: bool = False) -> dict:
    """Cyber events join the situation picture as a category; place from the target / title when we know it."""
    from .text import normalize as _n
    n = _n(f"{r['title']} {r['target']}")
    places = [p for p in _gaz().find(n, set(n.split())) if not _gaz().by_name[p].get("region")]
    p = _gaz().by_name.get(places[0]) if places else None
    return {"id": f"c{r['id']}", "kind": "cyber", "category": "cyber", "label": EV_LABEL["cyber"],
            "title": r["title"], "place": p["name"] if p else None, "lat": p["lat"] if p else None,
            "lon": p["lon"] if p else None, "first_ts": r["claimed_at"], "last_ts": r["verdict_at"] or r["claimed_at"],
            "severity": r["severity"] or "info", "score": r["score"], "status": r["status"], "confidence": r["confidence"],
            "admiralty": f"{r['source_reliability']}{r['info_credibility']}", "n_posts": None, "n_sources": None,
            "n_credible": None, "hybrid": linked}


def _all_events(c, since: float) -> list[dict]:
    now = _now(c)
    linked = {r[0] for r in c.execute("SELECT cyber_event_id FROM hybrid_links")}
    evs = [_event_row(e) for e in c.execute("SELECT * FROM events WHERE last_ts>=? AND first_ts<=?", (since, now))]
    evs += [_cyber_as_event(r, r["id"] in linked) for r in c.execute("SELECT * FROM cyber_events WHERE claimed_at>=? AND first_seen<=? "
                                                  "AND status!='refuted' AND kind!='vulnerability'", (since, now))]
    rank = {"developing": 2, "ongoing": 1, "quiet": 0, "confirmed": 1, "partly_confirmed": 1, "unverified": 0}
    evs.sort(key=lambda e: (-SEV_RANK.get(e["severity"], 0), -(e.get("score") or 0), -rank.get(e["status"], 0),
                            -(e["last_ts"] or 0)))
    return evs


@app.get("/api/events")
def list_events(category: str = "all", hours: float = 168):
    c = db()
    evs = _all_events(c, _now(c) - hours * 3600)
    counts = Counter(e["category"] for e in evs)
    if category != "all":
        evs = [e for e in evs if e["category"] == category]
    return {"events": evs, "counts": {"all": sum(counts.values()), **counts}}


@app.get("/api/situation")
def situation(hours: float = 24):
    c = db()
    now = _now(c)
    since = now - hours * 3600
    evs = _all_events(c, since)
    by_cat = defaultdict(list)
    for e in evs:
        by_cat[e["category"]].append(e)
    tiles = [{"category": k, "label": EV_LABEL[k], "count": len(by_cat[k]),
              "top": by_cat[k][0]["severity"] if by_cat[k] else None,
              "critical": sum(e["severity"] == "critical" for e in by_cat[k]),
              "title": by_cat[k][0]["title"] if by_cat[k] else None} for k in EV_ORDER]
    posts = c.execute("SELECT COUNT(*), COUNT(DISTINCT source) FROM posts WHERE ts>=? AND ts<=?", (since, now)).fetchone()
    return {"now": now, "hours": hours, "tiles": tiles, "priority": evs[:8],
            "points": [e for e in evs if e["lat"] is not None],
            "lanes": {k: [{"id": e["id"], "t0": e["first_ts"], "t1": e["last_ts"], "severity": e["severity"],
                           "title": e["title"]} for e in by_cat[k]] for k in EV_ORDER},
            "kpis": {"events": len(evs), "critical": sum(e["severity"] == "critical" for e in evs),
                     "threat": sum(e["severity"] == "threat" for e in evs), "hybrid": sum(e["hybrid"] for e in evs),
                     "posts": posts[0], "sources": posts[1]},
            "brief": _brief(evs, by_cat, now, hours)}


def _brief(evs, by_cat, now, hours) -> list[str]:
    """Situation brief: short, factual, every line traceable to an event in the list."""
    if not evs:
        return [f"No threat events in the last {hours:g} hours (to {_hm(now)}). Monitoring continues."]
    crit = [e for e in evs if e["severity"] in ("critical", "threat")]
    out = [f"Situation to {_hm(now)} (last {hours:g} h): {len(evs)} events, {len(crit)} of them at Threat or Critical level."]
    top = evs[0]
    out.append(f"Highest priority: {top['title'].rstrip('. ')} ({top['label'].lower()}{', ' + top['place'] if top['place'] else ''}, "
               f"{top['severity']}).")
    phrase = {"security_incident": "Security", "organized_activity": "Organized activity", "infrastructure": "Infrastructure",
              "regional": "Regional", "disinformation": "Information space", "cyber": "Cyber"}
    for k in EV_ORDER:
        items = by_cat.get(k) or []
        if not items:
            continue
        names = "; ".join(f"{e['title'].rstrip('. ')}{' (' + e['place'] + ')' if e['place'] else ''}" for e in items[:2])
        more = f" and {len(items) - 2} more" if len(items) > 2 else ""
        extra = ""
        if k == "disinformation":
            hy = sum(e["hybrid"] for e in items)
            extra = f" {hy} linked to a cyber incident (hybrid threat)." if hy else ""
        out.append(f"{phrase[k]}: {len(items)} — {names}{more}.{extra}")
    return out


@app.get("/api/events/{eid}")
def event_detail(eid: int):
    c = db()
    e = c.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
    if not e:
        raise HTTPException(404)
    out = _event_row(e)
    pids = jloads(e["post_ids"], []) or []
    ps = []
    for i in range(0, len(pids), 500):
        chunk = pids[i:i + 500]
        ps += c.execute(f"SELECT id, ts, source, source_type, credibility, author, text, translation, lang, url, followers "
                        f"FROM posts WHERE id IN ({','.join('?' * len(chunk))})", chunk).fetchall()
    ps.sort(key=lambda p: p["ts"])
    rel_of = lambda p: ("B" if p["source_type"] in ("official", "cert") or p["credibility"] == "credible"
                        else "E" if p["credibility"] == "low" else "F")
    # sources: one row per source, most reliable first, with its first post (traceable)
    src = {}
    for p in ps:
        s = src.setdefault(p["source"], {"source": p["author"] or p["source"], "type": p["source_type"], "reliability": rel_of(p),
                                         "first_ts": p["ts"], "posts": 0, "url": p["url"]})
        s["posts"] += 1
    sources = sorted(src.values(), key=lambda s: (s["reliability"], s["first_ts"]))
    first, first_cred, first_off = ps[0] if ps else None, e["first_credible_ts"], e["first_official_ts"]
    key_posts = ([p for p in ps if p["source_type"] in ("official", "cert")][:1]
                 + [p for p in ps if p["credibility"] == "credible" and p["source_type"] == "news"][:3]
                 + sorted([p for p in ps if p["credibility"] != "credible"], key=lambda p: -(p["followers"] or 0))[:3])
    alert_ids = jloads(e["alert_ids"], []) or []

    # timeline: posts per bucket over the event's life (min 6 h), with labelled markers
    t0, t1 = e["first_ts"], max(e["last_ts"], e["first_ts"] + 6 * 3600)
    bucket = max(1800, int((t1 - t0) / 48 // 1800 + 1) * 1800)
    bars = Counter(int(p["ts"] // bucket * bucket) for p in ps)
    markers = [{"label": "First mention", "t": first["ts"], "kind": "first"}] if first else []
    if first_cred:
        markers.append({"label": "First credible report", "t": first_cred, "kind": "credible"})
    if first_off:
        markers.append({"label": "Official statement", "t": first_off, "kind": "official"})
    alerts = []
    for aid in alert_ids:
        a = c.execute(ALERT_SQL + "WHERE a.id=?", (aid,)).fetchone()
        if a:
            sig = jloads(a["signals"], {})
            alerts.append({"id": a["id"], "title": _title(c, a, sig)[0], "severity": a["severity"], "type": a["type"],
                           "summary": _summary_base(sig, _factcheck(c, sig))})
            markers.append({"label": f"Alert #{a['id']}", "t": a["created_sim"], "kind": "alert"})
    # context: linked cyber events (hybrid links of our alerts, or same place within 24 h)
    cyber, seen = [], set()
    for aid in alert_ids:
        for l in links_for_alert(c, aid):
            if l["event_id"] not in seen:
                seen.add(l["event_id"])
                cyber.append({"id": l["event_id"], "title": l["title"], "status": l["status"], "kind": l["kind"],
                              "admiralty": l["admiralty"], "reason": l["reason"], "t": l["claimed_at"]})
    if e["place"]:
        for r in c.execute("SELECT * FROM cyber_events WHERE status!='refuted' AND claimed_at BETWEEN ? AND ?",
                           (e["first_ts"] - 86400, e["last_ts"] + 86400)):
            ce = _cyber_as_event(r)
            if ce["place"] == e["place"] and r["id"] not in seen:
                seen.add(r["id"])
                cyber.append({"id": r["id"], "title": r["title"], "status": r["status"], "kind": r["kind"],
                              "admiralty": ce["admiralty"], "reason": f"Same place ({e['place']}), within 24 hours",
                              "t": r["claimed_at"]})
    for cy in cyber:
        if e["first_ts"] - 86400 <= cy["t"] <= t1 + 86400:
            markers.append({"label": f"Cyber #{cy['id']}", "t": cy["t"], "kind": "cyber"})
    # related events: same place within 48 h, or same narrative
    nids = set(jloads(e["narrative_ids"], []) or [])
    related = []
    for o in c.execute("SELECT * FROM events WHERE id!=? AND last_ts>=? AND first_ts<=?",
                       (eid, e["first_ts"] - 2 * 86400, e["last_ts"] + 2 * 86400)):
        why = []
        if nids & set(jloads(o["narrative_ids"], []) or []):
            why.append("same story")
        if e["place"] and o["place"] == e["place"]:
            why.append(f"same place ({e['place']})")
        if why:
            related.append(_event_row(o) | {"why": ", ".join(why)})

    lo = min([m["t"] for m in markers] + [t0])
    hi = max([m["t"] for m in markers] + [t1])
    out.update({
        "threat": viz.threat_with_weights(jloads(e["threat"], {}) or {}),
        "indicators": jloads(e["indicators"], []), "langs": jloads(e["langs"], {}), "n_official": e["n_official"],
        "first_credible_ts": first_cred, "first_official_ts": first_off, "reliability": e["reliability"],
        "credibility": e["credibility"], "part": e["part"],
        "summary": _event_summary(e, first, sources, alerts, cyber),
        "sources": sources[:40], "n_sources_total": len(sources),
        "key_posts": [dict(p) for p in key_posts],
        "timeline": {"start": lo // bucket * bucket, "end": hi + bucket, "bucket": bucket,
                     "bars": [{"t": t, "posts": n} for t, n in sorted(bars.items())], "markers": markers},
        "alerts": alerts, "cyber": cyber, "related": related,
    })
    return out


def _event_summary(e, first, sources, alerts, cyber) -> str:
    """What we know, in plain language. Reports who says what; never states that a claim is true."""
    where = f" in {e['place']}" if e["place"] else ""
    if e["category"] == "disinformation":
        a = alerts[0] if alerts else None
        s = f"Information threat{where}: “{e['title']}”. " + (a["summary"] if a else "")
        if cyber:
            s += f" It is linked to cyber event #{cyber[0]['id']} ({cyber[0]['title']}): a hybrid threat."
        return s.strip()
    lead = ""
    if first and e["first_credible_ts"] and e["first_credible_ts"] > first["ts"] + 300:
        m = int((e["first_credible_ts"] - first["ts"]) / 60)
        lead = f" Social media carried it {m // 60}h {m % 60:02d}m before the first credible report." if m >= 60 else \
               f" Social media carried it {m} min before the first credible report."
    cred = CRED_TEXT.get(e["credibility"], "")
    s = (f"{EV_LABEL[e['category']]}{where}, first mentioned {_hm(e['first_ts'])}. "
         f"{e['n_sources']} sources ({e['n_credible']} credible outlet(s){', an official statement' if e['n_official'] else ''}), "
         f"{e['n_posts']} posts.{lead} Assessment: {cred} (Admiralty {e['reliability']}{e['credibility']}).")
    if cyber:
        s += f" Related cyber event: {cyber[0]['title']} ({cyber[0]['status'].replace('_', ' ')})."
    return s


# ---------------------------------------------------------------- settings
@app.get("/api/settings")
def get_settings():
    c = db()
    out = {k: get_setting(c, k, CFG.get(k)) for k in ("sources", "focus", "factcheckers")}
    out["cyber_sources"] = get_setting(c, "cyber_sources", (CFG.get("cyber") or {}).get("sources", []))
    return out


class SettingsIn(BaseModel):
    sources: list | None = None
    focus: dict | None = None
    factcheckers: list | None = None
    cyber_sources: list | None = None


@app.put("/api/settings")
def put_settings(s: SettingsIn):
    c = db()
    for k, v in s.model_dump().items():
        if v is not None:
            set_setting(c, k, v)
    return {"ok": True}


# ---------------------------------------------------------------- activity
@app.get("/api/activity")
def activity():
    c = db()
    buckets = [dict(r) for r in c.execute(
        "SELECT CAST(ts/3600 AS INTEGER)*3600 AS t, COUNT(*) AS posts FROM posts GROUP BY t ORDER BY t")]
    alerts = [{"id": r["id"], "t": r["created_sim"], "type": r["type"], "severity": r["severity"],
               "title": _title(c, r, jloads(r["signals"], {}))[0]}
              for r in c.execute(ALERT_SQL + "WHERE a.status!='merged' ORDER BY a.created_sim")]
    m = c.execute("SELECT SUM(n_posts) AS n, SUM(seconds) AS s, MAX(n_posts*1.0/seconds) AS peak FROM metrics "
                  "WHERE seconds>0").fetchone()
    dec = c.execute("SELECT changed_fields FROM decisions WHERE action='confirm' AND undone=0").fetchall()
    changed = [f for d in dec for f in jloads(d["changed_fields"], [])]
    return {"buckets": buckets, "alerts": alerts,
            "throughput": {"posts": m["n"] or 0, "avg_per_sec": (m["n"] / m["s"]) if m["s"] else 0,
                           "peak_per_sec": m["peak"] or 0},
            "ai_prefill": {"confirmed": len(dec), "fields_changed": len(changed)}}


# ---------------------------------------------------------------- visual layer (overview, charts)
# imported last: viz uses the helpers above
from . import viz  # noqa: E402

app.include_router(viz.router)
